from __future__ import annotations

from benchmark.adapters.chat_engine_live import LiveRuntimeHarness
from benchmark.adapters.parity import run_parity_case
from benchmark.config import BenchmarkResult, load_json_data

PARITY_CASE_IDS = ["fc_001", "fc_006", "fc_011", "fc_016", "fc_021"]


def run(config: dict) -> BenchmarkResult:
    scenarios = [
        item for item in load_json_data("funcall_scenarios.json") if item["id"] in PARITY_CASE_IDS
    ]
    details = []
    passed_cases: list[str] = []
    failed_cases: list[dict] = []
    errors: list[str] = []
    harness: LiveRuntimeHarness | None = None

    if config.get("mode") == "live":
        if not config["env"].get("llm_api_key"):
            return BenchmarkResult(
                "B7",
                "Telegram ↔ Voice parity",
                False,
                {"parity_rate": 0.0, "n_cases": len(scenarios), "live_mode": "partial-live"},
                [],
                ["LIVE mode requires LLM_API_KEY"],
            )
        harness = LiveRuntimeHarness(config)
        startup_errors = harness.start()
        if startup_errors:
            return BenchmarkResult(
                "B7",
                "Telegram ↔ Voice parity",
                False,
                {"parity_rate": 0.0, "n_cases": len(scenarios), "live_mode": "partial-live"},
                [],
                startup_errors,
            )
        config = dict(config)
        config["_live_harness"] = harness

    try:
        for item in scenarios:
            result = run_parity_case(item, config)
            details.append(result)
            errors.extend(result.get("errors") or [])
            if result.get("passed"):
                passed_cases.append(item["id"])
            else:
                failed_cases.append(result)
    finally:
        if harness is not None:
            harness.stop()

    n_cases = len(scenarios)
    parity_rate = len(passed_cases) / max(1, n_cases)
    passed = parity_rate == 1.0 and not errors
    return BenchmarkResult(
        "B7",
        "Telegram ↔ Voice parity",
        passed,
        {
            "parity_rate": round(parity_rate, 4),
            "n_cases": n_cases,
            "passed_cases": passed_cases,
            "failed_cases": failed_cases,
            "path": (
                "mock: _parse_session_key + UnifiedToolHandler adapters"
                if config.get("mode") != "live"
                else "partial-live: InboundDispatcher + ChatEngine for telegram/voice"
            ),
            "live_mode": "partial-live" if config.get("mode") == "live" else "mock adapter",
        },
        details,
        errors,
    )
