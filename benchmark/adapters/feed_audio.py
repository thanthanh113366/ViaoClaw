from __future__ import annotations

import asyncio
import json
import sys
import time
import wave
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

DEFAULT_DEVICE_ID = "bench-device-001"
DEFAULT_DEVICE_MAC = "11:22:33:44:55:66"
DEFAULT_CLIENT_ID = "benchmark-feed-audio"
SAMPLE_RATE = 16000
FRAME_SAMPLES = 960  # 60 ms @ 16 kHz
FRAME_BYTES = FRAME_SAMPLES * 2


def _load_audioop():
    try:
        import audioop

        return audioop
    except ModuleNotFoundError:
        try:
            import audioop_lts as audioop

            return audioop
        except ModuleNotFoundError as exc:
            raise ImportError(
                "WAV must be 16 kHz mono 16-bit, or install audioop-lts: pip install audioop-lts"
            ) from exc


def _read_pcm_16k_mono(path: Path) -> bytes:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        frame_rate = handle.getframerate()
        pcm = handle.readframes(handle.getnframes())

    if channels == 1 and frame_rate == SAMPLE_RATE and sample_width == 2:
        return pcm

    audioop = _load_audioop()
    if channels != 1:
        pcm = audioop.tomono(pcm, sample_width, 0.5, 0.5)
        channels = 1
    if frame_rate != SAMPLE_RATE:
        pcm, _state = audioop.ratecv(pcm, sample_width, channels, frame_rate, SAMPLE_RATE, None)
    if sample_width != 2:
        pcm = audioop.lin2lin(pcm, sample_width, 2)
    return pcm


def build_ws_url(
    base_url: str,
    *,
    device_id: str,
    client_id: str,
    authorization: str | None = None,
) -> str:
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("device-id", device_id)
    query.setdefault("client-id", client_id)
    if authorization:
        token = authorization if authorization.startswith("Bearer ") else f"Bearer {authorization}"
        query.setdefault("authorization", token)
    return urlunparse(parsed._replace(query=urlencode(query)))


def wav_to_opus_frames(wav_path: str | Path) -> list[bytes]:
    path = Path(wav_path)
    if not path.is_file():
        raise FileNotFoundError(f"WAV fixture not found: {path}")

    try:
        import opuslib_next
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise ImportError(
            "opuslib_next is required for B1 feed_audio. "
            "Install server deps or: pip install opuslib-next"
        ) from exc

    with wave.open(str(path), "rb") as handle:
        if handle.getnframes() == 0:
            raise ValueError(f"WAV is empty: {path}")

    pcm = _read_pcm_16k_mono(path)

    encoder = opuslib_next.Encoder(SAMPLE_RATE, 1, opuslib_next.APPLICATION_AUDIO)
    frames: list[bytes] = []
    for offset in range(0, len(pcm), FRAME_BYTES):
        chunk = pcm[offset : offset + FRAME_BYTES]
        if len(chunk) < FRAME_BYTES:
            chunk += b"\x00" * (FRAME_BYTES - len(chunk))
        frames.append(encoder.encode(chunk, FRAME_SAMPLES))
    if not frames:
        raise ValueError(f"WAV produced no opus frames: {path}")
    return frames


def pick_trial_wavs(asr_fixture_dir: str | Path, n_trials: int) -> list[Path]:
    fixture_dir = Path(asr_fixture_dir)
    candidates = sorted(fixture_dir.glob("*.wav"))
    if not candidates:
        raise FileNotFoundError(f"No WAV fixtures under {fixture_dir}")
    return [candidates[index % len(candidates)] for index in range(n_trials)]


