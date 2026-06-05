from __future__ import annotations

import tempfile
import time
from pathlib import Path

from benchmark.config import BenchmarkResult, mean, patch_server_logging


class _RecordingHandler:
    def __init__(self) -> None:
        self.fires: dict[str, float] = {}

    def handle(self, job: dict) -> None:
        self.fires[job["id"]] = time.time()


def run(config: dict) -> BenchmarkResult:
    patch_server_logging()
    try:
        from core.cron.service import CronService
        from core.cron.store import JobStore
    except Exception as exc:
        return BenchmarkResult("B4", "Cron timing", False, {}, [], [f"CronService import failed: {exc}"])

    n_jobs = int(config.get("b4", {}).get("n_jobs", 10))
    threshold = float(config["thresholds"]["cron_max_delta_seconds"])
    details = []
    deltas: list[float] = []
    handler = _RecordingHandler()

    with tempfile.TemporaryDirectory(prefix="viaoclaw-cron-bench-") as tmp:
        cron_config = {
            "cron": {
                "enabled": True,
                "timezone": "Asia/Ho_Chi_Minh",
                "store_path": str(Path(tmp) / "jobs.json"),
            }
        }
        service = CronService(cron_config, JobStore(cron_config), handler)
        expected: dict[str, float] = {}
        service.start()
        try:
            for index in range(n_jobs):
                scheduled = time.time() + 3 + index
                job = service.add_job(
                    name=f"bench-cron-timing-{index}",
                    schedule={"kind": "at", "atMs": int(scheduled * 1000)},
                    message="benchmark",
                    deliver=True,
                    channel="telegram",
                    target_id="benchmark",
                )
                expected[job["id"]] = scheduled

            deadline = time.time() + 3 + n_jobs + threshold + 2
            while time.time() < deadline and len(handler.fires) < n_jobs:
                time.sleep(0.05)

            for job_id, scheduled in expected.items():
                actual = handler.fires.get(job_id)
                if actual is None:
                    details.append({"job_id": job_id, "fired": False, "delta_seconds": None})
                    continue
                delta = abs(actual - scheduled)
                deltas.append(delta)
                details.append(
                    {
                        "job_id": job_id,
                        "fired": True,
                        "delta_seconds": round(delta, 4),
                        "on_time": delta <= threshold,
                    }
                )
        finally:
            service.stop()

    on_time = sum(1 for item in details if item.get("on_time"))
    on_time_rate = on_time / max(1, n_jobs)
    max_delta = max(deltas) if deltas else None
    passed = len(deltas) == n_jobs and max_delta is not None and max_delta <= threshold
    return BenchmarkResult(
        "B4",
        "Cron fire timing",
        passed,
        {
            "mean_delta_s": round(mean(deltas) or 0.0, 4),
            "max_delta_s": round(max_delta or 0.0, 4),
            "on_time_rate": round(on_time_rate, 4),
            "n_jobs": n_jobs,
        },
        details,
        [] if passed else ["At least one cron job missed or exceeded timing threshold."],
    )

