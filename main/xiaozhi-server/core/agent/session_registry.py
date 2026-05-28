import time
from typing import TYPE_CHECKING

from config.logger import setup_logging

if TYPE_CHECKING:
    from core.agent.session import ChatSession

TAG = __name__
logger = setup_logging()


class SessionRegistry:
    def __init__(self, config: dict):
        self._config = config
        self._sessions: dict[str, "ChatSession"] = {}
        self._last_active: dict[str, float] = {}

    def get(self, session_key: str) -> "ChatSession | None":
        return self._sessions.get(session_key)

    def get_or_create(
        self,
        session_key: str,
        *,
        channel: str,
        device_id: str | None = None,
        **kwargs,
    ) -> "ChatSession":
        from core.agent.session import ChatSessionFactory

        existing = self._sessions.get(session_key)
        if existing is not None:
            self._last_active[session_key] = time.time()
            return existing

        session = ChatSessionFactory.create(
            session_key,
            channel=channel,
            device_id=device_id,
            config=self._config,
            **kwargs,
        )
        self._sessions[session_key] = session
        self._last_active[session_key] = time.time()
        logger.bind(tag=TAG).info(
            f"[xiaoclaw.session] created session_key={session_key} channel={channel}"
        )
        return session

    def touch(self, session_key: str) -> None:
        if session_key in self._sessions:
            self._last_active[session_key] = time.time()

    def evict_idle(self, max_age_seconds: float) -> int:
        return self.evict_idle_by_channel(
            voice_max_age=max_age_seconds,
            telegram_max_age=max_age_seconds,
        )

    def evict_idle_by_channel(
        self,
        *,
        voice_max_age: float,
        telegram_max_age: float,
    ) -> int:
        now = time.time()
        evicted = 0
        for key in list(self._sessions.keys()):
            session = self._sessions.get(key)
            if session is None:
                continue
            if key.startswith("telegram:"):
                max_age = telegram_max_age
            else:
                max_age = voice_max_age
            if max_age <= 0:
                continue
            last = self._last_active.get(key, now)
            if now - last >= max_age:
                self._sessions.pop(key, None)
                self._last_active.pop(key, None)
                session.on_evict()
                evicted += 1
                logger.bind(tag=TAG).info(
                    f"[xiaoclaw.session] evicted session_key={key}"
                )
        return evicted
