from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

LIVE_ASR_BASE_URL = "https://api.openai.com/v1/audio/transcriptions"
LIVE_ASR_MODEL = "gpt-4o-transcribe"
LIVE_ASR_LANGUAGE = "vi"
LIVE_ASR_PROMPT = (
    "Vietnamese smart-home commands. Preserve Vietnamese wording and do not translate "
    "room, device, or action names."
)
# Groq Whisper echoes English prompt text instead of transcribing (see Groq STT docs:
# use Vietnamese prompt or omit). Server GroqASR in .config.yaml sends no prompt.
LIVE_ASR_GROQ_PROMPT = "bật đèn, tắt đèn, phòng bếp, phòng ngủ, quạt, máy lạnh"
LIVE_ASR_TIMEOUT = 60.0


def _default_asr_prompt(base_url: str, bench: dict[str, Any]) -> str | None:
    if "prompt" in bench:
        raw = bench.get("prompt")
        return raw if raw else None
    if "groq.com" in str(base_url):
        return LIVE_ASR_GROQ_PROMPT
    return LIVE_ASR_PROMPT


def live_asr_request_options(
    api_key: str,
    sample: dict[str, Any] | None = None,
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sample = sample or {}
    bench = defaults or {}
    base_url = sample.get("asr_base_url", bench.get("asr_base_url", LIVE_ASR_BASE_URL))
    if "prompt" in sample:
        prompt = sample.get("prompt") or None
    else:
        prompt = _default_asr_prompt(base_url, bench)
    return {
        "api_key": str(bench.get("api_key") or api_key),
        "base_url": base_url,
        "model_name": sample.get("model_name", bench.get("model_name", LIVE_ASR_MODEL)),
        "language": sample.get("language", bench.get("language", LIVE_ASR_LANGUAGE)),
        "timeout": float(sample.get("timeout", bench.get("timeout", LIVE_ASR_TIMEOUT))),
        "prompt": prompt,
    }


async def transcribe_openai_whisper(
    audio_path: str | Path,
    *,
    api_key: str,
    base_url: str = LIVE_ASR_BASE_URL,
    model_name: str = LIVE_ASR_MODEL,
    language: str = LIVE_ASR_LANGUAGE,
    timeout: float = LIVE_ASR_TIMEOUT,
    prompt: str | None = LIVE_ASR_PROMPT,
) -> str:
    """Call OpenAI-compatible transcription API (same HTTP shape as ASRProvider.speech_to_text)."""
    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(f"WAV not found: {path}")
    if not api_key:
        raise ValueError("LLM_API_KEY is required for live ASR")

    data: dict[str, Any] = {"model": model_name, "language": language}
    if prompt:
        data["prompt"] = prompt

    headers = {"Authorization": f"Bearer {api_key}"}
    with path.open("rb") as handle:
        response = requests.post(
            base_url,
            files={"file": handle},
            data=data,
            headers=headers,
            timeout=timeout,
        )
    if response.status_code != 200:
        raise RuntimeError(f"ASR API failed: {response.status_code} - {response.text}")
    return str(response.json().get("text", "") or "")
