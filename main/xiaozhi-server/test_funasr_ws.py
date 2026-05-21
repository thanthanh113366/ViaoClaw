#!/usr/bin/env python3
"""Quick FunASR WSS connectivity test (no opus; sends silence PCM)."""
import argparse
import asyncio
import json
import struct
import uuid

import websockets


def pcm_silence_ms(ms: int, rate: int = 16000) -> bytes:
    samples = int(rate * ms / 1000)
    return struct.pack(f"<{samples}h", *([0] * samples))


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=10095)
    parser.add_argument("--ssl", action="store_true")
    parser.add_argument("--mode", default="2pass")
    args = parser.parse_args()

    scheme = "wss" if args.ssl else "ws"
    uri = f"{scheme}://{args.host}:{args.port}"
    session_id = uuid.uuid4().hex[:8]

    async with websockets.connect(
        uri, subprotocols=["binary"], ping_interval=None
    ) as ws:
        cfg = {
            "mode": args.mode,
            "chunk_size": [5, 10, 5],
            "chunk_interval": 10,
            "wav_name": session_id,
            "is_speaking": True,
            "itn": False,
            "audio_fs": 16000,
        }
        await ws.send(json.dumps(cfg))
        print("sent config:", cfg)

        for _ in range(5):
            await ws.send(pcm_silence_ms(200))
            await asyncio.sleep(0.05)

        await ws.send(json.dumps({"is_speaking": False}))
        print("sent is_speaking=false")

        try:
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=15)
                if isinstance(msg, bytes):
                    continue
                data = json.loads(msg)
                print("recv:", data)
                if data.get("is_final"):
                    break
        except asyncio.TimeoutError:
            print("timeout waiting for final result (server may still be loading models)")


if __name__ == "__main__":
    asyncio.run(main())
