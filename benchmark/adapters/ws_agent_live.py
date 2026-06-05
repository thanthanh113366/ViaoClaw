"""Live B3 via Docker AgentRuntime: WebSocket text dispatch + log parsing."""

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from benchmark.adapters.chat_engine_live import LiveTurnResult, score_live_turn
from benchmark.adapters.feed_audio import build_ws_url
from benchmark.adapters.yaml_config import read_yaml
from benchmark.config import SERVER_ROOT

DEFAULT_DOCKER_CONTAINER = "xiaozhi-esp32-server"

_CHAT_ENGINE_TOOL_RE = re.compile(
    r"function_name=(?P<name>[^,\s]+),\s*function_id=[^,]+,\s*function_arguments=(?P<args>.+)\s*$"
)
_HANDLER_TOOL_RE = re.compile(
    r"调用函数:\s*(?P<name>\w+),\s*参数:\s*(?P<args>\{.*\})\s*$"
)
_MANAGER_TOOL_RE = re.compile(
    r"执行工具:\s*(?P<name>\w+)，参数:\s*(?P<args>.+)\s*$"
)


class LogCapture(Protocol):
    source_label: str

    def mark(self) -> None: ...

    def read_delta_lines(self) -> list[str]: ...


@dataclass
class FileLogTailer:
    path: Path
    offset: int = 0
    source_label: str = field(init=False)

    def __post_init__(self) -> None:
        self.source_label = f"file:{self.path}"

    def mark(self) -> None:
        if not self.path.is_file():
            self.offset = 0
            return
        self.offset = self.path.stat().st_size

    def read_delta_lines(self) -> list[str]:
        if not self.path.is_file():
            return []
        with self.path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(self.offset)
            chunk = handle.read()
            self.offset = handle.tell()
        if not chunk:
            return []
        return chunk.splitlines()


