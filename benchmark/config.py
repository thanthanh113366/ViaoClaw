from __future__ import annotations

import json
import os
import sys
import types
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

BENCHMARK_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = BENCHMARK_ROOT.parent

# benchmark/ nằm trong xiaozhi-esp32-server/ hoặc cạnh nó (monorepo xiaozhi/)
_server_in_repo = PROJECT_ROOT / "main" / "xiaozhi-server"
_server_in_monorepo = PROJECT_ROOT / "xiaozhi-esp32-server" / "main" / "xiaozhi-server"
if _server_in_repo.is_dir():
    SERVER_ROOT = _server_in_repo
elif _server_in_monorepo.is_dir():
    SERVER_ROOT = _server_in_monorepo
else:
    SERVER_ROOT = _server_in_repo
DATA_DIR = Path(__file__).resolve().parent / "data"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

LATENCY_P50_WARN_MS = 800.0
LATENCY_P95_WARN_MS = 1500.0
ASR_WER_THRESHOLD = 0.15
CRON_MAX_DELTA_SECONDS = 2.0
EXEC_PRECISION_THRESHOLD = 0.95
EXEC_RECALL_THRESHOLD = 0.90
OFFLINE_WAKE_SUCCESS_THRESHOLD = 0.90

N_TRIALS = 20
N_JOBS = 10

DEFAULT_DENY_PATTERNS = [
    r"rm\s+.*(-rf|-fr|--no-preserve-root)",
    r"\bmkfs(\.\w+)?\b",
    r":\(\)\s*\{",
    r"\bsudo\s+su\b",
    r"\bpasswd\b",
    r"\bdd\s+.*if=/dev/zero",
    r"(curl|wget)\b.*\|\s*(sh|bash)",
    r"\bshutdown\b|\breboot\b|\bpoweroff\b",
    r">\s*/dev/sd[a-z]",
]


@dataclass
class BenchmarkResult:
    benchmark_id: str
    name: str
    passed: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    details: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ensure_server_path() -> None:
    server_path = str(SERVER_ROOT)
    if server_path not in sys.path:
        sys.path.insert(0, server_path)


class NoopLogger:
    def bind(self, **_kwargs):
        return self

    def debug(self, *_args, **_kwargs) -> None:
        pass

    def info(self, *_args, **_kwargs) -> None:
        pass

    def warning(self, *_args, **_kwargs) -> None:
        pass

    def error(self, *_args, **_kwargs) -> None:
        pass


def patch_server_logging() -> None:
    """Avoid production config.yaml parsing while importing server modules."""
    ensure_server_path()
    logger_module = types.ModuleType("config.logger")
    logger_module.setup_logging = lambda: NoopLogger()
    sys.modules["config.logger"] = logger_module


def ensure_benchmark_import_stubs() -> None:
    """Stub optional production deps so benchmark can import tool/cron modules in CI."""
    patch_server_logging()
    from benchmark.stubs.optional_deps import install_all_optional_stubs

    install_all_optional_stubs()


