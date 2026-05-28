from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.connection import ConnectionHandler


def submit_agent_turn(
    conn: "ConnectionHandler",
    session_key: str,
    text: str,
    *,
    source: str = "cron",
) -> None:
    from core.agent.service import get_agent_runtime_optional, is_agent_enabled
    from core.handle.agentOutbound import VoiceOutbound

    if not is_agent_enabled(conn.config):
        conn.executor.submit(conn.chat, text)
        return

    runtime = get_agent_runtime_optional()
    if runtime is None or conn.loop is None:
        conn.executor.submit(conn.chat, text)
        return

    outbound = VoiceOutbound(conn)
    coro = runtime.dispatch(
        session_key,
        text,
        outbound=outbound,
        conn=conn,
        source=source,
    )
    asyncio.run_coroutine_threadsafe(coro, conn.loop)
