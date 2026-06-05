"""Shared live preflight checks (TCP/files/env only — no Docker API)."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import asdict, dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
ASR_FIXTURE_DIR = DATA_DIR / "fixtures" / "asr"
ASR_TESTSET = DATA_DIR / "asr_testset.json"

DEFAULT_MQTT_HOST = "127.0.0.1"
DEFAULT_MQTT_PORT = 1883
DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 8000
DEFAULT_WS_URL = "ws://127.0.0.1:8000/xiaozhi/v1/"
DEFAULT_HTTP_URL = "http://127.0.0.1:8000/"


@dataclass
class TcpCheck:
    host: str
    port: int
    ok: bool
    error: str | None = None


@dataclass
class MqttCheck:
    tcp: TcpCheck
    auth_ok: bool | None = None
    auth_error: str | None = None
    publish_ok: bool | None = None
    publish_error: str | None = None


@dataclass
class AsrFixtureReport:
    fixture_count: int
    expected_count: int
    missing_count: int
    missing_examples: list[str] = field(default_factory=list)
    ok: bool = False


@dataclass
class EnvCheck:
    name: str
    set: bool
    masked_preview: str | None = None


def tcp_check(host: str, port: int, timeout: float = 2.0) -> TcpCheck:
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return TcpCheck(host=host, port=port, ok=True)
    except OSError as exc:
        return TcpCheck(host=host, port=port, ok=False, error=repr(exc))
    finally:
        sock.close()


def check_asr_fixtures(
    fixture_dir: Path | str | None = None,
    testset_path: Path | str | None = None,
) -> AsrFixtureReport:
    fixture_dir = Path(fixture_dir or ASR_FIXTURE_DIR)
    testset_path = Path(testset_path or ASR_TESTSET)
    with testset_path.open("r", encoding="utf-8") as handle:
        samples = json.load(handle)
    missing: list[str] = []
    present = 0
    for sample in samples:
        path = fixture_dir / Path(sample["audio_file"]).name
        if path.is_file():
            present += 1
        else:
            missing.append(str(path))
    expected = len(samples)
    return AsrFixtureReport(
        fixture_count=present,
        expected_count=expected,
        missing_count=len(missing),
        missing_examples=missing[:5],
        ok=present == expected and expected > 0,
    )


def _mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}...{value[-2:]}"


def check_env_vars() -> list[EnvCheck]:
    keys = [
        "LLM_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "VIAOCLAW_SERVER_URL",
        "VIAOCLAW_WS_URL",
        "MQTT_USERNAME",
        "MQTT_PASSWORD",
    ]
    results: list[EnvCheck] = []
    for name in keys:
        value = os.environ.get(name)
        results.append(
            EnvCheck(
                name=name,
                set=bool(value),
                masked_preview=_mask_secret(value) if value else None,
            )
        )
    return results


def check_mqtt(
    host: str | None = None,
    port: int | None = None,
    *,
    username: str | None = None,
    password: str | None = None,
    probe_topic: str = "xiaozhi/wake/bench-preflight",
) -> MqttCheck:
    host = host or os.environ.get("MQTT_HOST", DEFAULT_MQTT_HOST)
    port = int(port or os.environ.get("MQTT_PORT", DEFAULT_MQTT_PORT))
    username = username if username is not None else os.environ.get("MQTT_USERNAME")
    password = password if password is not None else os.environ.get("MQTT_PASSWORD")

    tcp = tcp_check(host, port)
    result = MqttCheck(tcp=tcp)
    if not tcp.ok:
        return result

    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:
        result.auth_ok = False
        result.auth_error = f"paho-mqtt not installed: {exc}"
        return result

    client = mqtt.Client()
    if username:
        client.username_pw_set(username, password or "")

    auth_error: list[str] = []

    def on_connect(_client, _userdata, _flags, rc, *_args):  # noqa: ANN001
        if rc != 0:
            auth_error.append(f"CONNACK rc={rc}")

    try:
        client.on_connect = on_connect
        client.connect(host, port, keepalive=10)
        client.loop_start()
        import time

        time.sleep(0.3)
        client.loop_stop()
        client.disconnect()
        if auth_error:
            result.auth_ok = False
            result.auth_error = auth_error[0]
            if "rc=5" in auth_error[0]:
                result.auth_error = (
                    "MQTT broker rejected connection (Not authorized). "
                    "Set MQTT_USERNAME and MQTT_PASSWORD for live B5."
                )
        else:
            result.auth_ok = True
    except Exception as exc:
        result.auth_ok = False
        text = str(exc)
        if "Not authorized" in text:
            result.auth_error = (
                "MQTT broker rejected connection (Not authorized). "
                "Set MQTT_USERNAME and MQTT_PASSWORD for live B5."
            )
        else:
            result.auth_error = text

    if result.auth_ok:
        try:
            import paho.mqtt.publish as mqtt_publish

            auth = None
            if username:
                auth = {"username": username, "password": password or ""}
            mqtt_publish.single(
                probe_topic,
                payload='{"type":"wake","source":"preflight"}',
                hostname=host,
                port=port,
                qos=0,
                auth=auth,
            )
            result.publish_ok = True
        except Exception as exc:
            result.publish_ok = False
            text = str(exc)
            if "Not authorized" in text or "refused" in text.lower():
                result.publish_error = (
                    f"MQTT publish rejected: {text}. "
                    "Set MQTT_USERNAME/MQTT_PASSWORD matching mosquitto config."
                )
            else:
                result.publish_error = text

    return result


def run_all_checks() -> dict:
    mqtt_host = os.environ.get("MQTT_HOST", DEFAULT_MQTT_HOST)
    mqtt_port = int(os.environ.get("MQTT_PORT", DEFAULT_MQTT_PORT))
    server_host = os.environ.get("VIAOCLAW_SERVER_HOST", DEFAULT_SERVER_HOST)
    server_port = int(os.environ.get("VIAOCLAW_SERVER_PORT", DEFAULT_SERVER_PORT))

    return {
        "mqtt_tcp": asdict(tcp_check(mqtt_host, mqtt_port)),
        "mqtt": asdict(check_mqtt(mqtt_host, mqtt_port)),
        "server_tcp": asdict(tcp_check(server_host, server_port)),
        "asr_fixtures": asdict(check_asr_fixtures()),
        "env": [asdict(item) for item in check_env_vars()],
        "defaults": {
            "mqtt_host": DEFAULT_MQTT_HOST,
            "mqtt_port": DEFAULT_MQTT_PORT,
            "server_url": os.environ.get("VIAOCLAW_SERVER_URL", DEFAULT_HTTP_URL),
            "ws_url": os.environ.get("VIAOCLAW_WS_URL", DEFAULT_WS_URL),
            "discovery_note": (
                "Docker xiaozhi-esp32-server uses host network; TCP :8000 serves WebSocket "
                "at /xiaozhi/v1/ (HTTP GET returns 'Server is running'). "
                "B1/B3/B7 live injection still needs VIAOCLAW_WS_URL or capture fixture."
            ),
        },
    }
