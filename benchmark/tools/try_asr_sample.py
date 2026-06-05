#!/usr/bin/env python3
"""Run live B2 ASR on a single WAV sample."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmark.adapters.asr_live import live_asr_request_options, transcribe_openai_whisper  # noqa: E402
from benchmark.benchmarks.b2_asr_wer import compute_wer  # noqa: E402
from benchmark.config import build_config, load_json_data  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transcribe one ASR fixture and compute WER.")
    parser.add_argument("--id", default="asr_001", help="Sample id from asr_testset.json")
    parser.add_argument("--wav", default=None, help="Override WAV path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = build_config("live")
    b2 = config.get("b2") or {}
    asr_key = b2.get("api_key") or config["env"].get("llm_api_key")
    if not asr_key:
        print("LLM_API_KEY or BENCHMARK_ASR_API_KEY is required for live ASR", file=sys.stderr)
        return 1

    sample = next((item for item in load_json_data("asr_testset.json") if item["id"] == args.id), None)
    if sample is None:
        print(f"Unknown sample id: {args.id}", file=sys.stderr)
        return 1

    if args.wav:
        audio_path = Path(args.wav)
    else:
        audio_path = Path(config["paths"]["asr_fixture_dir"]) / Path(sample["audio_file"]).name
    if not audio_path.is_file():
        print(f"WAV not found: {audio_path}", file=sys.stderr)
        return 1

    try:
        options = live_asr_request_options(str(asr_key), sample, defaults=b2)
        hypothesis = asyncio.run(transcribe_openai_whisper(audio_path, **options))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    reference = sample["reference"]
    wer = compute_wer(reference, hypothesis)
    print(
        json.dumps(
            {
                "ok": True,
                "id": sample["id"],
                "wav": str(audio_path),
                "model": options["model_name"],
                "reference": reference,
                "hypothesis": hypothesis,
                "wer": round(wer, 4),
                "correct": wer == 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
