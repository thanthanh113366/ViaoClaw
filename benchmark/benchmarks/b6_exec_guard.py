from __future__ import annotations

import shlex

from benchmark.config import BenchmarkResult, load_json_data, patch_server_logging


def _command(sample: dict) -> str:
    args = sample.get("args") or []
    if args:
        return " ".join([str(sample["cmd"]), shlex.join([str(arg) for arg in args])])
    return str(sample["cmd"])


def run(config: dict) -> BenchmarkResult:
    patch_server_logging()
    samples = load_json_data("exec_policy_testset.json")
    try:
        from core.exec.runner import ExecRunner
    except Exception as exc:
        return BenchmarkResult("B6", "Exec guard", False, {}, [], [f"ExecRunner import failed: {exc}"])

    runner = ExecRunner({"exec": config["exec"]})
    details = []
    tp = fp = tn = fn = 0
    for sample in samples:
        command = _command(sample)
        label = sample["label"]
        blocked_reason = runner._guard_command(command)  # noqa: SLF001 - benchmark targets guard policy directly.
        blocked = blocked_reason is not None
        dangerous = label == "dangerous"
        if dangerous and blocked:
            tp += 1
        elif not dangerous and blocked:
            fp += 1
        elif not dangerous and not blocked:
            tn += 1
        elif dangerous and not blocked:
            fn += 1
        details.append(
            {
                "cmd": command,
                "label": label,
                "blocked": blocked,
                "blocked_reason": blocked_reason,
                "correct": blocked == dangerous,
            }
        )

    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    n_safe = sum(1 for sample in samples if sample["label"] == "safe")
    n_dangerous = sum(1 for sample in samples if sample["label"] == "dangerous")
    passed = (
        precision >= float(config["thresholds"]["exec_precision"])
        and recall >= float(config["thresholds"]["exec_recall"])
    )
    return BenchmarkResult(
        "B6",
        "Exec guard precision/recall",
        passed,
        {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "n_safe": n_safe,
            "n_dangerous": n_dangerous,
        },
        details,
        [] if passed else ["Exec guard precision/recall below threshold."],
    )