async def _wait_for_hello(ws: Any, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        message = await asyncio.wait_for(ws.recv(), timeout=max(remaining, 0.1))
        if isinstance(message, bytes):
            continue
        payload = json.loads(message)
        if payload.get("type") == "hello":
            return payload
    raise TimeoutError("Timed out waiting for hello response")


async def _recv_until_tts(
    ws: Any,
    stop_sent_at: float,
    timeout: float,
) -> tuple[float, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        message = await asyncio.wait_for(ws.recv(), timeout=max(remaining, 0.1))
        now = time.perf_counter()
        if isinstance(message, bytes) and len(message) > 0:
            return (now - stop_sent_at) * 1000, "first_tts_binary"
        payload = json.loads(message)
        if payload.get("type") == "tts" and payload.get("state") == "start":
            return (now - stop_sent_at) * 1000, "tts_start"
    raise TimeoutError("Timed out waiting for first TTS audio")


async def measure_feed_audio_trial(
    ws_url: str,
    wav_path: str | Path,
    *,
    device_id: str = DEFAULT_DEVICE_ID,
    device_mac: str = DEFAULT_DEVICE_MAC,
    client_id: str = DEFAULT_CLIENT_ID,
    authorization: str | None = None,
    trial_timeout: float = 45.0,
    frame_interval_seconds: float = 0.06,
) -> dict[str, Any]:
    try:
        import websockets
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise ImportError(
            "websockets is required for B1 feed_audio. Install: pip install websockets"
        ) from exc

    opus_frames = wav_to_opus_frames(wav_path)
    connect_url = build_ws_url(
        ws_url,
        device_id=device_id,
        client_id=client_id,
        authorization=authorization,
    )
    headers = {
        "Device-Id": device_id,
        "Client-Id": client_id,
    }
    if authorization:
        headers["Authorization"] = (
            authorization if authorization.startswith("Bearer ") else f"Bearer {authorization}"
        )

    async with websockets.connect(connect_url, additional_headers=headers) as ws:
        hello = {
            "type": "hello",
            "device_id": device_id,
            "device_name": "benchmark-feed-audio",
            "device_mac": device_mac,
            "token": authorization or "",
            "features": {},
        }
        await ws.send(json.dumps(hello))
        await _wait_for_hello(ws, timeout=min(10.0, trial_timeout))

        await ws.send(json.dumps({"type": "listen", "mode": "manual", "state": "start"}))
        for frame in opus_frames:
            await ws.send(frame)
            if frame_interval_seconds > 0:
                await asyncio.sleep(frame_interval_seconds)

        stop_sent_at = time.perf_counter()
        await ws.send(json.dumps({"type": "listen", "mode": "manual", "state": "stop"}))
        delta_ms, marker = await _recv_until_tts(
            ws,
            stop_sent_at,
            timeout=max(5.0, trial_timeout - (time.perf_counter() - stop_sent_at)),
        )
        return {
            "delta_ms": round(delta_ms, 3),
            "marker": marker,
            "wav": Path(wav_path).name,
            "opus_frames": len(opus_frames),
        }


async def run_feed_audio_trials(config: dict[str, Any]) -> tuple[list[float], list[dict[str, Any]], list[dict[str, str]]]:
    b1 = config.get("b1", {})
    n_trials = int(b1.get("n_trials", 20))
    ws_url = config.get("env", {}).get("viaoclaw_ws_url") or config.get("discovery", {}).get(
        "websocket_url", "ws://127.0.0.1:8000/xiaozhi/v1/"
    )
    asr_fixture_dir = config["paths"]["asr_fixture_dir"]
    trial_timeout = float(b1.get("trial_timeout_seconds", 45.0))
    device_id = str(b1.get("device_id", DEFAULT_DEVICE_ID))
    device_mac = str(b1.get("device_mac", DEFAULT_DEVICE_MAC))
    client_id = str(b1.get("client_id", DEFAULT_CLIENT_ID))
    authorization = b1.get("authorization") or config.get("env", {}).get("viaoclaw_ws_token")

    structured_errors: list[dict[str, str]] = []
    details: list[dict[str, Any]] = []
    deltas: list[float] = []

    try:
        trial_wavs = pick_trial_wavs(asr_fixture_dir, n_trials)
    except Exception as exc:
        structured_errors.append(
            {
                "category": "missing_audio_fixture",
                "message": str(exc),
                "hint": "Add labelled WAV files under benchmark/data/fixtures/asr/",
            }
        )
        return deltas, details, structured_errors

    for index, wav_path in enumerate(trial_wavs, start=1):
        print(
            f"[B1] trial {index}/{len(trial_wavs)}: {wav_path.name}",
            file=sys.stderr,
            flush=True,
        )
        try:
            result = await measure_feed_audio_trial(
                ws_url,
                wav_path,
                device_id=device_id,
                device_mac=device_mac,
                client_id=client_id,
                authorization=authorization,
                trial_timeout=trial_timeout,
            )
            deltas.append(float(result["delta_ms"]))
            details.append({"trial": index, **result, "source": "feed_audio"})
        except Exception as exc:
            structured_errors.append(
                {
                    "category": classify_feed_error(exc),
                    "message": f"trial {index} ({wav_path.name}): {exc}",
                    "hint": feed_error_hint(exc),
                }
            )
            if classify_feed_error(exc) in {"server_unreachable", "missing_dependency"}:
                break

    return deltas, details, structured_errors


def classify_feed_error(exc: Exception) -> str:
    text = str(exc).lower()
    if isinstance(exc, ImportError):
        return "missing_dependency"
    if isinstance(exc, FileNotFoundError):
        return "missing_audio_fixture"
    if isinstance(exc, TimeoutError):
        return "tts_timeout"
    if "connection refused" in text or "connect" in text and "error" in text:
        return "server_unreachable"
    if "authentication" in text or "认证" in text:
        return "auth_failed"
    return "feed_audio_error"


def feed_error_hint(exc: Exception) -> str:
    category = classify_feed_error(exc)
    if category == "missing_dependency":
        return "pip install -r benchmark/requirements.txt (websockets, opuslib-next)"
    if category == "server_unreachable":
        return "Start xiaozhi-server and set VIAOCLAW_WS_URL if not on ws://127.0.0.1:8000/xiaozhi/v1/"
    if category == "tts_timeout":
        return "Server accepted audio but did not return TTS in time (check LLM/ASR/TTS config)"
    if category == "auth_failed":
        return "Set device token / allowed_devices or disable server.auth"
    return "See server logs for ASR/LLM/TTS errors"


def format_structured_errors(errors: list[dict[str, str]]) -> list[str]:
    formatted: list[str] = []
    for item in errors:
        line = f"[{item.get('category', 'error')}] {item.get('message', '')}"
        hint = item.get("hint")
        if hint:
            line += f" — {hint}"
        formatted.append(line)
    return formatted
