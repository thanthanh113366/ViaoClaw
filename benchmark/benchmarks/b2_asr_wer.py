from __future__ import annotations

import asyncio
import re
import sys

from benchmark.adapters.asr_live import live_asr_request_options, transcribe_openai_whisper
from benchmark.config import BenchmarkResult, load_json_data
from benchmark.tools.live_prereqs import check_asr_fixtures

_PUNCTUATION_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)


def normalize_transcript(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for fair WER."""
    cleaned = _PUNCTUATION_RE.sub(" ", (text or "").lower())
    return " ".join(cleaned.split())


def compute_wer(reference: str, hypothesis: str) -> float:
    try:
        from jiwer import wer
    except ImportError:
        return _fallback_wer(reference, hypothesis)
    return float(wer(normalize_transcript(reference), normalize_transcript(hypothesis)))


def _fallback_wer(reference: str, hypothesis: str) -> float:
    ref = normalize_transcript(reference).split()
    hyp = normalize_transcript(hypothesis).split()
    rows = len(ref) + 1
    cols = len(hyp) + 1
    dp = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        dp[i][0] = i
    for j in range(cols):
        dp[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    return dp[-1][-1] / max(1, len(ref))


async def _live_transcribe(sample: dict, config: dict) -> str:
    from pathlib import Path

    audio_path = Path(config["paths"]["asr_fixture_dir"]) / Path(sample["audio_file"]).name
    b2 = config.get("b2") or {}

    # Local Sherpa ASR path
    asr_type = b2.get("asr_type", "cloud")
    if asr_type == "local":
        from benchmark.adapters.asr_local import transcribe_local_sherpa

        model_dir = b2.get("model_dir", "")
        model_type = b2.get("model_type", "zipformer_vi")
        if not model_dir:
            raise ValueError("b2.model_dir is required for local ASR")
        return transcribe_local_sherpa(audio_path, model_dir=model_dir, model_type=model_type)

    # Cloud ASR path (OpenAI-compatible)
    api_key = str(b2.get("api_key") or config["env"].get("llm_api_key") or "")
    options = live_asr_request_options(api_key, sample, defaults=b2)
    return await transcribe_openai_whisper(audio_path, **options)


def _live_asr_path_label(b2: dict) -> str:
    if b2.get("asr_type") == "local":
        model_dir = b2.get("model_dir", "unknown")
        return f"Sherpa Local ASR ({model_dir})"
    model = b2.get("model_name") or "gpt-4o-transcribe"
    base = b2.get("asr_base_url") or "https://api.openai.com/v1/audio/transcriptions"
    if "groq.com" in str(base):
        return f"Groq ASR {model}"
    if "openai.com" in str(base):
        return f"OpenAI ASR {model}"
    return f"ASR {model} @ {base}"


def _live_metrics_base(mode: str, fixture_report, b2: dict | None = None) -> dict:
    b2 = b2 or {}
    return {
        "mode": mode,
        "fixture_count": fixture_report.fixture_count,
        "expected_count": fixture_report.expected_count,
        "missing_count": fixture_report.missing_count,
        "path": _live_asr_path_label(b2),
        "asr_model": b2.get("model_name"),
        "asr_base_url": b2.get("asr_base_url"),
        "live_mode": "full-live",
    }


def run(config: dict) -> BenchmarkResult:
    samples = load_json_data("asr_testset.json")
    mode = config.get("mode", "mock")
    fixture_report = check_asr_fixtures(config["paths"]["asr_fixture_dir"])

    if mode == "live":
        if fixture_report.missing_count > 0:
            examples = ", ".join(fixture_report.missing_examples[:3])
            suffix = (
                f" (+{fixture_report.missing_count - 3} more)"
                if fixture_report.missing_count > 3
                else ""
            )
            return BenchmarkResult(
                "B2",
                "ASR WER Vietnamese",
                False,
                _live_metrics_base(mode, fixture_report, config.get("b2") or {}),
                [],
                [
                    f"LIVE ASR missing {fixture_report.missing_count}/{fixture_report.expected_count} "
                    f"WAV files. Examples: {examples}{suffix}. "
                    "Run: python benchmark/tools/check_asr_fixtures.py"
                ],
            )
        b2 = config.get("b2") or {}
        asr_type = b2.get("asr_type", "cloud")
        if asr_type != "local":
            asr_key = b2.get("api_key") or config["env"].get("llm_api_key")
            if not asr_key:
                return BenchmarkResult(
                    "B2",
                    "ASR WER Vietnamese",
                    False,
                    _live_metrics_base(mode, fixture_report, b2),
                    [],
                    ["LIVE mode requires LLM_API_KEY or BENCHMARK_ASR_API_KEY"],
                )

    details = []
    errors: list[str] = []
    total_wer = 0.0
    n_correct = 0

    for sample in samples:
        reference = sample["reference"]
        if mode == "live":
            print(f"[B2] transcribing {sample['id']}...", file=sys.stderr, flush=True)
            try:
                hypothesis = asyncio.run(_live_transcribe(sample, config))
            except Exception as exc:
                errors.append(f"{sample['id']}: {exc}")
                hypothesis = ""
        else:
            hypothesis = sample.get("mock_transcript", reference)
        sample_wer = compute_wer(reference, hypothesis)
        total_wer += sample_wer
        correct = sample_wer == 0
        n_correct += 1 if correct else 0
        details.append(
            {
                "id": sample["id"],
                "reference": reference,
                "hypothesis": hypothesis,
                "wer": round(sample_wer, 4),
                "correct": correct,
            }
        )

    mean_wer = total_wer / max(1, len(samples))
    passed = mean_wer < float(config["thresholds"]["asr_wer"]) and not errors
    b2 = config.get("b2") or {}
    metrics = {
        "wer": round(mean_wer, 4),
        "n_samples": len(samples),
        "n_correct": n_correct,
        "mode": mode,
        "fixture_count": fixture_report.fixture_count,
        "expected_count": fixture_report.expected_count,
        "missing_count": fixture_report.missing_count,
        "path": (
            _live_asr_path_label(b2)
            if mode == "live"
            else "mock_transcript (mock)"
        ),
        "asr_model": b2.get("model_name") if mode == "live" else None,
        "live_mode": "full-live" if mode == "live" else "fixture transcript",
    }
    return BenchmarkResult("B2", "ASR WER Vietnamese", passed, metrics, details, errors)
