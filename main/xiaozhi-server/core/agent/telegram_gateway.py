from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import CommandStart
from aiogram.types import Message

from config.logger import setup_logging
from core.agent.outbound import TelegramOutbound
from core.agent.service import is_telegram_enabled, telegram_cfg

if TYPE_CHECKING:
    from core.agent.runtime import AgentRuntime

TAG = __name__
logger = setup_logging()


class TelegramGateway:
    def __init__(self, runtime: "AgentRuntime", config: dict):
        self.runtime = runtime
        self.config = config
        self.tg_cfg = telegram_cfg(config)
        self._bot: Bot | None = None
        self._dp: Dispatcher | None = None
        self._poll_task: asyncio.Task | None = None
        self._running = False

    @property
    def bot(self) -> Bot:
        if self._bot is None:
            raise RuntimeError("TelegramGateway chưa start()")
        return self._bot

    def _is_allowed(self, chat_id: int) -> bool:
        allowed = self.tg_cfg.get("allowed_chat_ids") or []
        if not allowed:
            return False
        return chat_id in allowed

    async def start(self) -> None:
        if self._running or not is_telegram_enabled(self.config):
            return
        token = str(self.tg_cfg.get("bot_token") or "").strip()
        if not token:
            logger.bind(tag=TAG).error("[xiaoclaw.telegram] bot_token trống, bỏ qua start")
            return

        proxy = self.tg_cfg.get("proxy")
        if proxy:
            session = AiohttpSession(proxy=str(proxy))
            self._bot = Bot(token=token, session=session)
        else:
            self._bot = Bot(token=token)

        self._dp = Dispatcher()
        self._register_handlers()
        self._running = True
        self._poll_task = asyncio.create_task(self._run_polling())
        await self._flush_pending_cron()
        logger.bind(tag=TAG).info("[xiaoclaw.telegram] gateway started (long polling)")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        if self._bot is not None:
            await self._bot.session.close()
            self._bot = None
        self._dp = None
        logger.bind(tag=TAG).info("[xiaoclaw.telegram] gateway stopped")

    def _register_handlers(self) -> None:
        assert self._dp is not None

        @self._dp.message(CommandStart())
        async def on_start(message: Message) -> None:
            chat_id = message.chat.id
            logger.bind(tag=TAG).info(
                f"[xiaoclaw.telegram] /start chat_id={chat_id}"
            )
            if not self._is_allowed(chat_id):
                return
            await message.answer(
                "Xin chào! Gửi tin nhắn text hoặc voice note để trò chuyện."
            )

        @self._dp.message(F.text)
        async def on_text(message: Message) -> None:
            await self._on_text(message)

        @self._dp.message(F.voice | F.audio)
        async def on_voice(message: Message) -> None:
            await self._on_voice(message)

    async def _run_polling(self) -> None:
        assert self._bot is not None and self._dp is not None
        try:
            await self._dp.start_polling(
                self._bot,
                handle_signals=False,
                allowed_updates=["message"],
            )
        except asyncio.CancelledError:
            await self._dp.stop_polling()
            raise
        except Exception as exc:
            logger.bind(tag=TAG).error(
                f"[xiaoclaw.telegram] polling error: {exc}", exc_info=True
            )

    async def _on_text(self, message: Message) -> None:
        chat_id = message.chat.id
        if not self._is_allowed(chat_id):
            logger.bind(tag=TAG).debug(
                f"[xiaoclaw.telegram] ignored chat_id={chat_id} (not in whitelist)"
            )
            return
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            if text == "/start":
                return
            if text:
                await message.answer(text)
            return

        session_key = f"telegram:{chat_id}"
        outbound = TelegramOutbound(self.bot, chat_id, self.tg_cfg)
        try:
            await self.runtime.dispatch(
                session_key,
                text,
                outbound=outbound,
                conn=None,
                channel="telegram",
                chat_id=str(chat_id),
                source="user",
            )
            await outbound.flush()
        except Exception as exc:
            logger.bind(tag=TAG).error(
                f"[xiaoclaw.telegram] dispatch failed chat_id={chat_id}: {exc}",
                exc_info=True,
            )
            await message.answer("Xin lỗi, xử lý tin nhắn thất bại. Thử lại sau.")

    async def _on_voice(self, message: Message) -> None:
        chat_id = message.chat.id
        if not self._is_allowed(chat_id):
            return
        if self.runtime.asr is None:
            await message.answer("ASR chưa được cấu hình trên server.")
            return

        from core.agent.telegram_asr import TelegramAsrQueue

        asr_queue = TelegramAsrQueue.get(self.runtime)
        try:
            text = await asr_queue.transcribe_message(self.bot, message)
        except Exception as exc:
            logger.bind(tag=TAG).error(
                f"[xiaoclaw.telegram] ASR failed chat_id={chat_id}: {exc}",
                exc_info=True,
            )
            await message.answer("Không nhận dạng được giọng nói. Thử lại.")
            return

        if not text or not text.strip():
            await message.answer("Không nghe rõ, bạn nói lại nhé.")
            return

        query = f"[voice] {text.strip()}"
        session_key = f"telegram:{chat_id}"
        outbound = TelegramOutbound(self.bot, chat_id, self.tg_cfg)
        try:
            await self.runtime.dispatch(
                session_key,
                query,
                outbound=outbound,
                conn=None,
                channel="telegram",
                chat_id=str(chat_id),
                source="user",
            )
            await outbound.flush()
        except Exception as exc:
            logger.bind(tag=TAG).error(
                f"[xiaoclaw.telegram] voice dispatch failed chat_id={chat_id}: {exc}",
                exc_info=True,
            )
            await message.answer("Xin lỗi, xử lý voice note thất bại.")

    async def send_message(self, chat_id: str | int, text: str) -> None:
        if not text:
            return
        outbound = TelegramOutbound(self.bot, chat_id, self.tg_cfg)
        sentence_id = "cron"
        outbound.on_first(sentence_id)
        outbound.on_chunk(sentence_id, text)
        outbound.on_last(sentence_id)
        await outbound.flush()

    async def _flush_pending_cron(self) -> None:
        try:
            from core.cron.service import get_cron_service

            pending_store = get_cron_service().fire_handler.pending_store
        except RuntimeError:
            return
        items = pending_store.pop_for_channel("telegram", limit=20)
        if not items:
            return
        logger.bind(tag=TAG).info(
            f"[xiaoclaw.telegram] flushing {len(items)} pending cron items"
        )
        for item in items:
            text = item.get("text") or ""
            target_id = item.get("target_id") or ""
            mode = item.get("mode", "tts")
            if not target_id or not text:
                continue
            try:
                if mode == "chat":
                    await self._dispatch_cron_chat(target_id, text)
                else:
                    await self.send_message(target_id, text)
            except Exception as exc:
                logger.bind(tag=TAG).error(
                    f"[xiaoclaw.telegram] pending flush failed target={target_id}: {exc}"
                )

    async def _dispatch_cron_chat(self, chat_id: str, text: str) -> None:
        outbound = TelegramOutbound(self.bot, chat_id, self.tg_cfg)
        await self.runtime.dispatch(
            f"telegram:{chat_id}",
            text,
            outbound=outbound,
            conn=None,
            channel="telegram",
            chat_id=str(chat_id),
            source="cron_pending",
        )
        await outbound.flush()
