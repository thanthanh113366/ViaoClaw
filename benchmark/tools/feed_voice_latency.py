#!/usr/bin/env python3
"""Run a single B1 feed_audio latency trial over WebSocket."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmark.adapters.feed_audio import measure_feed_audio_trial  # noqa: E402
from benchmark.config import build_config  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Feed one WAV over WebSocket and measure TTS latency.")
    parser.add_argument(
        "--wav",
        default="benchmark/data/fixtures/asr/001.wav",
        help="WAV fixture to replay as opus frames",
    )
    parser.add_argument("--mode", choices=["mock", "live"], default="live")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = build_config(args.mode)
    wav_path = Path(args.wav)
    if not wav_path.is_file():
        print(f"WAV not found: {wav_path}", file=sys.stderr)
        return 1

    b1 = config["b1"]
    try:
        result = asyncio.run(
            measure_feed_audio_trial(
                config["env"]["viaoclaw_ws_url"],
                wav_path,
                device_id=b1["device_id"],
                device_mac=b1["device_mac"],
                client_id=b1["client_id"],
                authorization=b1.get("authorization"),
                trial_timeout=float(b1.get("trial_timeout_seconds", 45)),
            )
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
