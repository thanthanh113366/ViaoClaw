from __future__ import annotations

import os

from benchmark.adapters.chat_engine_live import evaluate_live_scenarios
from benchmark.adapters.funcall import evaluate_scenario
from benchmark.adapters.ws_agent_live import evaluate_docker_scenarios
from benchmark.config import BenchmarkResult, load_json_data


def run(config: dict) -> BenchmarkResult:
    scenarios = load_json_data("funcall_scenarios.json")
    details = []
    errors: list[str] = []
    function_hits = 0
    exact_hits = 0
    partial_hits = 0

    if config.get("mode") == "live":
        max_live = int(os.environ.get("BENCHMARK_LIVE_MAX_SCENARIOS", "5"))
        live_scenarios = scenarios[:max_live]
        b3_target = str((config.get("b3") or {}).get("target") or "host").lower()
        if b3_target == "docker":
            live_details, meta, live_errors = evaluate_docker_scenarios(live_scenarios, config)
            live_mode = "docker-live"
        else:
            if not config["env"].get("llm_api_key"):
                return BenchmarkResult(
                    "B3",
                    "Function-call accuracy",
                    False,
                    {
                        "function_accuracy": 0.0,
                        "args_exact_accuracy": 0.0,
                        "n_scenarios": len(live_scenarios),
                        "b3_target": "host",
                    },
                    [],
                    ["LIVE host mode requires LLM_API_KEY (or set BENCHMARK_B3_TARGET=docker)"],
                )
            live_details, meta, live_errors = evaluate_live_scenarios(
                live_scenarios,
                config,
                path_label="full-live: AgentRuntime.dispatch -> ChatEngine.chat_sync -> UnifiedToolHandler",
            )
            live_mode = "full-live"
        if live_errors and not live_details:
            return BenchmarkResult(
                "B3",
                "Function-call accuracy",
                False,
                {
                    "function_accuracy": 0.0,
                    "args_exact_accuracy": 0.0,
                    "n_scenarios": len(live_scenarios),
                    **meta,
                    "live_mode": live_mode,
                },
                [],
                live_errors,
            )
        for result in live_details:
            details.append(result)
            errors.extend(
                [err for err in (result.get("errors") or []) if isinstance(err, str)]
            )
        for result in live_details:
            function_hits += 1 if result.get("function_correct") else 0
            exact_hits += 1 if result.get("args_exact_match") else 0
            partial_hits += 1 if result.get("partial_args_match") else 0
        errors.extend(live_errors)
        total = max(1, len(live_details))
        function_accuracy = function_hits / total
        exact_accuracy = exact_hits / total
        passed = function_accuracy >= 0.90 and exact_accuracy >= 0.80 and not live_errors
        return BenchmarkResult(
            "B3",
            "Function-call accuracy",
            passed,
            {
                "function_accuracy": round(function_accuracy, 4),
                "args_exact_accuracy": round(exact_accuracy, 4),
                "args_partial_accuracy": round(partial_hits / total, 4),
                "n_scenarios": len(live_details),
                **meta,
                "live_mode": live_mode,
            },
            details,
            errors,
        )

    for item in scenarios:
        result = evaluate_scenario(item, config)
        details.append({key: value for key, value in result.items() if key != "errors"})
        errors.extend(result.get("errors") or [])
        function_hits += 1 if result["function_correct"] else 0
        exact_hits += 1 if result["args_exact_match"] else 0
        partial_hits += 1 if result["partial_args_match"] else 0

    total = max(1, len(scenarios))
    function_accuracy = function_hits / total
    exact_accuracy = exact_hits / total
    passed = function_accuracy >= 0.90 and exact_accuracy >= 0.80 and not errors
    return BenchmarkResult(
        "B3",
        "Function-call accuracy",
        passed,
        {
            "function_accuracy": round(function_accuracy, 4),
            "args_exact_accuracy": round(exact_accuracy, 4),
            "args_partial_accuracy": round(partial_hits / total, 4),
            "n_scenarios": len(scenarios),
            "path": "mock: pre-recorded tool_call -> UnifiedToolHandler.handle_llm_function_call",
            "live_mode": "mock-only dispatcher replay",
        },
        details,
        errors,
    )
