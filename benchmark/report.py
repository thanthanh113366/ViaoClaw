from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from benchmark.config import BenchmarkResult, RESULTS_DIR


def _key_metric(result: BenchmarkResult) -> str:
    m = result.metrics
    if result.benchmark_id == "B1":
        return f"P50={m.get('p50_ms', '-')}ms P95={m.get('p95_ms', '-')}ms"
    if result.benchmark_id == "B2":
        return f"WER={m.get('wer', '-')}"
    if result.benchmark_id == "B3":
        return f"func={m.get('function_accuracy', '-')} args={m.get('args_exact_accuracy', '-')}"
    if result.benchmark_id == "B4":
        return f"mean={m.get('mean_delta_s', '-')}s on-time={m.get('on_time_rate', '-')}"
    if result.benchmark_id == "B5":
        if result.errors:
            return result.errors[0]
        return f"success={m.get('success_rate', '-')} wake={m.get('mean_wake_latency_ms', '-')}ms"
    if result.benchmark_id == "B6":
        return f"P={m.get('precision', '-')} R={m.get('recall', '-')} F1={m.get('f1', '-')}"
    if result.benchmark_id == "B7":
        return f"parity={len(m.get('passed_cases', []))}/{m.get('n_cases', '-')}"
    return str(m)


def render_table(results: Iterable[BenchmarkResult]) -> None:
    rows = list(results)
    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        for result in rows:
            status = "PASS" if result.passed else "FAIL"
            print(f"{result.benchmark_id} · {result.name}: {status} | {_key_metric(result)}")
        return

    table = Table(title="ViaoClaw Benchmark Suite")
    table.add_column("Benchmark")
    table.add_column("Result")
    table.add_column("Key metric")
    for result in rows:
        status = "[green]PASS[/green]" if result.passed else "[red]FAIL/SKIP[/red]"
        table.add_row(f"{result.benchmark_id} · {result.name}", status, _key_metric(result))
    Console().print(table)


def write_json(results: Iterable[BenchmarkResult], output_dir: Path = RESULTS_DIR) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat().replace(":", "")
    path = output_dir / f"run_{stamp}.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": next((result.metrics.get("mode") for result in results if result.metrics.get("mode")), None),
        "passed": all(result.passed for result in results),
        "results": [result.to_dict() for result in results],
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path

