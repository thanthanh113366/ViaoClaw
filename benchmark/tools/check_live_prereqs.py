#!/usr/bin/env python3
"""Preflight checks for live benchmark runs (TCP/files/env only)."""

from __future__ import annotations

import json
import sys

from benchmark.tools.live_prereqs import run_all_checks


def main() -> int:
    report = run_all_checks()
    print(json.dumps(report, indent=2, ensure_ascii=False))

    failed = False
    mqtt = report["mqtt"]
    if not report["mqtt_tcp"]["ok"]:
        failed = True
    if mqtt.get("auth_ok") is False:
        failed = True
    if not report["asr_fixtures"]["ok"]:
        print(
            f"\nASR fixtures: {report['asr_fixtures']['fixture_count']}/"
            f"{report['asr_fixtures']['expected_count']} present",
            file=sys.stderr,
        )
    if not report["server_tcp"]["ok"]:
        failed = True

    llm = next((e for e in report["env"] if e["name"] == "LLM_API_KEY"), None)
    if llm and not llm["set"]:
        print("\nLLM_API_KEY not set (required for live B2/B3/B7)", file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
