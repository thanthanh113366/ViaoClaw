from __future__ import annotations

import asyncio
import os
import tempfile
from typing import TYPE_CHECKING, Optional

import aiohttp
import httpx
from pydub import AudioSegment

from config.logger import setup_logging

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message
    from core.agent.runtime import AgentRuntime

TAG = __name__
logger = setup_logging()

_instances: dict[int, "TelegramAsrQueue"] = {}


class TelegramAsrQueue:
    """Single-worker ASR queue — serialize Telegram voice transcription."""

    def __init__(self, runtime: "AgentRuntime"):
        self.runtime = runtime
        self._queue: asyncio.Queue[tuple[asyncio.Future, Bot, Message]] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        tg_cfg = (runtime.config.get("xiaoclaw") or {}).get("telegram") or {}
        self._timeout = float(tg_cfg.get("asr_timeout_seconds") or 60)
        proxy = tg_cfg.get("proxy")
        self._proxy = str(proxy).strip() if proxy else None

    @classmethod
    def get(cls, runtime: "AgentRuntime") -> "TelegramAsrQueue":
        key = id(runtime)
        inst = _instances.get(key)
        if inst is None:
            inst = cls(runtime)
            _instances[key] = inst
        return inst

    def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())

    async def transcribe_message(self, bot: "Bot", message: "Message") -> str:
        self._ensure_worker()
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        await self._queue.put((future, bot, message))
        return await asyncio.wait_for(future, timeout=self._timeout)

    async def _worker(self) -> None:
        while True:
            future, bot, message = await self._queue.get()
            try:
                text = await self._transcribe_one(bot, message)
                if not future.done():
                    future.set_result(text)
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)
            finally:
                self._queue.task_done()

    async def _download_file(self, url: str) -> bytes:
        if self._proxy:
            from aiohttp_socks import ProxyConnector

            connector = ProxyConnector.from_url(self._proxy)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    resp.raise_for_status()
                    return await resp.read()

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content

    async def _transcribe_one(self, bot: "Bot", message: "Message") -> str:
        file_id = None
        if message.voice is not None:
            file_id = message.voice.file_id
        elif message.audio is not None:
            file_id = message.audio.file_id
        if not file_id:
            return ""

        tg_file = await bot.get_file(file_id)
        if tg_file.file_path is None:
            raise RuntimeError("Telegram file path trống")

        file_url = f"https://api.telegram.org/file/bot{bot.token}/{tg_file.file_path}"
        suffix = os.path.splitext(tg_file.file_path)[1] or ".ogg"
        temp_path = None
        wav_path = None
        try:
            content = await self._download_file(file_url)
            with tempfile.NamedTemporaryFile(
                suffix=suffix, delete=False
            ) as tmp:
                tmp.write(content)
                temp_path = tmp.name

            audio = AudioSegment.from_file(temp_path)
            audio = audio.set_channels(1).set_frame_rate(16000)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_tmp:
                wav_path = wav_tmp.name
            audio.export(wav_path, format="wav")

            pcm_bytes = audio.raw_data
            pcm_chunks = [
                pcm_bytes[i : i + 3200] for i in range(0, len(pcm_bytes), 3200)
            ] or [pcm_bytes]
            session_id = f"telegram:{message.chat.id}"
            asr = self.runtime.asr
            if asr is None:
                raise RuntimeError("ASR module chưa khởi tạo")

            from core.providers.asr.base import ASRProviderBase

            artifacts = ASRProviderBase.AudioArtifacts(
                pcm_frames=pcm_chunks,
                pcm_bytes=pcm_bytes,
                file_path=wav_path,
                temp_path=None,
            )
            text, _ = await asr.speech_to_text(
                pcm_chunks,
                session_id,
                audio_format="pcm",
                artifacts=artifacts,
            )
            return (text or "").strip()
        finally:
            for path in (temp_path, wav_path):
                if path and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
