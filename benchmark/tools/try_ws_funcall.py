"""Try one B3 docker-live scenario: WS text dispatch + parse server.log."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmark.adapters.ws_agent_live import dispatch_text_via_ws, resolve_log_capture  # noqa: E402
from benchmark.config import build_config, load_json_data  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Try B3 docker-live funcall path.")
    parser.add_argument("--scenario", default="fc_001", help="Scenario id from funcall_scenarios.json")
    args = parser.parse_args()

    config = build_config("live")
    scenarios = [s for s in load_json_data("funcall_scenarios.json") if s["id"] == args.scenario]
    if not scenarios:
        print(f"Unknown scenario: {args.scenario}", file=sys.stderr)
        return 1

    item = scenarios[0]
    capture = resolve_log_capture(config)
    print(f"log source: {capture.source_label}", file=sys.stderr)

    import asyncio

    b1 = config.get("b1") or {}
    base_device = str(b1.get("device_id") or "bench-device-001")
    device_id = f"{base_device}-try-{int(time.time())}"
    turn = asyncio.run(
        dispatch_text_via_ws(
            config,
            utterance=item["user_utterance"],
            device_id=device_id,
            client_id=f"benchmark-b3-try-{args.scenario}",
            trial_timeout=90.0,
            log_capture=capture,
        )
    )
    print(json.dumps({"turn": turn.__dict__, "expected": item["expected_function"]}, ensure_ascii=False, indent=2))
    return 0 if turn.dispatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
