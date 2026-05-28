import asyncio
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.agent.outbound import OutboundSink
    from core.agent.runtime import AgentRuntime
    from core.connection import ConnectionHandler


def _parse_session_key(session_key: str) -> tuple[str | None, str | None]:
    if session_key.startswith("telegram:"):
        return None, session_key.split(":", 1)[1]
    if session_key.startswith("xiaozhi:"):
        return session_key.split(":", 1)[1], None
    return session_key, None


class InboundDispatcher:
    def __init__(self, runtime: "AgentRuntime"):
        self._runtime = runtime
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, session_key: str) -> asyncio.Lock:
        lock = self._locks.get(session_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_key] = lock
        return lock

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
        runtime = self._runtime
        turn_timeout = runtime.turn_timeout_seconds
        parsed_device_id, parsed_chat_id = _parse_session_key(session_key)
        device_id = parsed_device_id
        if chat_id is None:
            chat_id = parsed_chat_id
        if device_id is None and conn is not None:
            device_id = conn.device_id
        if device_id is None and chat_id is not None:
            device_id = chat_id

        async with self._lock_for(session_key):
            session = runtime.session_registry.get_or_create(
                session_key,
                channel=channel,
                device_id=device_id,
                conn=conn,
                runtime=runtime,
            )
            runtime.session_registry.touch(session_key)
            coro = session.run_turn(
                text,
                outbound=outbound,
                runtime=runtime,
                conn=conn,
                source=source,
            )
            if turn_timeout and turn_timeout > 0:
                await asyncio.wait_for(coro, timeout=turn_timeout)
            else:
                await coro
