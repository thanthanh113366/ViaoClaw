"""Install lightweight stubs for optional production dependencies used in CI mock mode."""

from __future__ import annotations

import sys
import types
from typing import Any


def _ensure_module(name: str) -> types.ModuleType:
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)
    return sys.modules[name]


def install_mcp_stubs() -> None:
    """Stub MCP SDK imports required by core.providers.tools.server_mcp."""
    mcp = _ensure_module("mcp")
    mcp.ClientSession = type("ClientSession", (), {})  # type: ignore[attr-defined]
    mcp.StdioServerParameters = type("StdioServerParameters", (), {})  # type: ignore[attr-defined]
    mcp.Implementation = type("Implementation", (), {})  # type: ignore[attr-defined]

    _ensure_module("mcp.client")
    session = _ensure_module("mcp.client.session")
    for alias in (
        "SamplingFnT",
        "ElicitationFnT",
        "ListRootsFnT",
        "LoggingFnT",
        "MessageHandlerFnT",
    ):
        setattr(session, alias, Any)

    async def _noop_client(*_args, **_kwargs):
        if False:  # pragma: no cover - keep async generator shape
            yield None

    stdio = _ensure_module("mcp.client.stdio")
    stdio.stdio_client = _noop_client

    sse = _ensure_module("mcp.client.sse")
    sse.sse_client = _noop_client

    streamable = _ensure_module("mcp.client.streamable_http")
    streamable.streamablehttp_client = _noop_client

    shared = _ensure_module("mcp.shared.session")
    shared.ProgressFnT = Any

    mcp_types = _ensure_module("mcp.types")
    if not hasattr(mcp_types, "LoggingMessageNotificationParams"):
        mcp_types.LoggingMessageNotificationParams = type("LoggingMessageNotificationParams", (), {})


def install_media_stubs() -> None:
    """Stub audio helpers imported by core.utils.util at module import time."""
    opus = _ensure_module("opuslib_next")
    opus.APPLICATION_AUDIO = 2049
    opus.Encoder = type("Encoder", (), {"__init__": lambda self, *_a, **_k: None})
    opus.Decoder = type("Decoder", (), {"__init__": lambda self, *_a, **_k: None})

    pydub = _ensure_module("pydub")
    pydub.AudioSegment = type("AudioSegment", (), {})  # type: ignore[attr-defined]

    _ensure_module("cnlunar")


def install_telegram_stubs() -> None:
    _ensure_module("aiogram")


def install_misc_stubs() -> None:
    portalocker = _ensure_module("portalocker")
    portalocker.LOCK_EX = 1
    portalocker.LOCK_NB = 2
    portalocker.LockException = type("LockException", (Exception,), {})
    portalocker.lock = lambda *_a, **_k: None
    portalocker.unlock = lambda *_a, **_k: None


def install_all_optional_stubs() -> None:
    install_mcp_stubs()
    install_media_stubs()
    install_telegram_stubs()
    install_misc_stubs()
