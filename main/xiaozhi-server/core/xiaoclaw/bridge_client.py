"""HTTP client for xiaoclaw-bridge."""

from __future__ import annotations

from typing import Any

import aiohttp
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()


def _xiaoclaw_config(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("xiaoclaw") or {}


def is_enabled(config: dict[str, Any]) -> bool:
    return bool(_xiaoclaw_config(config).get("enabled"))


async def request_reply(
    config: dict[str, Any],
    *,
    device_id: str | None,
    text: str,
    session_id: str | None = None,
) -> str:
    """POST /v1/utterance; raise on failure."""
    xc = _xiaoclaw_config(config)
    url = (xc.get("bridge_url") or "http://127.0.0.1:8787/v1/utterance").strip()
    timeout = float(xc.get("timeout_seconds") or 90)
    device_id = device_id or "unknown"
    if session_id is None:
        session_id = f"xiaozhi:{device_id}"

    payload = {
        "device_id": device_id,
        "text": text,
        "session_id": session_id,
    }
    client_timeout = aiohttp.ClientTimeout(total=timeout + 5)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.post(url, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                return str(data.get("reply") or "")
            body = await resp.text()
            logger.bind(tag=TAG).error(
                "xiaoclaw bridge error status=%s body=%s",
                resp.status,
                body[:500],
            )
            if resp.status == 504:
                return "Xin lỗi, em đang xử lý lâu quá. Anh chị thử lại sau nhé."
            if resp.status >= 500:
                return "Xin lỗi, em chưa kết nối được với trợ lý. Anh chị kiểm tra bridge giúp em."
            return "Xin lỗi, em chưa hiểu yêu cầu đó."
