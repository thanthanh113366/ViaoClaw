"""Live ChatEngine / AgentRuntime harness for benchmark integration tests."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmark.adapters.yaml_config import read_yaml
from benchmark.config import SERVER_ROOT, ensure_benchmark_import_stubs
from benchmark.stubs.recording import DispatchRecorder, FakeBenchConn


@dataclass
class LiveTurnResult:
    session_key: str
    channel: str
    utterance: str
    function: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    dispatches: list[dict[str, Any]] = field(default_factory=list)
    memory_ops: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    path: str = ""


class BenchNullOutbound:
    """Minimal OutboundSink for live dispatch without TTS/Telegram I/O."""

    def on_first(self, _sentence_id: str) -> None:
        pass

    def on_chunk(self, _sentence_id: str, _text: str) -> None:
        pass

    def on_last(self, _sentence_id: str) -> None:
        pass


def load_runtime_config(api_key: str, *, enable_telegram: bool = False) -> tuple[dict | None, str]:
    """Load production config for live LLM calls.

    Uses data/.config.yaml only (same file Docker mounts). The stock config.yaml
    template is not parsed — it contains placeholder colons that break PyYAML.
    """
    ensure_benchmark_import_stubs()

    bench_path = os.environ.get("BENCHMARK_CONFIG_PATH")
    if bench_path and Path(bench_path).is_file():
        config = read_yaml(bench_path)
        source = f"BENCHMARK_CONFIG_PATH ({bench_path})"
    else:
        custom_path = SERVER_ROOT / "data" / ".config.yaml"
        if not custom_path.is_file():
            return None, f"Missing production config: {custom_path}"
        config = read_yaml(custom_path)
        source = "data/.config.yaml (Docker-mounted server config)"

    config.setdefault("xiaoclaw", {})
    config["xiaoclaw"].setdefault("agent", {})["enabled"] = True
    tg_cfg = config["xiaoclaw"].setdefault("telegram", {})
    if enable_telegram:
        tg_cfg["enabled"] = True
        if os.environ.get("TELEGRAM_BOT_TOKEN"):
            tg_cfg["bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
        allowed = tg_cfg.get("allowed_chat_ids") or [12345]
        tg_cfg["allowed_chat_ids"] = allowed
    else:
        tg_cfg["enabled"] = False

    selected = config.setdefault("selected_module", {})
    llm_module = selected.get("LLM") or "ChatGLMLLM"
    selected["LLM"] = llm_module
    selected["Intent"] = selected.get("Intent") or "function_call"
    config.setdefault("LLM", {}).setdefault(llm_module, {})
    config["LLM"][llm_module]["api_key"] = api_key
    if os.environ.get("BENCHMARK_LLM_MODEL"):
        config["LLM"][llm_module]["model_name"] = os.environ["BENCHMARK_LLM_MODEL"]
    if os.environ.get("BENCHMARK_LLM_BASE_URL"):
        config["LLM"][llm_module]["base_url"] = os.environ["BENCHMARK_LLM_BASE_URL"]

    return config, source


@contextlib.contextmanager
def _recording_tool_handler(recorder: DispatchRecorder):
    from core.providers.tools.unified_tool_handler import UnifiedToolHandler

    original = UnifiedToolHandler.handle_llm_function_call

    async def wrapped(self, conn, tool_call_data):  # noqa: ANN001
        name = str(tool_call_data.get("name") or "")
        raw_args = tool_call_data.get("arguments") or "{}"
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                args = {}
        else:
            args = dict(raw_args)
        recorder.record(name, args)
        return await original(self, conn, tool_call_data)

    UnifiedToolHandler.handle_llm_function_call = wrapped
    try:
        yield
    finally:
        UnifiedToolHandler.handle_llm_function_call = original


class LiveRuntimeHarness:
    """Start AgentRuntime on a background loop and dispatch live turns."""

    def __init__(self, bench_config: dict[str, Any], *, enable_telegram: bool = False) -> None:
        self.bench_config = bench_config
        self.enable_telegram = enable_telegram
        self.recorder = DispatchRecorder()
        self.runtime = None
        self.config: dict[str, Any] | None = None
        self.config_source = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._start_error: str | None = None

    def start(self) -> list[str]:
        api_key = self.bench_config.get("env", {}).get("llm_api_key")
        if not api_key:
            return ["LIVE mode requires LLM_API_KEY"]

        self.config, self.config_source = load_runtime_config(
            api_key, enable_telegram=self.enable_telegram
        )
        if self.config is None:
            return [f"Cannot load runtime config: {self.config_source}"]

        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=180):
            return ["AgentRuntime.start() timed out after 180s"]
        if self._start_error:
            return [f"AgentRuntime.start() failed: {self._start_error}"]
        return []

    def stop(self) -> None:
        if self._loop is None or self.runtime is None:
            return
        try:
            if self._loop.is_running():
                future = asyncio.run_coroutine_threadsafe(self._async_stop(), self._loop)
                future.result(timeout=30)
                self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        previous_cwd = os.getcwd()
        try:
            os.chdir(SERVER_ROOT)
            self._loop.run_until_complete(self._async_start())
            self._loop.run_forever()
        except Exception as exc:
            self._start_error = str(exc)
            if not self._ready.is_set():
                self._ready.set()
        finally:
            os.chdir(previous_cwd)
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    async def _async_start(self) -> None:
        ensure_benchmark_import_stubs()
        from core.agent.runtime import AgentRuntime

        with _recording_tool_handler(self.recorder):
            self.runtime = AgentRuntime(self.config)
            await self.runtime.start()
        self._ready.set()

    async def _async_stop(self) -> None:
        if self.runtime is not None:
            await self.runtime.stop()
            self.runtime = None

    def run_turn(
        self,
        *,
        session_key: str,
        utterance: str,
        channel: str,
        conn: FakeBenchConn | None = None,
        chat_id: str | None = None,
        path_label: str,
    ) -> LiveTurnResult:
        if self._loop is None or self.runtime is None:
            return LiveTurnResult(
                session_key=session_key,
                channel=channel,
                utterance=utterance,
                errors=["LiveRuntimeHarness not started"],
                path=path_label,
            )

        self.recorder.dispatches.clear()
        async def _dispatch() -> None:
            with _recording_tool_handler(self.recorder):
                outbound = BenchNullOutbound()
                await self.runtime.dispatch(
                    session_key,
                    utterance,
                    outbound=outbound,
                    conn=conn,  # type: ignore[arg-type]
                    channel=channel,
                    chat_id=chat_id,
                    source="benchmark",
                )

        timeout_s = float(self.runtime.turn_timeout_seconds or 90)
        try:
            if not self._loop.is_running():
                return LiveTurnResult(
                    session_key=session_key,
                    channel=channel,
                    utterance=utterance,
                    errors=["dispatch failed: AgentRuntime event loop is not running"],
                    path=path_label,
                )
            future = asyncio.run_coroutine_threadsafe(_dispatch(), self._loop)
            future.result(timeout=timeout_s)
        except TimeoutError:
            return LiveTurnResult(
                session_key=session_key,
                channel=channel,
                utterance=utterance,
                errors=[f"dispatch timed out after {timeout_s:.0f}s"],
                path=path_label,
            )
        except Exception as exc:
            detail = str(exc) or type(exc).__name__
            return LiveTurnResult(
                session_key=session_key,
                channel=channel,
                utterance=utterance,
                errors=[f"dispatch failed: {detail}"],
                path=path_label,
            )

        last = self.recorder.last or {}
        return LiveTurnResult(
            session_key=session_key,
            channel=channel,
            utterance=utterance,
            function=last.get("function"),
            args=dict(last.get("arguments") or {}),
            dispatches=list(self.recorder.dispatches),
            path=path_label,
        )


def _pick_scored_dispatch(
    turn: LiveTurnResult, expected_function: str
) -> tuple[str, dict[str, Any]]:
    """Prefer the first tool call matching expected_function, not the last in the turn."""
    for entry in turn.dispatches:
        name = str(entry.get("function") or "")
        if name == expected_function:
            args = entry.get("arguments") or {}
            return name, dict(args) if isinstance(args, dict) else {}
    last = turn.dispatches[-1] if turn.dispatches else {}
    name = str(last.get("function") or turn.function or "")
    args = last.get("arguments") if last else turn.args
    return name, dict(args or {})


def score_live_turn(item: dict[str, Any], turn: LiveTurnResult) -> dict[str, Any]:
    expected = item.get("expected_args") or {}
    dispatched_name, dispatched_args = _pick_scored_dispatch(turn, item["expected_function"])
    function_correct = dispatched_name == item["expected_function"]
    exact = function_correct and all(
        dispatched_args.get(key) == value for key, value in expected.items()
    )
    hits = sum(1 for key, value in expected.items() if dispatched_args.get(key) == value)
    arg_score = hits / len(expected) if expected else 1.0
    partial = function_correct and arg_score >= 0.80
    errors = list(turn.errors)
    if not dispatched_name and not errors:
        errors.append(f"{item['id']}: LLM did not dispatch a tool call")
    return {
        "id": item["id"],
        "utterance": item["user_utterance"],
        "expected_function": item["expected_function"],
        "dispatched_function": dispatched_name,
        "dispatched_args": dispatched_args,
        "function_correct": function_correct,
        "args_exact_match": exact,
        "partial_args_match": partial,
        "arg_score": round(arg_score, 4),
        "path": turn.path,
        "dispatches": turn.dispatches,
        "errors": errors,
    }


def evaluate_live_scenarios(
    scenarios: list[dict[str, Any]],
    bench_config: dict[str, Any],
    *,
    path_label: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    harness = LiveRuntimeHarness(bench_config)
    startup_errors = harness.start()
    if startup_errors:
        return [], {"path": path_label, "config_source": harness.config_source}, startup_errors

    details: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        for item in scenarios:
            session_key = f"xiaozhi:bench-live-{item['id']}"
            turn = harness.run_turn(
                session_key=session_key,
                utterance=item["user_utterance"],
                channel="xiaozhi",
                path_label=path_label,
            )
            scored = score_live_turn(item, turn)
            details.append({k: v for k, v in scored.items() if k != "errors"})
            errors.extend(scored.get("errors") or [])
    finally:
        harness.stop()

    meta = {
        "path": path_label,
        "config_source": harness.config_source,
        "llm_module": (harness.config or {}).get("selected_module", {}).get("LLM"),
        "llm_model": _llm_model_name(harness.config),
    }
    return details, meta, errors


def _llm_model_name(config: dict[str, Any] | None) -> str | None:
    if not config:
        return None
    module = (config.get("selected_module") or {}).get("LLM")
    if not module:
        return None
    return (config.get("LLM") or {}).get(module, {}).get("model_name")