@dataclass
class DockerLogsTailer:
    container: str
    since_unix: int = 0
    source_label: str = field(init=False)

    def __post_init__(self) -> None:
        self.source_label = f"docker-logs:{self.container}"

    def mark(self) -> None:
        self.since_unix = int(time.time()) - 1

    def read_delta_lines(self) -> list[str]:
        if self.since_unix <= 0:
            return []
        try:
            proc = subprocess.run(
                ["docker", "logs", self.container, "--since", str(self.since_unix)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [f"__benchmark_log_error__:{exc}"]
        combined = (proc.stdout or "") + (proc.stderr or "")
        return combined.splitlines()


def _candidate_log_paths(bench_config: dict[str, Any]) -> list[Path]:
    b3 = bench_config.get("b3") or {}
    raw = b3.get("server_log") or os.environ.get("BENCHMARK_SERVER_LOG")
    if raw:
        return [Path(raw)]
    return [
        SERVER_ROOT / "tmp" / "server.log",
        SERVER_ROOT / "data" / "server.log",
    ]


def _docker_container_name(bench_config: dict[str, Any]) -> str:
    b3 = bench_config.get("b3") or {}
    return str(
        b3.get("docker_container")
        or os.environ.get("BENCHMARK_DOCKER_CONTAINER")
        or DEFAULT_DOCKER_CONTAINER
    )


def _docker_container_running(name: str) -> bool:
    try:
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and (proc.stdout or "").strip().lower() == "true"


def resolve_log_capture(bench_config: dict[str, Any]) -> LogCapture:
    for path in _candidate_log_paths(bench_config):
        if path.is_file():
            return FileLogTailer(path)

    container = _docker_container_name(bench_config)
    if _docker_container_running(container):
        return DockerLogsTailer(container=container)

    return FileLogTailer(_candidate_log_paths(bench_config)[0])


def _parse_arguments(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return dict(parsed) if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    try:
        parsed = ast.literal_eval(text)
        return dict(parsed) if isinstance(parsed, dict) else {}
    except (SyntaxError, ValueError):
        return {}


def parse_tool_dispatches_from_lines(lines: list[str]) -> list[dict[str, Any]]:
    """Extract tool calls from server logs (INFO unified_tool_manager / DEBUG chat_engine)."""
    dispatches: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for line in lines:
        if line.startswith("__benchmark_log_error__:"):
            continue
        match = (
            _MANAGER_TOOL_RE.search(line)
            or _CHAT_ENGINE_TOOL_RE.search(line)
            or _HANDLER_TOOL_RE.search(line)
        )
        if not match:
            continue
        name = str(match.group("name") or "").strip()
        if not name:
            continue
        args = _parse_arguments(match.group("args"))
        key = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        dispatches.append({"function": name, "arguments": args})

    return dispatches


def filter_log_lines_for_turn(
    lines: list[str],
    *,
    utterance: str,
    device_id: str,
) -> list[str]:
    """Keep only docker log lines belonging to this WS turn (avoid older tool calls)."""
    start = 0
    for index, line in enumerate(lines):
        if device_id not in line:
            continue
        if "run_turn" in line and utterance in line:
            start = index
            break
        if "大模型收到用户消息" in line and utterance in line:
            start = index
            break

    scoped = lines[start:]
    end = len(scoped)
    for offset, line in enumerate(scoped[1:], start=1):
        if device_id in line and "[cron] unregistered device_id=" in line:
            end = offset + 1
            break
    return scoped[:end]


def _summarize_turn_logs(lines: list[str]) -> str:
    for line in lines:
        if "执行工具:" in line:
            return line.strip()[-200:]
    for line in lines:
        if "发送第一段语音:" in line:
            return f"TTS text-only response: {line.split('发送第一段语音:')[-1].strip()}"
        if "语音生成成功:" in line:
            return f"TTS text-only response: {line.split('语音生成成功:')[-1].strip()}"
    return f"no tool/TTS markers in {len(lines)} scoped log lines"


def _docker_preflight(capture: LogCapture, bench_config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if isinstance(capture, FileLogTailer) and not capture.path.is_file():
        container = _docker_container_name(bench_config)
        errors.append(
            f"No readable server log on host ({capture.path}). "
            f"Expected tmp/server.log inside container or set BENCHMARK_SERVER_LOG. "
            f"Ensure Docker container '{container}' is running."
        )
    return errors


def _fetch_docker_logs(container: str, since_unix: int) -> list[str]:
    try:
        proc = subprocess.run(
            ["docker", "logs", container, "--since", str(since_unix)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return ((proc.stdout or "") + (proc.stderr or "")).splitlines()


def _ws_bind_ready(line: str, device_id: str) -> bool:
    return device_id in line and "[xiaoclaw.session] bind" in line


async def _wait_for_ws_conn_ready(
    bench_config: dict[str, Any],
    *,
    device_id: str,
    since_unix: int,
    timeout: float,
) -> tuple[bool, str]:
    """Wait until ConnectionHandler finishes lazy init and binds agent session.

    WS benchmark used to send listen/detect immediately after hello, racing
    _background_initialize() — LLM saw an empty prompt without HA device list.
    """
    container = _docker_container_name(bench_config)
    fallback_wait = float(
        os.environ.get(
            "BENCHMARK_WS_INIT_WAIT_SECONDS",
            str((bench_config.get("b3") or {}).get("ws_init_wait_seconds") or "5"),
        )
    )

    if not _docker_container_running(container):
        await asyncio.sleep(fallback_wait)
        return True, f"fixed wait {fallback_wait:.1f}s (docker logs unavailable)"

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for line in _fetch_docker_logs(container, since_unix):
            if _ws_bind_ready(line, device_id):
                await asyncio.sleep(0.2)
                return True, "agent session bind ready (prompt + HA devices loaded)"
        await asyncio.sleep(0.25)

    await asyncio.sleep(fallback_wait)
    return False, (
        f"timeout after {timeout:.0f}s waiting for [xiaoclaw.session] bind "
        f"device_id={device_id!r}; ConnectionHandler init may still be running"
    )


def _ws_conn_ready_timeout(bench_config: dict[str, Any]) -> float:
    b3 = bench_config.get("b3") or {}
    raw = os.environ.get("BENCHMARK_WS_CONN_READY_TIMEOUT") or b3.get(
        "ws_conn_ready_timeout"
    )
    return float(raw or "30")


async def _wait_for_hello(ws: Any, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        message = await asyncio.wait_for(ws.recv(), timeout=max(remaining, 0.1))
        if isinstance(message, bytes):
            continue
        payload = json.loads(message)
        if payload.get("type") == "hello":
            return
    raise TimeoutError("Timed out waiting for hello response")


async def _recv_until_tts_stop(ws: Any, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        message = await asyncio.wait_for(ws.recv(), timeout=max(remaining, 0.1))
        if isinstance(message, bytes):
            continue
        payload = json.loads(message)
        if payload.get("type") == "tts" and payload.get("state") == "stop":
            return
    raise TimeoutError(f"Timed out waiting for tts stop after {timeout:.0f}s")


async def dispatch_text_via_ws(
    bench_config: dict[str, Any],
    *,
    utterance: str,
    device_id: str,
    client_id: str,
    trial_timeout: float,
    log_capture: LogCapture | None = None,
) -> LiveTurnResult:
    try:
        import websockets
    except ImportError as exc:
        return LiveTurnResult(
            session_key=f"xiaozhi:{device_id}",
            channel="xiaozhi",
            utterance=utterance,
            errors=[f"websockets not installed: {exc}"],
            path="docker-live: WS listen/detect -> Docker AgentRuntime",
        )

    ws_url = bench_config.get("env", {}).get("viaoclaw_ws_url") or bench_config.get(
        "discovery", {}
    ).get("websocket_url", "ws://127.0.0.1:8000/xiaozhi/v1/")
    b1 = bench_config.get("b1") or {}
    authorization = b1.get("authorization")
    device_mac = str(b1.get("device_mac") or "11:22:33:44:55:66")
    connect_url = build_ws_url(
        ws_url,
        device_id=device_id,
        client_id=client_id,
        authorization=authorization,
    )
    headers = {"Device-Id": device_id, "Client-Id": client_id}
    if authorization:
        token = authorization if str(authorization).startswith("Bearer ") else f"Bearer {authorization}"
        headers["Authorization"] = token

    capture = log_capture or resolve_log_capture(bench_config)
    init_timeout = _ws_conn_ready_timeout(bench_config)
    path_label = f"docker-live: WS listen/detect -> Docker AgentRuntime ({capture.source_label})"

    try:
        async with websockets.connect(connect_url, additional_headers=headers) as ws:
            hello_since = int(time.time()) - 1
            await ws.send(
                json.dumps(
                    {
                        "type": "hello",
                        "device_id": device_id,
                        "device_name": client_id,
                        "device_mac": device_mac,
                        "token": authorization or "",
                        "features": {},
                    }
                )
            )
            await _wait_for_hello(ws, timeout=min(10.0, trial_timeout))

            ready, ready_detail = await _wait_for_ws_conn_ready(
                bench_config,
                device_id=device_id,
                since_unix=hello_since,
                timeout=init_timeout,
            )
            if not ready:
                return LiveTurnResult(
                    session_key=f"xiaozhi:{device_id}",
                    channel="xiaozhi",
                    utterance=utterance,
                    errors=[f"WS conn init: {ready_detail}"],
                    path=path_label,
                )

            capture.mark()
            await ws.send(
                json.dumps(
                    {
                        "type": "listen",
                        "state": "detect",
                        "mode": "manual",
                        "text": utterance,
                    }
                )
            )
            await _recv_until_tts_stop(ws, timeout=trial_timeout)
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        return LiveTurnResult(
            session_key=f"xiaozhi:{device_id}",
            channel="xiaozhi",
            utterance=utterance,
            errors=[f"WS dispatch failed: {detail}"],
            path=path_label,
        )

    await asyncio.sleep(0.5)
    raw_lines = capture.read_delta_lines()
    turn_lines = filter_log_lines_for_turn(
        raw_lines, utterance=utterance, device_id=device_id
    )
    dispatches = parse_tool_dispatches_from_lines(turn_lines)
    last = dispatches[-1] if dispatches else {}
    errors: list[str] = []
    if not dispatches:
        summary = _summarize_turn_logs(turn_lines)
        errors.append(
            "No tool calls in Docker logs for this turn. "
            f"Observed: {summary}"
        )
    return LiveTurnResult(
        session_key=f"xiaozhi:{device_id}",
        channel="xiaozhi",
        utterance=utterance,
        function=last.get("function"),
        args=dict(last.get("arguments") or {}),
        dispatches=dispatches,
        errors=errors,
        path=path_label,
    )


def evaluate_docker_scenarios(
    scenarios: list[dict[str, Any]],
    bench_config: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    capture = resolve_log_capture(bench_config)
    preflight = _docker_preflight(capture, bench_config)
    if preflight and not isinstance(capture, DockerLogsTailer):
        return [], {"path": "docker-live: WS + logs", "log_source": capture.source_label}, preflight

    b1 = bench_config.get("b1") or {}
    base_device = str(b1.get("device_id") or "bench-device-001")
    trial_timeout = float(os.environ.get("BENCHMARK_B3_TIMEOUT", "90"))
    llm_module = None
    llm_model = None
    config_path = SERVER_ROOT / "data" / ".config.yaml"
    if config_path.is_file():
        try:
            cfg = read_yaml(config_path)
            llm_module = (cfg.get("selected_module") or {}).get("LLM")
            if llm_module:
                llm_model = (cfg.get("LLM") or {}).get(llm_module, {}).get("model_name")
        except Exception:
            pass

    details: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, item in enumerate(scenarios, start=1):
        device_id = f"{base_device}-fc-{item['id']}"
        client_id = f"benchmark-b3-{item['id']}"
        print(
            f"[B3/docker] scenario {index}/{len(scenarios)} {item['id']} via WS",
            file=sys.stderr,
            flush=True,
        )
        turn = asyncio.run(
            dispatch_text_via_ws(
                bench_config,
                utterance=item["user_utterance"],
                device_id=device_id,
                client_id=client_id,
                trial_timeout=trial_timeout,
                log_capture=capture,
            )
        )
        scored = score_live_turn(item, turn)
        details.append({k: v for k, v in scored.items() if k != "errors"})
        errors.extend(scored.get("errors") or [])

    meta = {
        "path": "docker-live: WS listen/detect -> Docker AgentRuntime (log parse)",
        "config_source": "data/.config.yaml (Docker-mounted server config)",
        "log_source": capture.source_label,
        "llm_module": llm_module,
        "llm_model": llm_model,
        "b3_target": "docker",
    }
    return details, meta, errors
