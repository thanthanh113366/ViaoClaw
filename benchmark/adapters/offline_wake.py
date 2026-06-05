from __future__ import annotations

import json
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from benchmark.config import ensure_benchmark_import_stubs
from benchmark.stubs.recording import FakeVoiceConnection


@contextmanager
def _patched_delivery(on_deliver: Callable[[Any, str], None]):
    """Patch CronFireHandler._deliver_tts only (avoid intentHandler/MCP import chain)."""
    ensure_benchmark_import_stubs()
    from core.cron.fire import CronFireHandler

    original_deliver = CronFireHandler._deliver_tts

    def wrapped_deliver(self, target_id: str, text: str, *, job_id: str | None = None) -> None:
        conn = self._resolve_connection(target_id, job_id=job_id)  # noqa: SLF001
        if conn is not None:
            on_deliver(conn, text)
            return
        return original_deliver(self, target_id, text, job_id=job_id)

    CronFireHandler._deliver_tts = wrapped_deliver  # noqa: SLF001
    try:
        yield
    finally:
        CronFireHandler._deliver_tts = original_deliver  # noqa: SLF001


class SimulatedMqttWakePublisher:
    """Mock-mode wake publisher: synchronously simulates ESP32 reconnect on wake."""

    def __init__(
        self,
        on_wake: Callable[[str, str | None], None],
        *,
        register_delay_s: float = 0.0,
    ):
        self._on_wake = on_wake
        self._register_delay_s = register_delay_s

    def publish_wake(self, device_id: str, *, job_id: str | None = None, reason: str = "cron") -> bool:
        if self._register_delay_s > 0:
            threading.Timer(
                self._register_delay_s,
                lambda: self._on_wake(device_id, job_id),
            ).start()
        elif self._on_wake:
            self._on_wake(device_id, job_id)
        return True



def _offline_config(
    tmp: str,
    host: str,
    port: int,
    *,
    username: str | None = None,
    password: str | None = None,
) -> dict[str, Any]:
    wake_server: dict[str, Any] = {
        "enabled": True,
        "broker": f"{host}:{port}",
        "topic_template": "xiaozhi/wake/{device_id}",
    }
    if username:
        wake_server["username"] = username
        wake_server["password"] = password or ""
    return {
        "cron": {
            "enabled": True,
            "timezone": "Asia/Ho_Chi_Minh",
            "store_path": str(Path(tmp) / "jobs.json"),
            "pending_path": str(Path(tmp) / "pending.json"),
            "mqtt_wake": {
                "enabled": True,
                "wait_register_seconds": 3,
                "wait_tts_ready_seconds": 1,
            },
        },
        "server": {
            "mqtt_wake": wake_server,
        },
    }


def _force_offline(registry, device_id: str) -> None:
    with registry._lock:  # noqa: SLF001 - benchmark manipulates test registry state.
        registry._connections.pop(device_id, None)


def run_mock_offline_wake(n_trials: int, device_id: str) -> dict[str, Any]:
    ensure_benchmark_import_stubs()
    from core.cron.fire import CronFireHandler
    from core.cron.registry import ConnectionRegistry

    delivered = 0
    details: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="viaoclaw-offline-wake-") as tmp:
        config = _offline_config(tmp, "127.0.0.1", 1883)
        registry = ConnectionRegistry(config)
        pending = registry.pending_store
        fire_handler = CronFireHandler(registry, pending, _NoopExecRunner(), config)

        def on_wake(dev_id: str, _job_id: str | None) -> None:
            registry.register(dev_id, FakeVoiceConnection(dev_id))  # type: ignore[arg-type]

        publisher = SimulatedMqttWakePublisher(on_wake)
        fire_handler._mqtt_wake = publisher  # noqa: SLF001

        with _patched_delivery(lambda conn, text: conn.mark_delivered(text)):
            for index in range(n_trials):
                message = f"offline wake payload {index}"
                _force_offline(registry, device_id)
                started = time.perf_counter()
                job = {
                    "id": f"bench-offline-{index}",
                    "payload": {
                        "channel": "xiaozhi",
                        "to": device_id,
                        "message": message,
                        "deliver": True,
                        "command": "",
                    },
                }
                fire_handler.handle(job)
                latency_ms = (time.perf_counter() - started) * 1000
                conn = registry.get(device_id)
                got_delivery = bool(conn and message in conn.delivered)
                delivered += 1 if got_delivery else 0
                details.append(
                    {
                        "trial": index + 1,
                        "wake_received": True,
                        "reconnected": conn is not None,
                        "delivered": got_delivery,
                        "pending_stored": False,
                        "wake_latency_ms": round(latency_ms, 3),
                    }
                )

    latencies = [d["wake_latency_ms"] for d in details]
    return {
        "success_rate": delivered / max(1, n_trials),
        "mean_wake_latency_ms": sum(latencies) / len(latencies) if latencies else 0.0,
        "n_trials": n_trials,
        "details": details,
        "flow": "offline -> handle -> simulated mqtt wake -> register -> deliver",
        "errors": [],
    }