def load_json_data(filename: str) -> list[dict[str, Any]]:
    path = DATA_DIR / filename
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON list")
    return data


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * pct
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def mean(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def build_config(mode: str) -> dict[str, Any]:
    mqtt_port = int(os.environ.get("MQTT_PORT", "1883"))
    server_url = os.environ.get("VIAOCLAW_SERVER_URL", "http://127.0.0.1:8000/")
    ws_url = os.environ.get("VIAOCLAW_WS_URL", "ws://127.0.0.1:8000/xiaozhi/v1/")
    return {
        "mode": mode,
        "paths": {
            "project_root": str(PROJECT_ROOT),
            "server_root": str(SERVER_ROOT),
            "data_dir": str(DATA_DIR),
            "results_dir": str(RESULTS_DIR),
            "asr_fixture_dir": str(DATA_DIR / "fixtures" / "asr"),
        },
        "discovery": {
            "server_tcp": "127.0.0.1:8000",
            "mqtt_tcp": f"{os.environ.get('MQTT_HOST', '127.0.0.1')}:{mqtt_port}",
            "server_network_mode": "host (docker inspect: NetworkMode host)",
            "websocket_url": ws_url,
            "http_probe_url": server_url,
            "injection_status": (
                "B1 feeds labelled ASR WAV fixtures as opus frames over WebSocket "
                "(hello → listen manual → stop). Set VIAOCLAW_WS_URL if the server is not local."
            ),
        },
        "thresholds": {
            "latency_p50_warn_ms": LATENCY_P50_WARN_MS,
            "latency_p95_warn_ms": LATENCY_P95_WARN_MS,
            "asr_wer": ASR_WER_THRESHOLD,
            "cron_max_delta_seconds": CRON_MAX_DELTA_SECONDS,
            "offline_wake_success_rate": OFFLINE_WAKE_SUCCESS_THRESHOLD,
            "exec_precision": EXEC_PRECISION_THRESHOLD,
            "exec_recall": EXEC_RECALL_THRESHOLD,
        },
        "b1": {
            "n_trials": N_TRIALS,
            "device_id": os.environ.get("BENCHMARK_DEVICE_ID", "bench-device-001"),
            "device_mac": os.environ.get("BENCHMARK_DEVICE_MAC", "11:22:33:44:55:66"),
            "client_id": os.environ.get("BENCHMARK_CLIENT_ID", "benchmark-feed-audio"),
            "trial_timeout_seconds": float(os.environ.get("BENCHMARK_B1_TIMEOUT", "45")),
            "authorization": os.environ.get("VIAOCLAW_WS_TOKEN"),
        },
        "b2": {
            "asr_type": os.environ.get("BENCHMARK_ASR_TYPE", "cloud"),
            "asr_base_url": os.environ.get(
                "BENCHMARK_ASR_BASE_URL",
                "https://api.openai.com/v1/audio/transcriptions",
            ),
            "model_name": os.environ.get("BENCHMARK_ASR_MODEL", "gpt-4o-transcribe"),
            "model_dir": os.environ.get("BENCHMARK_ASR_MODEL_DIR", ""),
            "model_type": os.environ.get("BENCHMARK_ASR_MODEL_TYPE", "zipformer_vi"),
            "language": os.environ.get("BENCHMARK_ASR_LANGUAGE", "vi"),
            "api_key": os.environ.get("BENCHMARK_ASR_API_KEY") or os.environ.get("LLM_API_KEY"),
            # Empty string disables prompt; unset uses provider default in asr_live.py
            **(
                {"prompt": os.environ["BENCHMARK_ASR_PROMPT"]}
                if "BENCHMARK_ASR_PROMPT" in os.environ
                else {}
            ),
        },
        "b3": {
            "target": os.environ.get("BENCHMARK_B3_TARGET", "host").lower(),
            "server_log": os.environ.get("BENCHMARK_SERVER_LOG"),
            "docker_container": os.environ.get("BENCHMARK_DOCKER_CONTAINER", "xiaozhi-esp32-server"),
            "ws_conn_ready_timeout": os.environ.get("BENCHMARK_WS_CONN_READY_TIMEOUT", "30"),
            "ws_init_wait_seconds": os.environ.get("BENCHMARK_WS_INIT_WAIT_SECONDS", "5"),
        },
        "b4": {"n_jobs": N_JOBS},
        "b5": {
            "n_trials": 5,
            "mqtt_host": os.environ.get("MQTT_HOST", "127.0.0.1"),
            "mqtt_port": mqtt_port,
            "mqtt_username": os.environ.get("MQTT_USERNAME"),
            "mqtt_password": os.environ.get("MQTT_PASSWORD"),
            "device_id": "bench-device-001",
        },
        "exec": {
            "workspace": "/tmp/viaoclaw-benchmark-exec",
            "timeout_seconds": 2,
            "max_output_bytes": 8192,
            "allow_network": False,
            "deny_patterns": DEFAULT_DENY_PATTERNS,
        },
        "env": {
            "llm_api_key": os.environ.get("LLM_API_KEY"),
            "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN"),
            "viaoclaw_server_url": server_url,
            "viaoclaw_ws_url": ws_url,
        },
    }

