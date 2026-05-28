from __future__ import annotations

import html
from typing import Protocol


class OutboundSink(Protocol):
    def on_first(self, sentence_id: str) -> None: ...

    def on_chunk(self, sentence_id: str, text: str) -> None: ...

    def on_last(self, sentence_id: str) -> None: ...


def escape_telegram_html(text: str) -> str:
    return html.escape(text or "")


def split_telegram_message(text: str, max_length: int) -> list[str]:
    if max_length <= 0:
        max_length = 4096
    text = text or ""
    if len(text) <= max_length:
        return [text] if text else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_length, len(text))
        chunks.append(text[start:end])
        start = end
    return chunks


class TelegramOutbound:
    """Buffer LLM text chunks and flush to Telegram send_message."""

    def __init__(self, bot, chat_id: str | int, tg_cfg: dict):
        self._bot = bot
        self._chat_id = chat_id
        self._tg_cfg = tg_cfg
        self._buffer: list[str] = []
        self._current_sentence_id: str | None = None
        self._sent_typing = False

    def on_first(self, sentence_id: str) -> None:
        self._current_sentence_id = sentence_id
        self._buffer = []

    def on_chunk(self, sentence_id: str, text: str) -> None:
        if text:
            self._buffer.append(text)

    def on_last(self, sentence_id: str) -> None:
        pass

    async def flush(self) -> None:
        text = "".join(self._buffer).strip()
        self._buffer = []
        if not text:
            return
        max_len = int(self._tg_cfg.get("max_message_length") or 4096)
        parse_mode = self._tg_cfg.get("parse_mode")
        for chunk in split_telegram_message(text, max_len):
            payload_text = chunk
            if parse_mode == "HTML":
                payload_text = escape_telegram_html(chunk)
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=payload_text,
                parse_mode=parse_mode if parse_mode else None,
            )
