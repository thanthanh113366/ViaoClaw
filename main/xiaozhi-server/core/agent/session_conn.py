from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from core.agent.session import ChatSession
    from core.connection import ConnectionHandler


class SessionConnProxy:
    """Đứng in cho ConnectionHandler khi gọi plugin / tool executor."""

    def __init__(self, config: dict):
        self.config = config
        self.live_voice_conn: Optional["ConnectionHandler"] = None
        self.session: Optional["ChatSession"] = None
        self.channel: str | None = None
        self.tool_context: Any = None

    @property
    def device_id(self) -> str | None:
        if self.live_voice_conn is not None:
            return self.live_voice_conn.device_id
        if self.session is not None:
            return self.session.device_id
        return None

    @property
    def loop(self):
        if self.live_voice_conn is not None:
            return self.live_voice_conn.loop
        return None

    @property
    def executor(self):
        if self.live_voice_conn is not None:
            return self.live_voice_conn.executor
        return None

    def __getattr__(self, name: str) -> Any:
        if self.live_voice_conn is not None:
            return getattr(self.live_voice_conn, name)
        raise AttributeError(name)
