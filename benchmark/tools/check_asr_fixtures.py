#!/usr/bin/env python3
"""Verify ASR WAV fixtures referenced by benchmark/data/asr_testset.json."""

from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
FIXTURE_DIR = DATA_DIR / "fixtures" / "asr"
TESTSET = DATA_DIR / "asr_testset.json"


def main() -> int:
    with TESTSET.open("r", encoding="utf-8") as handle:
        samples = json.load(handle)

    missing: list[str] = []
    present = 0
    for sample in samples:
        filename = Path(sample["audio_file"]).name
        path = FIXTURE_DIR / filename
        if path.is_file():
            present += 1
        else:
            missing.append(str(path))

    print(f"ASR fixtures: {present}/{len(samples)} present under {FIXTURE_DIR}")
    if missing:
        print(f"Missing {len(missing)} file(s), examples:")
        for path in missing[:5]:
            print(f"  - {path}")
        if len(missing) > 5:
            print(f"  ... and {len(missing) - 5} more")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
