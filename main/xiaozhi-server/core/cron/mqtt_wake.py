"""MQTT wake publisher for hybrid cron delivery.

Firmware contract (wake_only mode, implemented outside this repo):
- OTA returns mqtt.mode == "wake_only" alongside websocket config.
- ESP stays on WebSocket for chat/audio; MQTT is idle subscribe only.
- On JSON {"type": "wake", ...} on subscribe_topic, call the same handler
  as local wakeword → StartWebSocketSession() to xiaozhi-server.
- If a WS session is already active, ignore duplicate wake or queue (device choice).
- Do NOT switch to hello-over-MQTT / UDP transport (full mqtt_gateway mode).
"""

from __future__ import annotations

import json
import time
from typing import Any

from config.logger import setup_logging

TAG = __name__
logger = setup_logging()


def normalize_device_id(device_id: str) -> str:
    return (device_id or "").replace(":", "_").strip()


def format_topic(template: str, device_id: str) -> str:
    return template.format(device_id=normalize_device_id(device_id))


def is_mqtt_wake_enabled(config: dict) -> bool:
    server_cfg = (config or {}).get("server") or {}
    wake_cfg = server_cfg.get("mqtt_wake") or {}
    cron_cfg = (config or {}).get("cron") or {}
    cron_wake = cron_cfg.get("mqtt_wake") or {}
    return bool(wake_cfg.get("enabled") and cron_wake.get("enabled", True))


def get_cron_mqtt_wake_config(config: dict) -> dict:
    cron_cfg = (config or {}).get("cron") or {}
    return cron_cfg.get("mqtt_wake") or {}


class MqttWakePublisher:
    def __init__(self, config: dict):
        self.config = config
        self.server_cfg = (config or {}).get("server") or {}
        self.wake_cfg = self.server_cfg.get("mqtt_wake") or {}

    def publish_wake(
        self,
        device_id: str,
        *,
        job_id: str | None = None,
        reason: str = "cron",
    ) -> bool:
        if not device_id:
            logger.bind(tag=TAG).warning("[mqtt_wake] publish skipped: empty device_id")
            return False

        broker = (self.wake_cfg.get("broker") or "").strip()
        if not broker:
            logger.bind(tag=TAG).error("[mqtt_wake] broker not configured")
            return False

        host, port = self._parse_broker(broker)
        topic = format_topic(
            self.wake_cfg.get("topic_template") or "xiaozhi/wake/{device_id}",
            device_id,
        )
        payload = {
            "type": "wake",
            "source": reason,
            "job_id": job_id,
            "ts": int(time.time()),
        }
        qos = int(self.wake_cfg.get("qos") or 1)
        timeout = float(self.wake_cfg.get("connect_timeout_seconds") or 5)

        try:
            import paho.mqtt.publish as mqtt_publish
        except ImportError as exc:
            logger.bind(tag=TAG).error(f"[mqtt_wake] paho-mqtt not installed: {exc}")
            return False

        auth = None
        username = self.wake_cfg.get("username")
        password = self.wake_cfg.get("password")
        if username:
            auth = {"username": str(username), "password": str(password or "")}

        try:
            mqtt_publish.single(
                topic,
                payload=json.dumps(payload, ensure_ascii=False),
                hostname=host,
                port=port,
                qos=qos,
                retain=False,
                auth=auth,
                keepalive=60,
            )
            logger.bind(tag=TAG).info(
                f"[mqtt_wake] published wake device={device_id} topic={topic} job_id={job_id}"
            )
            return True
        except Exception as exc:
            logger.bind(tag=TAG).error(
                f"[mqtt_wake] publish failed device={device_id} topic={topic}: {exc}"
            )
            return False

    @staticmethod
    def _parse_broker(broker: str) -> tuple[str, int]:
        if ":" in broker:
            host, port_str = broker.rsplit(":", 1)
            return host.strip(), int(port_str)
        return broker.strip(), 1883


def build_wake_only_mqtt_config(
    *,
    device_id: str,
    client_id: str,
    device_model: str,
    server_config: dict,
    generate_password_signature,
) -> dict[str, Any]:
    """Build OTA mqtt block for hybrid wake_only mode."""
    import base64

    wake_cfg = server_config.get("mqtt_wake") or {}
    mac_safe = normalize_device_id(device_id)
    group_id = f"GID_{device_model}".replace(":", "_").replace(" ", "_")

    mqtt_client_id = client_id or f"{group_id}@@@{mac_safe}@@@{mac_safe}"

    broker_username = (wake_cfg.get("username") or "").strip()
    if broker_username:
        username = broker_username
        password = str(wake_cfg.get("password") or "")
    else:
        user_data = {"ip": "unknown"}
        username = base64.b64encode(json.dumps(user_data).encode("utf-8")).decode(
            "utf-8"
        )
        password = ""
        signature_key = server_config.get("mqtt_signature_key") or ""
        if signature_key:
            password = generate_password_signature(
                mqtt_client_id + "|" + username, signature_key
            )

    subscribe_topic = format_topic(
        wake_cfg.get("topic_template") or "xiaozhi/wake/{device_id}",
        device_id,
    )
    publish_topic = format_topic(
        wake_cfg.get("publish_topic_template") or "xiaozhi/device-server/{device_id}",
        device_id,
    )

    return {
        "mode": "wake_only",
        "endpoint": (wake_cfg.get("broker") or "").strip(),
        "client_id": mqtt_client_id,
        "username": username,
        "password": password or "",
        "subscribe_topic": subscribe_topic,
        "publish_topic": publish_topic,
    }
