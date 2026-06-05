from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable

from benchmark.config import BenchmarkResult, build_config
from benchmark.report import render_table, write_json

BENCHMARKS = {
    "B1": "benchmark.benchmarks.b1_latency",
    "B2": "benchmark.benchmarks.b2_asr_wer",
    "B3": "benchmark.benchmarks.b3_funcall_accuracy",
    "B4": "benchmark.benchmarks.b4_cron_timing",
    "B5": "benchmark.benchmarks.b5_offline_wake",
    "B6": "benchmark.benchmarks.b6_exec_guard",
    "B7": "benchmark.benchmarks.b7_telegram_parity",
}


def _load_runner(module_name: str) -> Callable[[dict], BenchmarkResult]:
    module = importlib.import_module(module_name)
    runner = getattr(module, "run")
    return runner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ViaoClaw benchmarks.")
    parser.add_argument("--only", nargs="+", choices=sorted(BENCHMARKS), help="Run subset.")
    parser.add_argument(
        "--mode",
        choices=["mock", "live"],
        default="mock",
        help="mock is CI-safe; live may require hardware, broker, fixtures and API keys.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = build_config(args.mode)
    selected = args.only or list(BENCHMARKS)
    results = []
    for benchmark_id in selected:
        print(f"[benchmark] Running {benchmark_id}...", file=sys.stderr, flush=True)
        runner = _load_runner(BENCHMARKS[benchmark_id])
        result = runner(config)
        result.metrics["mode"] = args.mode
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"[benchmark] Finished {benchmark_id}: {status}", file=sys.stderr, flush=True)
    render_table(results)
    json_path = write_json(results)
    print(f"JSON report: {json_path}")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

