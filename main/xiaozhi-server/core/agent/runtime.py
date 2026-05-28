from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Optional

from config.logger import setup_logging
from core.agent.dispatcher import InboundDispatcher
from core.agent.service import is_telegram_enabled
from core.agent.session_conn import SessionConnProxy
from core.agent.session_registry import SessionRegistry
from core.agent.chat_engine import ChatEngine

if TYPE_CHECKING:
    from core.agent.outbound import OutboundSink
    from core.agent.telegram_gateway import TelegramGateway
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()


class AgentRuntime:
    def __init__(self, config: dict):
        self.config = config
        self.session_registry = SessionRegistry(config)
        self.conn_proxy = SessionConnProxy(config)
        self.engine = ChatEngine()
        self.func_handler = None
        self.dispatcher = InboundDispatcher(self)
        self.telegram_gateway: Optional["TelegramGateway"] = None
        self._started = False
        xc = config.get("xiaoclaw") or {}
        agent_cfg = xc.get("agent") or {}
        self.turn_timeout_seconds = float(agent_cfg.get("turn_timeout_seconds") or 90)
        sessions_cfg = xc.get("sessions") or {}
        self.voice_ttl_hours = float(sessions_cfg.get("voice_ttl_hours") or 24)
        self.telegram_ttl_hours = float(
            sessions_cfg.get("telegram_ttl_hours") or 168
        )
        self._evict_task: Optional[asyncio.Task] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.executor: Optional[ThreadPoolExecutor] = None
        self.llm: Any = None
        self.memory: Any = None
        self.intent: Any = None
        self.intent_type: str = "nointent"
        self.prompt: str | None = None
        self.load_function_plugin = False
        self.asr: Any = None

    async def start(self) -> None:
        if self._started:
            return
        from core.providers.tools.unified_tool_handler import UnifiedToolHandler
        from core.utils.modules_initialize import initialize_modules

        self.loop = asyncio.get_running_loop()
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent")

        init_asr = is_telegram_enabled(self.config)
        modules = await self.loop.run_in_executor(
            None,
            initialize_modules,
            logger,
            self.config,
            False,
            init_asr,
            "LLM" in self.config.get("selected_module", {}),
            False,
            "Memory" in self.config.get("selected_module", {}),
            "Intent" in self.config.get("selected_module", {}),
        )
        self.llm = modules.get("llm")
        self.memory = modules.get("memory")
        self.intent = modules.get("intent")
        self.asr = modules.get("asr")
        self._init_intent_type()
        if self.intent_type in ("function_call", "intent_llm"):
            self.load_function_plugin = True
        self._init_prompt()

        from plugins_func.functions.hass_init import append_devices_to_prompt

        append_devices_to_prompt(self)

        self.func_handler = UnifiedToolHandler(self.conn_proxy)
        await self.func_handler._initialize()

        if is_telegram_enabled(self.config):
            from core.agent.telegram_gateway import TelegramGateway

            self.telegram_gateway = TelegramGateway(self, self.config)
            await self.telegram_gateway.start()

        self._wire_cron_handlers()

        self._started = True
        logger.bind(tag=TAG).info("[xiaoclaw.agent] runtime started")
        if self.voice_ttl_hours > 0 or self.telegram_ttl_hours > 0:
            self._evict_task = asyncio.create_task(self._evict_loop())

    def _init_intent_type(self) -> None:
        selected = self.config.get("selected_module") or {}
        intent_name = selected.get("Intent")
        if not intent_name:
            return
        intent_cfg = (self.config.get("Intent") or {}).get(intent_name) or {}
        self.intent_type = intent_cfg.get("type") or "nointent"
        if self.intent is None:
            return
        if self.intent_type == "nointent":
            return
        if self.intent_type == "intent_llm":
            intent_config = self.config.get("Intent") or {}
            intent_llm_name = intent_config.get(intent_name, {}).get("llm")
            if intent_llm_name and intent_llm_name in (self.config.get("LLM") or {}):
                from core.utils import llm as llm_utils

                memory_llm_config = self.config["LLM"][intent_llm_name]
                memory_llm_type = memory_llm_config.get("type", intent_llm_name)
                intent_llm = llm_utils.create_instance(
                    memory_llm_type, memory_llm_config
                )
                self.intent.set_llm(intent_llm)
            elif self.llm is not None:
                self.intent.set_llm(self.llm)
        elif self.llm is not None:
            self.intent.set_llm(self.llm)

    def _init_prompt(self) -> None:
        from core.utils.prompt_manager import PromptManager

        pm = PromptManager(self.config, logger)
        user_prompt = self.config.get("prompt") or ""
        self.prompt = pm.get_quick_prompt(user_prompt, device_id=None)

    def _wire_cron_handlers(self) -> None:
        try:
            from core.cron.service import get_cron_service

            svc = get_cron_service()
        except RuntimeError:
            return
        svc.fire_handler.agent_runtime = self
        if self.telegram_gateway is not None:
            svc.fire_handler.telegram_gateway = self.telegram_gateway

    async def stop(self) -> None:
        if self.telegram_gateway is not None:
            await self.telegram_gateway.stop()
            self.telegram_gateway = None
        if self._evict_task is not None:
            self._evict_task.cancel()
            try:
                await self._evict_task
            except asyncio.CancelledError:
                pass
            self._evict_task = None
        if self.executor is not None:
            self.executor.shutdown(wait=False)
            self.executor = None
        self._started = False
        logger.bind(tag=TAG).info("[xiaoclaw.agent] runtime stopped")

    async def _evict_loop(self) -> None:
        voice_max = self.voice_ttl_hours * 3600 if self.voice_ttl_hours > 0 else 0
        tg_max = self.telegram_ttl_hours * 3600 if self.telegram_ttl_hours > 0 else 0
        interval_candidates = [
            v for v in (voice_max, tg_max) if v > 0
        ]
        if not interval_candidates:
            return
        min_max = min(interval_candidates)
        interval = min(max(min_max / 4, 300), 3600)
        while True:
            await asyncio.sleep(interval)
            try:
                self.session_registry.evict_idle_by_channel(
                    voice_max_age=voice_max,
                    telegram_max_age=tg_max,
                )
            except Exception as exc:
                logger.bind(tag=TAG).error(
                    f"[xiaoclaw.session] evict loop error: {exc}"
                )

    async def dispatch(
        self,
        session_key: str,
        text: str,
        *,
        outbound: "OutboundSink",
        conn: Optional["ConnectionHandler"] = None,
        channel: str = "xiaozhi",
        chat_id: str | None = None,
        source: str = "user",
    ) -> None:
        logger.bind(tag=TAG).info(
            f"[xiaoclaw.agent] dispatch session_key={session_key} "
            f"channel={channel} source={source}"
        )
        await self.dispatcher.dispatch(
            session_key,
            text,
            outbound=outbound,
            conn=conn,
            channel=channel,
            chat_id=chat_id,
            source=source,
        )
