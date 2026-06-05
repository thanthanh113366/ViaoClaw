from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

from benchmark.config import ensure_benchmark_import_stubs
from benchmark.stubs.recording import DispatchRecorder, FakeBenchConn


def extract_json_from_string(input_string: str) -> str | None:
    """Mirror core.utils.util.extract_json_from_string without importing heavy util deps."""
    match = re.search(r"(\{.*\})", input_string, re.DOTALL)
    return match.group(1) if match else None


def build_tool_call_data(mock_response: str | dict) -> dict[str, Any]:
    """Build tool_call_data using the same shape ChatEngine emits to the dispatcher."""
    if isinstance(mock_response, dict):
        if "function" in mock_response:
            fn = mock_response["function"]
            name = str(fn.get("name") or "")
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args) if args else {}
        else:
            name = str(mock_response.get("name") or "")
            args = dict(mock_response.get("arguments") or {})
    else:
        raw = extract_json_from_string(str(mock_response)) or str(mock_response)
        data = json.loads(raw)
        if "function" in data:
            fn = data["function"]
            name = str(fn.get("name") or "")
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args) if args else {}
        else:
            name = str(data.get("name") or "")
            args = dict(data.get("arguments") or {})

    return {
        "id": uuid.uuid4().hex,
        "name": name,
        "arguments": json.dumps(args, ensure_ascii=False),
    }


def normalize_tool_call(tool_call_data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Mirror UnifiedToolHandler.handle_llm_function_call argument normalization."""
    function_name = str(tool_call_data["name"])
    arguments = tool_call_data.get("arguments", {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            arguments = {}
    return function_name, dict(arguments)


async def dispatch_with_recording(
    tool_call_data: dict[str, Any],
    *,
    conn: FakeBenchConn | None = None,
    recorder: DispatchRecorder | None = None,
) -> tuple[str, dict[str, Any]]:
    """Route a parsed tool call through UnifiedToolHandler with a recording executor."""
    ensure_benchmark_import_stubs()
    from core.providers.tools.base import ToolDefinition, ToolExecutor, ToolType
    from core.providers.tools.unified_tool_handler import UnifiedToolHandler
    from plugins_func.register import Action, ActionResponse

    conn = conn or FakeBenchConn()
    recorder = recorder or DispatchRecorder()

    class RecordingExecutor(ToolExecutor):
        async def execute(self, _conn, tool_name: str, arguments: dict[str, Any]) -> ActionResponse:
            recorder.record(tool_name, arguments)
            return ActionResponse(action=Action.RESPONSE, response="benchmark-ok")

        def get_tools(self) -> dict[str, ToolDefinition]:
            return {
                tool_call_data["name"]: ToolDefinition(
                    name=tool_call_data["name"],
                    description={"type": "function", "function": {"name": tool_call_data["name"]}},
                    tool_type=ToolType.SERVER_PLUGIN,
                )
            }

        def has_tool(self, tool_name: str) -> bool:
            return tool_name == tool_call_data["name"]

    handler = UnifiedToolHandler(conn)
    handler.finish_init = True
    handler.tool_manager.register_executor(ToolType.SERVER_PLUGIN, RecordingExecutor())
    handler.tool_manager.refresh_tools()

    await handler.handle_llm_function_call(conn, tool_call_data)
    if recorder.last:
        return recorder.last["function"], recorder.last["arguments"]
    return normalize_tool_call(tool_call_data)


def evaluate_scenario(item: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Score one scenario via parser + dispatcher path."""
    tool_call_data = build_tool_call_data(item["mock_response"])
    parsed_name, parsed_args = normalize_tool_call(tool_call_data)

    errors: list[str] = []
    dispatched_name, dispatched_args = parsed_name, parsed_args

    try:
        dispatched_name, dispatched_args = asyncio.run(dispatch_with_recording(tool_call_data))
    except Exception as exc:
        errors.append(f"{item['id']}: dispatcher failed: {exc}")

    expected = item.get("expected_args") or {}
    function_correct = dispatched_name == item["expected_function"]
    exact = function_correct and all(
        dispatched_args.get(key) == value for key, value in expected.items()
    )
    hits = sum(1 for key, value in expected.items() if dispatched_args.get(key) == value)
    arg_score = hits / len(expected) if expected else 1.0
    partial = function_correct and arg_score >= 0.80

    return {
        "id": item["id"],
        "utterance": item["user_utterance"],
        "expected_function": item["expected_function"],
        "parsed_function": parsed_name,
        "dispatched_function": dispatched_name,
        "dispatched_args": dispatched_args,
        "function_correct": function_correct,
        "args_exact_match": exact,
        "partial_args_match": partial,
        "arg_score": round(arg_score, 4),
        "errors": errors,
    }
