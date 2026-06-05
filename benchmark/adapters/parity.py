from __future__ import annotations

import asyncio
from typing import Any

from benchmark.adapters.chat_engine_live import LiveRuntimeHarness
from benchmark.adapters.funcall import build_tool_call_data, dispatch_with_recording
from benchmark.config import ensure_benchmark_import_stubs
from benchmark.stubs.recording import FakeBenchConn


def session_key_for_channel(channel: str, identifier: str) -> tuple[str, str | None, str | None]:
    ensure_benchmark_import_stubs()
    from core.agent.dispatcher import _parse_session_key

    prefix = "telegram" if channel == "telegram" else "xiaozhi"
    session_key = f"{prefix}:{identifier}"
    device_id, chat_id = _parse_session_key(session_key)
    return session_key, device_id, chat_id


async def run_telegram_path(
    utterance: str,
    mock_response: dict[str, Any],
    *,
    chat_id: str = "12345",
) -> dict[str, Any]:
    """Mock path: session_key + UnifiedToolHandler dispatch adapter."""
    conn = FakeBenchConn(channel="telegram", chat_id=chat_id, device_id=chat_id)
    tool_call_data = build_tool_call_data(mock_response)
    function_name, arguments = await dispatch_with_recording(tool_call_data, conn=conn)
    session_key, device_id, parsed_chat_id = session_key_for_channel("telegram", chat_id)
    return {
        "path": "mock: telegram adapter + UnifiedToolHandler",
        "utterance": utterance,
        "session_key": session_key,
        "device_id": device_id,
        "chat_id": parsed_chat_id,
        "function": function_name,
        "args": arguments,
    }


async def run_voice_path(
    utterance: str,
    mock_response: dict[str, Any],
    *,
    device_id: str = "bench-device-001",
) -> dict[str, Any]:
    """Mock path: session_key + UnifiedToolHandler dispatch adapter."""
    conn = FakeBenchConn(channel="xiaozhi", device_id=device_id)
    tool_call_data = build_tool_call_data(mock_response)
    function_name, arguments = await dispatch_with_recording(tool_call_data, conn=conn)
    session_key, parsed_device_id, parsed_chat_id = session_key_for_channel("xiaozhi", device_id)
    return {
        "path": "mock: voice adapter + UnifiedToolHandler",
        "utterance": utterance,
        "session_key": session_key,
        "device_id": parsed_device_id,
        "chat_id": parsed_chat_id,
        "function": function_name,
        "args": arguments,
    }


def run_live_telegram_path(
    harness: LiveRuntimeHarness,
    utterance: str,
    *,
    chat_id: str = "12345",
) -> dict[str, Any]:
    session_key, device_id, parsed_chat_id = session_key_for_channel("telegram", chat_id)
    turn = harness.run_turn(
        session_key=session_key,
        utterance=utterance,
        channel="telegram",
        chat_id=str(chat_id),
        path_label="partial-live: InboundDispatcher + ChatEngine (no aiogram polling)",
    )
    return {
        "path": turn.path,
        "utterance": utterance,
        "session_key": session_key,
        "device_id": device_id,
        "chat_id": parsed_chat_id,
        "function": turn.function,
        "args": turn.args,
        "errors": turn.errors,
    }


def run_live_voice_path(
    harness: LiveRuntimeHarness,
    utterance: str,
    *,
    device_id: str = "bench-device-001",
) -> dict[str, Any]:
    session_key, parsed_device_id, parsed_chat_id = session_key_for_channel("xiaozhi", device_id)
    # conn=None: same as Telegram live path — use AgentRuntime prompt/session, not FakeBenchConn
    # (FakeBenchConn lacks conn.llm and breaks session.run_turn)
    turn = harness.run_turn(
        session_key=session_key,
        utterance=utterance,
        channel="xiaozhi",
        path_label="partial-live: InboundDispatcher + ChatEngine (voice session_key)",
    )
    return {
        "path": turn.path,
        "utterance": utterance,
        "session_key": session_key,
        "device_id": parsed_device_id,
        "chat_id": parsed_chat_id,
        "function": turn.function,
        "args": turn.args,
        "errors": turn.errors,
    }


def top_level_args(args: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in args.items() if not isinstance(value, dict)}


def compare_paths(telegram: dict[str, Any], voice: dict[str, Any]) -> dict[str, Any]:
    same_function = telegram.get("function") == voice.get("function")
    same_args = top_level_args(telegram.get("args") or {}) == top_level_args(voice.get("args") or {})
    telegram_prefix = str(telegram["session_key"]).startswith("telegram:")
    voice_prefix = str(voice["session_key"]).startswith("xiaozhi:")
    return {
        "same_function": same_function,
        "same_top_level_args": same_args,
        "telegram_session_key": telegram["session_key"],
        "voice_session_key": voice["session_key"],
        "session_prefix_ok": telegram_prefix and voice_prefix,
        "passed": same_function and same_args and telegram_prefix and voice_prefix,
    }


def run_parity_case(item: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    utterance = item["user_utterance"]
    mock_response = item["mock_response"]
    errors: list[str] = []

    if config.get("mode") == "live":
        if not config["env"].get("llm_api_key"):
            return {
                "id": item["id"],
                "utterance": utterance,
                "passed": False,
                "errors": ["LIVE mode requires LLM_API_KEY"],
            }
        harness: LiveRuntimeHarness | None = config.get("_live_harness")
        if harness is None:
            return {
                "id": item["id"],
                "utterance": utterance,
                "passed": False,
                "errors": ["LiveRuntimeHarness not initialized for B7"],
            }
        telegram = run_live_telegram_path(harness, utterance, chat_id="12345")
        voice = run_live_voice_path(harness, utterance, device_id="bench-device-001")
        errors.extend(telegram.get("errors") or [])
        errors.extend(voice.get("errors") or [])
        comparison = compare_paths(telegram, voice)
        passed = comparison["passed"] and not errors
        return {
            "id": item["id"],
            "utterance": utterance,
            "telegram": telegram,
            "voice": voice,
            **comparison,
            "passed": passed,
            "live_mode": "partial-live (ChatEngine; TelegramGateway aiogram polling not used)",
            "errors": errors,
        }

    telegram = asyncio.run(run_telegram_path(utterance, mock_response))
    voice = asyncio.run(run_voice_path(utterance, mock_response))
    comparison = compare_paths(telegram, voice)
    return {
        "id": item["id"],
        "utterance": utterance,
        "telegram": telegram,
        "voice": voice,
        **comparison,
        "live_mode": "mock adapter",
        "errors": errors,
    }
