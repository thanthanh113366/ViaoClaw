from __future__ import annotations

import asyncio
from typing import Any


class DispatchRecorder:
    """Captures tool dispatches routed through production handlers."""

    def __init__(self) -> None:
        self.dispatches: list[dict[str, Any]] = []
        self.memory_ops: list[dict[str, Any]] = []

    def record(self, function_name: str, arguments: dict[str, Any]) -> None:
        self.dispatches.append({"function": function_name, "arguments": dict(arguments)})

    def record_memory(self, op: str, **payload: Any) -> None:
        self.memory_ops.append({"op": op, **payload})

    @property
    def last(self) -> dict[str, Any] | None:
        return self.dispatches[-1] if self.dispatches else None


class FakeBenchConn:
    """Minimal conn stand-in for plugin/tool dispatch in benchmarks."""

    def __init__(
        self,
        *,
        channel: str = "telegram",
        device_id: str = "bench-device-001",
        chat_id: str = "12345",
        config: dict[str, Any] | None = None,
    ) -> None:
        self.channel = channel
        self.device_id = device_id
        self.chat_id = chat_id
        self.config = config or {
            "Intent": {"benchmark_intent": {"type": "function_call", "functions": []}},
            "selected_module": {"Intent": "benchmark_intent"},
            "plugins": {},
            "exec": {"enabled": True},
            "cron": {"enabled": True},
        }
        self.intent_type = "function_call"
        self.loop = asyncio.new_event_loop()
        self.client_abort = False


class FakeVoiceConnection:
    """Offline voice websocket stand-in for cron/MQTT delivery benchmarks."""

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.delivered: list[str] = []
        self.tts = object()
        self.config: dict[str, Any] = {}
        self.executor = _ImmediateExecutor()

    def mark_delivered(self, text: str) -> None:
        self.delivered.append(text)


class _ImmediateExecutor:
    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)
        return _DoneFuture()


class _DoneFuture:
    def result(self, timeout=None):
        return None
