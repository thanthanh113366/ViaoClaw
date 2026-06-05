from __future__ import annotations

import asyncio

from benchmark.adapters.feed_audio import format_structured_errors, run_feed_audio_trials
from benchmark.config import BenchmarkResult, percentile


def run(config: dict) -> BenchmarkResult:
    n_trials = int(config.get("b1", {}).get("n_trials", 20))
    deltas, details, structured_errors = asyncio.run(run_feed_audio_trials(config))

    if not deltas:
        categories = sorted({item.get("category") for item in structured_errors})
        return BenchmarkResult(
            "B1",
            "End-to-end voice latency",
            False,
            {
                "n_trials": 0,
                "source": "feed_audio",
                "error_categories": categories,
                "path": "WebSocket hello → listen manual → opus frames → listen stop → first TTS",
            },
            details,
            format_structured_errors(structured_errors),
        )

    deltas = deltas[:n_trials]
    p50 = percentile(deltas, 0.50) or 0.0
    p95 = percentile(deltas, 0.95) or 0.0
    thresholds = config["thresholds"]
    passed = (
        p50 <= float(thresholds["latency_p50_warn_ms"])
        and p95 <= float(thresholds["latency_p95_warn_ms"])
        and len(deltas) > 0
    )
    return BenchmarkResult(
        "B1",
        "End-to-end voice latency",
        passed,
        {
            "p50_ms": round(p50, 3),
            "p95_ms": round(p95, 3),
            "n_trials": len(deltas),
            "source": "feed_audio",
            "path": "WebSocket hello → listen manual → opus frames → listen stop → first TTS",
        },
        details,
        format_structured_errors(structured_errors),
    )