def run_live_offline_wake(
    *,
    n_trials: int,
    host: str,
    port: int,
    device_id: str,
    mqtt_username: str | None = None,
    mqtt_password: str | None = None,
) -> dict[str, Any]:
    from benchmark.tools.live_prereqs import check_mqtt

    mqtt_status = check_mqtt(host, port, username=mqtt_username, password=mqtt_password)
    if not mqtt_status.tcp.ok:
        return {
            "success_rate": 0.0,
            "mean_wake_latency_ms": None,
            "n_trials": n_trials,
            "details": [],
            "flow": "live MQTT wake (broker unreachable)",
            "errors": [f"MQTT broker TCP failed at {host}:{port}: {mqtt_status.tcp.error}"],
        }
    if mqtt_status.auth_ok is False:
        return {
            "success_rate": 0.0,
            "mean_wake_latency_ms": None,
            "n_trials": n_trials,
            "details": [],
            "flow": "live MQTT wake (auth rejected)",
            "errors": [mqtt_status.auth_error or "MQTT authentication failed"],
        }
    if mqtt_status.publish_ok is False:
        return {
            "success_rate": 0.0,
            "mean_wake_latency_ms": None,
            "n_trials": n_trials,
            "details": [],
            "flow": "live MQTT wake (publish rejected)",
            "errors": [mqtt_status.publish_error or "MQTT publish failed"],
        }

    ensure_benchmark_import_stubs()
    try:
        from core.cron.fire import CronFireHandler
        from core.cron.mqtt_wake import MqttWakePublisher, format_topic
        from core.cron.registry import ConnectionRegistry
        import paho.mqtt.client as mqtt
    except Exception as exc:
        return {
            "success_rate": 0.0,
            "mean_wake_latency_ms": None,
            "n_trials": n_trials,
            "details": [],
            "flow": "live MQTT wake (import failed)",
            "errors": [f"offline wake imports failed: {exc}"],
        }

    received: list[dict[str, Any]] = []
    topic = format_topic("xiaozhi/wake/{device_id}", device_id)
    reconnect_lock = threading.Lock()
    registry_holder: dict[str, Any] = {}

    def on_message(_client, _userdata, message) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except Exception:
            payload = {"raw": message.payload.decode("utf-8", errors="ignore")}
        payload["_received_at"] = time.perf_counter()
        received.append(payload)
        registry = registry_holder.get("registry")
        if registry is None:
            return
        with reconnect_lock:
            if registry.get(device_id) is None:
                registry.register(device_id, FakeVoiceConnection(device_id))  # type: ignore[arg-type]

    subscriber = mqtt.Client()
    if mqtt_username:
        subscriber.username_pw_set(mqtt_username, mqtt_password or "")
    subscriber.on_message = on_message
    try:
        subscriber.connect(host, port, keepalive=30)
    except Exception as exc:
        text = str(exc)
        err = (
            "MQTT subscriber connect rejected (Not authorized). Set MQTT_USERNAME/MQTT_PASSWORD."
            if "Not authorized" in text
            else f"MQTT subscriber connect failed: {exc}"
        )
        return {
            "success_rate": 0.0,
            "mean_wake_latency_ms": None,
            "n_trials": n_trials,
            "details": [],
            "flow": "live MQTT wake (subscriber connect failed)",
            "errors": [err],
        }
    subscriber.subscribe(topic)
    subscriber.loop_start()
    time.sleep(0.2)

    delivered = 0
    latencies: list[float] = []
    details: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="viaoclaw-offline-wake-live-") as tmp:
        config = _offline_config(tmp, host, port, username=mqtt_username, password=mqtt_password)
        registry = ConnectionRegistry(config)
        registry_holder["registry"] = registry
        pending = registry.pending_store
        fire_handler = CronFireHandler(registry, pending, _NoopExecRunner(), config)
        fire_handler._mqtt_wake = MqttWakePublisher(config)  # noqa: SLF001

        try:
            with _patched_delivery(lambda conn, text: conn.mark_delivered(text)):
                for index in range(n_trials):
                    message = f"live offline payload {index}"
                    before_wake = len(received)
                    _force_offline(registry, device_id)

                    job = {
                        "id": f"bench-live-{index}",
                        "payload": {
                            "channel": "xiaozhi",
                            "to": device_id,
                            "message": message,
                            "deliver": True,
                            "command": "",
                        },
                    }

                    started = time.perf_counter()
                    fire_handler.handle(job)
                    latency_ms = (time.perf_counter() - started) * 1000

                    conn = registry.get(device_id)
                    got_delivery = bool(conn and message in conn.delivered)
                    wake_received = len(received) > before_wake
                    delivered += 1 if got_delivery else 0
                    if wake_received:
                        latencies.append(latency_ms)
                    details.append(
                        {
                            "trial": index + 1,
                            "wake_received": wake_received,
                            "reconnected": conn is not None,
                            "delivered": got_delivery,
                            "pending_stored": not got_delivery and not conn,
                            "wake_latency_ms": round(latency_ms, 3),
                        }
                    )
        finally:
            subscriber.loop_stop()
            subscriber.disconnect()

    success_rate = delivered / max(1, n_trials)
    errors: list[str] = []
    if success_rate == 0:
        if not any(d["wake_received"] for d in details):
            errors.append(
                "MQTT wake was not received by fake ESP32 subscriber "
                f"(topic={topic}). Check broker ACL and MQTT_USERNAME/MQTT_PASSWORD."
            )
        else:
            errors.append(
                "Wake received but pending payload was not delivered after WS register. "
                "See ConnectionRegistry.register -> _flush_pending in core.cron.registry."
            )
    return {
        "success_rate": success_rate,
        "mean_wake_latency_ms": sum(latencies) / len(latencies) if latencies else None,
        "n_trials": n_trials,
        "details": details,
        "flow": "offline -> handle -> mqtt wake -> subscriber register -> deliver (single handle)",
        "errors": errors,
    }


class _NoopExecRunner:
    def run(self, command: str, **kwargs) -> str:
        return "ok"
