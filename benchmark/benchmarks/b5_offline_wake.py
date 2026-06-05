from __future__ import annotations

from benchmark.adapters.offline_wake import run_live_offline_wake, run_mock_offline_wake
from benchmark.config import BenchmarkResult


def run(config: dict) -> BenchmarkResult:
    cfg = config.get("b5", {})
    n_trials = int(cfg.get("n_trials", 5))
    host = str(cfg.get("mqtt_host", "127.0.0.1"))
    port = int(cfg.get("mqtt_port", 1883))
    device_id = str(cfg.get("device_id", "bench-device-001"))
    threshold = float(config["thresholds"]["offline_wake_success_rate"])

    if config.get("mode") == "mock":
        outcome = run_mock_offline_wake(n_trials, device_id)
    else:
        outcome = run_live_offline_wake(
            n_trials=n_trials,
            host=host,
            port=port,
            device_id=device_id,
            mqtt_username=cfg.get("mqtt_username"),
            mqtt_password=cfg.get("mqtt_password"),
        )

    passed = outcome["success_rate"] >= threshold and not outcome["errors"]
    return BenchmarkResult(
        "B5",
        "Offline MQTT wake",
        passed,
        {
            "success_rate": round(float(outcome["success_rate"]), 4),
            "mean_wake_latency_ms": outcome["mean_wake_latency_ms"],
            "n_trials": n_trials,
            "flow": outcome.get("flow"),
            "live_mode": "full-live MQTT" if config.get("mode") == "live" else "simulated mqtt wake",
        },
        outcome["details"],
        outcome["errors"],
    )
