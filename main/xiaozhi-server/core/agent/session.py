from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Optional

from config.logger import setup_logging
from core.utils.dialogue import Dialogue

if TYPE_CHECKING:
    from core.agent.outbound import OutboundSink
    from core.agent.runtime import AgentRuntime
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()


class ChatSession:
    def __init__(
        self,
        session_key: str,
        *,
        channel: str,
        device_id: str | None = None,
        dialogue: Dialogue | None = None,
        memory: Any = None,
        llm: Any = None,
        intent_type: str | None = None,
        prompt: str | None = None,
    ):
        self.session_key = session_key
        self.channel = channel
        self.device_id = device_id
        self.dialogue = dialogue or Dialogue()
        self.memory = memory
        self.llm = llm
        self.intent_type = intent_type
        self.prompt = prompt
        self._initialized = False

    async def run_turn(
        self,
        query: str,
        *,
        outbound: "OutboundSink",
        runtime: "AgentRuntime",
        conn: Optional["ConnectionHandler"] = None,
        source: str = "user",
    ) -> Any:
        from core.agent.context import build_turn_context

        proxy = runtime.conn_proxy
        proxy.live_voice_conn = conn
        proxy.session = self
        proxy.channel = self.channel
        try:
            ctx = build_turn_context(
                session=self,
                outbound=outbound,
                runtime=runtime,
                conn=conn,
            )
            logger.bind(tag=TAG).info(
                f"[xiaoclaw.agent] run_turn session_key={self.session_key} "
                f"source={source} query={(query or '')[:80]!r}"
            )
            executor = conn.executor if conn is not None else runtime.executor
            loop = conn.loop if conn is not None else runtime.loop
            if executor is None or loop is None:
                raise RuntimeError("AgentRuntime chưa start() — thiếu loop/executor")
            return await loop.run_in_executor(
                executor,
                runtime.engine.chat_sync,
                query,
                ctx,
            )
        finally:
            proxy.live_voice_conn = None
            proxy.session = None
            proxy.channel = None

    def on_evict(self) -> None:
        if self.memory is None:
            return
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(
                self.memory.save_memory(self.dialogue.dialogue, self.session_key)
            )
        except Exception as exc:
            logger.bind(tag=TAG).error(
                f"[xiaoclaw.session] memory save on evict failed: {exc}"
            )
        finally:
            try:
                loop.close()
            except Exception:
                pass


class ChatSessionFactory:
    @staticmethod
    def create(
        session_key: str,
        *,
        channel: str,
        device_id: str | None,
        config: dict,
        conn: Optional["ConnectionHandler"] = None,
        runtime: Optional["AgentRuntime"] = None,
    ) -> ChatSession:
        dialogue = Dialogue()
        memory = None
        llm = None
        intent_type = None
        prompt = None

        if conn is not None:
            llm = conn.llm
            intent_type = conn.intent_type
            prompt = getattr(conn, "prompt", None)
            memory = conn.memory
            if memory is not None and device_id:
                memory.init_memory(
                    role_id=device_id,
                    llm=llm,
                    summary_memory=config.get("summaryMemory"),
                    save_to_file=not conn.read_config_from_api,
                )
            if prompt:
                dialogue.update_system_message(prompt)
            ChatSessionFactory._inject_tool_fewshot(conn, dialogue)
        elif runtime is not None:
            llm = runtime.llm
            intent_type = runtime.intent_type
            prompt = runtime.prompt
            memory = runtime.memory
            role_id = str(device_id) if device_id else session_key
            if memory is not None:
                memory.init_memory(
                    role_id=role_id,
                    llm=llm,
                    summary_memory=config.get("summaryMemory"),
                    save_to_file=not config.get("read_config_from_api", False),
                )
            if prompt:
                dialogue.update_system_message(prompt)
            ChatSessionFactory._inject_tool_fewshot_runtime(runtime, dialogue)

        session = ChatSession(
            session_key,
            channel=channel,
            device_id=device_id,
            dialogue=dialogue,
            memory=memory,
            llm=llm,
            intent_type=intent_type,
            prompt=prompt,
        )
        session._initialized = True
        return session

    @staticmethod
    def _inject_tool_fewshot(conn: "ConnectionHandler", dialogue: Dialogue) -> None:
        if conn.intent_type != "function_call":
            return
        func_handler = getattr(conn, "func_handler", None)
        runtime = None
        from core.agent.service import get_agent_runtime_optional, is_agent_enabled

        if is_agent_enabled(conn.config):
            runtime = get_agent_runtime_optional()
            if runtime is not None:
                func_handler = runtime.func_handler
        if func_handler is None:
            return
        ChatSessionFactory._append_tool_fewshot(func_handler, dialogue)

    @staticmethod
    def _inject_tool_fewshot_runtime(
        runtime: "AgentRuntime", dialogue: Dialogue
    ) -> None:
        if runtime.intent_type != "function_call":
            return
        func_handler = runtime.func_handler
        if func_handler is None:
            return
        ChatSessionFactory._append_tool_fewshot(func_handler, dialogue)

    @staticmethod
    def _append_tool_fewshot(func_handler, dialogue: Dialogue) -> None:
        tools = func_handler.get_functions()
        if not tools:
            return
        from core.utils.dialogue import Message

        da_tc_id = "fewshot_da_001"
        dialogue.put(
            Message(role="user", content="给我讲个故事吧", is_temporary=True)
        )
        dialogue.put(
            Message(
                role="assistant",
                tool_calls=[
                    {
                        "id": da_tc_id,
                        "function": {
                            "arguments": '{"response": "好呀，你想听什么类型的呀？童话、冒险还是搞笑的？选一个我给你开讲~"}',
                            "name": "direct_answer",
                        },
                        "type": "function",
                        "index": 0,
                    }
                ],
                is_temporary=True,
            )
        )
        dialogue.put(
            Message(
                role="tool",
                tool_call_id=da_tc_id,
                content="好呀，你想听什么类型的呀？童话、冒险还是搞笑的？选一个我给你开讲~",
                is_temporary=True,
            )
        )
