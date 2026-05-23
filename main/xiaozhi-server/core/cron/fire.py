from typing import TYPE_CHECKING, Optional

from config.logger import setup_logging
from core.cron.mqtt_wake import (
    MqttWakePublisher,
    get_cron_mqtt_wake_config,
    is_mqtt_wake_enabled,
)
from core.cron.registry import ConnectionRegistry
from core.cron.runner import ExecRunner
from core.cron.store import PendingStore

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()


class CronFireHandler:
    def __init__(
        self,
        registry: ConnectionRegistry,
        pending_store: PendingStore,
        exec_runner: ExecRunner,
        config: dict | None = None,
    ):
        self.registry = registry
        self.pending_store = pending_store
        self.exec_runner = exec_runner
        self.config = config or {}
        self._mqtt_wake = (
            MqttWakePublisher(self.config) if is_mqtt_wake_enabled(self.config) else None
        )

    def handle(self, job: dict) -> None:
        payload = job.get("payload") or {}
        job_id = job.get("id", "")
        channel = payload.get("channel") or "xiaozhi"
        target_id = payload.get("to") or ""
        message = payload.get("message") or ""
        command = payload.get("command") or ""
        deliver = bool(payload.get("deliver"))

        if channel != "xiaozhi":
            raise RuntimeError(f"unsupported channel: {channel}")

        logger.bind(tag=TAG).info(
            f"[cron] fire job id={job_id} deliver={deliver} command={bool(command)} target={target_id}"
        )

        if command:
            try:
                output = self.exec_runner.run(command)
            except Exception as exc:
                output = f"Error executing scheduled command: {exc}"
            self._deliver_tts(target_id, output, job_id=job_id)
            return

        if deliver:
            self._deliver_tts(target_id, message, job_id=job_id)
            return

        chat_text = f"[cron {job_id}] {message}"
        conn = self._resolve_connection(target_id, job_id=job_id)
        if conn is None:
            self.pending_store.append(
                channel=channel,
                target_id=target_id,
                text=chat_text,
                mode="chat",
                job_id=job_id,
            )
            return
        conn.executor.submit(conn.chat, chat_text)

    def _deliver_tts(
        self, target_id: str, text: str, *, job_id: str | None = None
    ) -> None:
        conn = self._resolve_connection(target_id, job_id=job_id)
        if conn is None:
            self.pending_store.append(
                channel="xiaozhi",
                target_id=target_id,
                text=text,
                mode="tts",
                job_id=job_id,
            )
            return
        from core.handle.intentHandler import speak_txt

        conn.executor.submit(speak_txt, conn, text)

    def _resolve_connection(
        self, target_id: str, *, job_id: str | None = None
    ) -> Optional["ConnectionHandler"]:
        conn = self.registry.get(target_id)
        if conn is not None:
            return conn

        if self._mqtt_wake is None:
            return None

        wake_cfg = get_cron_mqtt_wake_config(self.config)
        wait_register = float(wake_cfg.get("wait_register_seconds") or 15)
        wait_tts = float(wake_cfg.get("wait_tts_ready_seconds") or 8)

        published = self._mqtt_wake.publish_wake(target_id, job_id=job_id, reason="cron")
        if not published:
            logger.bind(tag=TAG).warning(
                f"[cron] mqtt wake publish failed target={target_id} job_id={job_id}"
            )
            return None

        conn = self.registry.wait_for_device(
            target_id,
            wait_register,
            require_tts=False,
        )
        if conn is None:
            logger.bind(tag=TAG).info(
                f"[cron] mqtt wake timeout register target={target_id} job_id={job_id}"
            )
            return None

        if getattr(conn, "tts", None) is None and wait_tts > 0:
            conn = self.registry.wait_for_device(
                target_id,
                wait_tts,
                require_tts=True,
            ) or conn

        logger.bind(tag=TAG).info(
            f"[cron] mqtt wake connected target={target_id} job_id={job_id}"
        )
        return conn
