import threading
import time
from typing import TYPE_CHECKING, Optional

from config.logger import setup_logging
from core.cron.store import PendingStore

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

_registry: Optional["ConnectionRegistry"] = None


def init_connection_registry(config: dict) -> "ConnectionRegistry":
    global _registry
    _registry = ConnectionRegistry(config)
    return _registry


def get_connection_registry() -> Optional["ConnectionRegistry"]:
    return _registry


class ConnectionRegistry:
    def __init__(self, config: dict):
        self._lock = threading.Lock()
        self._connections: dict[str, "ConnectionHandler"] = {}
        self.pending_store = PendingStore(config)

    def register(self, device_id: str | None, conn: "ConnectionHandler") -> None:
        if not device_id:
            return
        with self._lock:
            self._connections[device_id] = conn
        logger.bind(tag=TAG).info(f"[cron] registered device_id={device_id}")
        self._flush_pending(device_id, conn)

    def unregister(self, device_id: str | None, conn: "ConnectionHandler") -> None:
        if not device_id:
            return
        with self._lock:
            current = self._connections.get(device_id)
            if current is conn:
                del self._connections[device_id]
                logger.bind(tag=TAG).info(
                    f"[cron] unregistered device_id={device_id}"
                )

    def get(self, device_id: str | None) -> Optional["ConnectionHandler"]:
        if not device_id:
            return None
        with self._lock:
            return self._connections.get(device_id)

    def wait_for_device(
        self,
        device_id: str | None,
        timeout: float,
        *,
        require_tts: bool = True,
        poll_interval: float = 0.2,
    ) -> Optional["ConnectionHandler"]:
        if not device_id or timeout <= 0:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            conn = self.get(device_id)
            if conn is not None:
                if not require_tts or getattr(conn, "tts", None) is not None:
                    return conn
            time.sleep(poll_interval)
        return None

    def _flush_pending(self, device_id: str, conn: "ConnectionHandler") -> None:
        items = self.pending_store.pop_for_device(device_id, limit=5)
        if not items:
            return
        logger.bind(tag=TAG).info(
            f"[cron] flushed {len(items)} pending for {device_id}"
        )
        from core.handle.intentHandler import speak_txt

        for item in items:
            text = item.get("text") or ""
            mode = item.get("mode", "tts")
            if mode == "chat":
                from core.agent.cron_bridge import submit_agent_turn

                submit_agent_turn(
                    conn, f"xiaozhi:{device_id}", text, source="cron_pending"
                )
            else:
                conn.executor.submit(speak_txt, conn, text)
