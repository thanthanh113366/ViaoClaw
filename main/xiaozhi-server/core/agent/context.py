from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from core.agent.outbound import OutboundSink
    from core.agent.runtime import AgentRuntime
    from core.agent.session import ChatSession
    from core.agent.session_conn import SessionConnProxy
    from core.connection import ConnectionHandler
    from core.providers.tools.unified_tool_handler import UnifiedToolHandler
    from core.utils.dialogue import Dialogue


@dataclass
class ChatTurnContext:
    dialogue: "Dialogue"
    llm: Any
    memory: Any
    func_handler: "UnifiedToolHandler"
    conn_proxy: "SessionConnProxy"
    outbound: "OutboundSink"
    abort_check: Callable[[], bool]
    llm_session_id: str
    config: dict
    intent_type: str
    loop: asyncio.AbstractEventLoop
    voice_conn: Optional["ConnectionHandler"]


def _is_agent_enabled(config: dict) -> bool:
    xc = config.get("xiaoclaw") or {}
    agent = xc.get("agent") or {}
    return bool(agent.get("enabled"))


def build_turn_context(
    *,
    session: "ChatSession",
    outbound: "OutboundSink",
    runtime: "AgentRuntime",
    conn: Optional["ConnectionHandler"] = None,
) -> ChatTurnContext:
    if conn is not None:
        func_handler = conn.func_handler
        if _is_agent_enabled(conn.config) and runtime.func_handler is not None:
            func_handler = runtime.func_handler
        memory = session.memory if session.memory is not None else conn.memory
        llm = session.llm if session.llm is not None else conn.llm
        intent_type = session.intent_type or conn.intent_type
        config = conn.config
        loop = conn.loop
        abort_check = lambda: conn.client_abort
    else:
        func_handler = runtime.func_handler
        memory = session.memory if session.memory is not None else runtime.memory
        llm = session.llm if session.llm is not None else runtime.llm
        intent_type = session.intent_type or runtime.intent_type
        config = runtime.config
        loop = runtime.loop
        abort_check = lambda: False

    return ChatTurnContext(
        dialogue=session.dialogue,
        llm=llm,
        memory=memory,
        func_handler=func_handler,
        conn_proxy=runtime.conn_proxy,
        outbound=outbound,
        abort_check=abort_check,
        llm_session_id=session.session_key,
        config=config,
        intent_type=intent_type,
        loop=loop,
        voice_conn=conn,
    )


def build_legacy_turn_context(
    conn: "ConnectionHandler",
    outbound: "OutboundSink",
) -> ChatTurnContext:
    from core.agent.session_conn import SessionConnProxy

    proxy = SessionConnProxy(conn.config)
    proxy.live_voice_conn = conn
    return ChatTurnContext(
        dialogue=conn.dialogue,
        llm=conn.llm,
        memory=conn.memory,
        func_handler=conn.func_handler,
        conn_proxy=proxy,
        outbound=outbound,
        abort_check=lambda: conn.client_abort,
        llm_session_id=conn.session_id,
        config=conn.config,
        intent_type=conn.intent_type,
        loop=conn.loop,
        voice_conn=conn,
    )
