from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

from config.logger import setup_logging
from core.agent.context import ChatTurnContext, build_legacy_turn_context, build_turn_context
from core.utils import textUtils
from core.utils.dialogue import Message
from core.utils.util import extract_json_from_string, get_system_error_response
from plugins_func.register import Action, ActionResponse

TAG = __name__
logger = setup_logging()

DIRECT_ANSWER_TOOL = {
    "type": "function",
    "function": {
        "name": "direct_answer",
        "description": "Use this to answer directly in Vietnamese when the user's request does not match any other tool. Put the full spoken Vietnamese reply in the response parameter.",
        "parameters": {
            "type": "object",
            "properties": {
                "response": {
                    "type": "string",
                    "description": "The full Vietnamese reply to speak to the user.",
                },
            },
            "required": ["response"],
        },
    },
}


class ChatEngine:
    def chat_sync(
        self, query: str | None, ctx: ChatTurnContext, *, depth: int = 0
    ) -> bool | None:
        current_sentence_id = None
        conn = ctx.voice_conn

        if query is not None:
            logger.bind(tag=TAG).info(f"大模型收到用户消息: {query}")

        if depth == 0:
            current_sentence_id = str(uuid.uuid4().hex)
            if conn is not None:
                conn.sentence_id = current_sentence_id
            ctx.dialogue.put(Message(role="user", content=query))
            ctx.outbound.on_first(current_sentence_id)
        else:
            if conn is not None:
                current_sentence_id = conn.sentence_id
            elif getattr(ctx.outbound, "_current_sentence_id", None):
                current_sentence_id = ctx.outbound._current_sentence_id
            else:
                current_sentence_id = str(uuid.uuid4().hex)

        max_depth = 5
        force_final_answer = False

        if depth >= max_depth:
            logger.bind(tag=TAG).debug(
                f"已达到最大工具调用深度 {max_depth}，将强制基于现有信息回答"
            )
            force_final_answer = True
            ctx.dialogue.put(
                Message(
                    role="user",
                    content="[系统提示] 已达到最大工具调用次数限制，请你基于目前已经获取的所有信息，直接给出最终答案。不要再尝试调用任何工具。",
                )
            )

        functions = None
        if (
            ctx.intent_type == "function_call"
            and ctx.func_handler is not None
            and not force_final_answer
        ):
            functions = ctx.func_handler.get_functions()
            if functions is not None and depth == 0:
                functions = list(functions)
                functions.append(DIRECT_ANSWER_TOOL)

        response_message = []

        try:
            memory_str = None
            if ctx.memory is not None and query:
                future = asyncio.run_coroutine_threadsafe(
                    ctx.memory.query_memory(query), ctx.loop
                )
                memory_str = future.result()

            if ctx.intent_type == "function_call" and functions is not None:
                llm_responses = ctx.llm.response_with_functions(
                    ctx.llm_session_id,
                    ctx.dialogue.get_llm_dialogue_with_memory(
                        memory_str, ctx.config.get("voiceprint", {})
                    ),
                    functions=functions,
                )
            else:
                llm_responses = ctx.llm.response(
                    ctx.llm_session_id,
                    ctx.dialogue.get_llm_dialogue_with_memory(
                        memory_str, ctx.config.get("voiceprint", {})
                    ),
                )
        except Exception as e:
            logger.bind(tag=TAG).error(f"LLM 处理出错 {query}: {e}")
            return None

        tool_call_flag = False
        tool_calls_list = []
        content_arguments = ""
        emotion_flag = True
        try:
            for response in llm_responses:
                if ctx.abort_check():
                    break
                if ctx.intent_type == "function_call" and functions is not None:
                    content, tools_call = response
                    if "content" in response:
                        content = response["content"]
                        tools_call = None
                    if content is not None and len(content) > 0:
                        content_arguments += content

                    if not tool_call_flag and content_arguments.startswith("<tool_call>"):
                        tool_call_flag = True

                    if tools_call is not None and len(tools_call) > 0:
                        tool_call_flag = True
                        self._merge_tool_calls(tool_calls_list, tools_call)

                    da_stream_buffer = 5
                    for tc in tool_calls_list:
                        if tc["name"] == "direct_answer" and tc.get("arguments"):
                            da_text = self._extract_direct_answer_response(tc["arguments"])
                            sent_len = tc.get("_da_sent", 0)
                            if da_text and len(da_text) > sent_len:
                                safe_end = max(sent_len, len(da_text) - da_stream_buffer)
                                if safe_end > sent_len:
                                    new_part = da_text[sent_len:safe_end]
                                    new_part = self._clean_response_garbage(new_part)
                                    if new_part:
                                        tc["_da_sent"] = safe_end
                                        ctx.outbound.on_chunk(
                                            current_sentence_id, new_part
                                        )
                else:
                    content = response

                if emotion_flag and content is not None and content.strip():
                    asyncio.run_coroutine_threadsafe(
                        textUtils.get_emotion(conn, content),
                        ctx.loop,
                    )
                    emotion_flag = False

                if content is not None and len(content) > 0:
                    if not tool_call_flag:
                        response_message.append(content)
                        ctx.outbound.on_chunk(current_sentence_id, content)
        except Exception as e:
            logger.bind(tag=TAG).error(f"LLM stream processing error: {e}")
            ctx.outbound.on_chunk(
                current_sentence_id, get_system_error_response(ctx.config)
            )
            if depth == 0:
                ctx.outbound.on_last(current_sentence_id)
            return None

        if tool_call_flag:
            b_has_error = False
            if len(tool_calls_list) == 0 and content_arguments:
                a = extract_json_from_string(content_arguments)
                if a is not None:
                    try:
                        content_arguments_json = json.loads(a)
                        tool_calls_list.append(
                            {
                                "id": str(uuid.uuid4().hex),
                                "name": content_arguments_json["name"],
                                "arguments": json.dumps(
                                    content_arguments_json["arguments"],
                                    ensure_ascii=False,
                                ),
                            }
                        )
                    except Exception:
                        b_has_error = True
                        response_message.append(a)
                else:
                    b_has_error = True
                    response_message.append(content_arguments)
                if b_has_error:
                    logger.bind(tag=TAG).error(
                        f"function call error: {content_arguments}"
                    )

            if not b_has_error and len(tool_calls_list) > 0:
                direct_answer_calls = [
                    tc for tc in tool_calls_list if tc["name"] == "direct_answer"
                ]
                real_tool_calls = [
                    tc for tc in tool_calls_list if tc["name"] != "direct_answer"
                ]

                if direct_answer_calls:
                    logger.bind(tag=TAG).debug(
                        "模型选择 direct_answer，流式已播报，写入对话历史"
                    )
                    for tc in direct_answer_calls:
                        da_response = self._extract_direct_answer_response(
                            tc.get("arguments", "{}")
                        )
                        if da_response:
                            sent_len = tc.get("_da_sent", 0)
                            remaining = da_response[sent_len:]
                            if remaining:
                                remaining = self._clean_response_garbage(remaining)
                                if remaining:
                                    ctx.outbound.on_chunk(
                                        current_sentence_id, remaining
                                    )
                            da_response = self._clean_response_garbage(da_response)
                            if conn is not None and getattr(conn, "tts", None) is not None:
                                conn.tts.store_tts_text(current_sentence_id, da_response)
                            ctx.dialogue.put(
                                Message(role="assistant", content=da_response)
                            )

                    if not real_tool_calls:
                        if depth == 0:
                            ctx.outbound.on_last(current_sentence_id)
                        return True

                    tool_calls_list = real_tool_calls

            if not b_has_error and len(tool_calls_list) > 0:
                logger.bind(tag=TAG).debug(
                    f"检测到 {len(tool_calls_list)} 个工具调用"
                )

                streamed_text = ""
                if len(response_message) > 0:
                    streamed_text = "".join(response_message)
                    if conn is not None and getattr(conn, "tts", None) is not None:
                        conn.tts.store_tts_text(current_sentence_id, streamed_text)
                    ctx.dialogue.put(
                        Message(role="assistant", content=streamed_text)
                    )
                response_message.clear()

                futures_with_data = []
                tool_conn = ctx.conn_proxy
                from core.handle.reportHandle import enqueue_tool_report

                for tool_call_data in tool_calls_list:
                    logger.bind(tag=TAG).debug(
                        f"function_name={tool_call_data['name']}, "
                        f"function_id={tool_call_data['id']}, "
                        f"function_arguments={tool_call_data['arguments']}"
                    )
                    tool_input = json.loads(tool_call_data.get("arguments") or "{}")
                    if conn is not None:
                        enqueue_tool_report(
                            conn, tool_call_data["name"], tool_input
                        )

                    future = asyncio.run_coroutine_threadsafe(
                        ctx.func_handler.handle_llm_function_call(
                            tool_conn, tool_call_data
                        ),
                        ctx.loop,
                    )
                    futures_with_data.append((future, tool_call_data, tool_input))

                tool_call_timeout = int(ctx.config.get("tool_call_timeout", 30))
                tool_results = []

                for future, tool_call_data, tool_input in futures_with_data:
                    try:
                        result = future.result(timeout=tool_call_timeout)
                        tool_results.append((result, tool_call_data))
                        if conn is not None:
                            enqueue_tool_report(
                                conn,
                                tool_call_data["name"],
                                tool_input,
                                str(result.result) if result.result else None,
                                report_tool_call=False,
                            )
                    except Exception as e:
                        logger.bind(tag=TAG).error(
                            f"工具调用超时或异常: {tool_call_data['name']}, 错误: {e}"
                        )
                        tool_results.append(
                            (
                                ActionResponse(
                                    action=Action.ERROR,
                                    result="哎呀，网络遇到点问题，请稍后再试下！",
                                ),
                                tool_call_data,
                            )
                        )
                        if conn is not None:
                            enqueue_tool_report(
                                conn,
                                tool_call_data["name"],
                                tool_input,
                                str(e),
                                report_tool_call=False,
                            )

                if tool_results:
                    self._handle_function_result(
                        ctx, tool_results, depth=depth, streamed_text=streamed_text
                    )

        if len(response_message) > 0:
            text_buff = "".join(response_message)
            if conn is not None and getattr(conn, "tts", None) is not None:
                conn.tts.store_tts_text(current_sentence_id, text_buff)
            ctx.dialogue.put(Message(role="assistant", content=text_buff))

        if depth == 0:
            ctx.outbound.on_last(current_sentence_id)
            logger.bind(tag=TAG).debug(
                lambda: json.dumps(
                    ctx.dialogue.get_llm_dialogue(), indent=4, ensure_ascii=False
                )
            )

        return True

    def _handle_function_result(
        self,
        ctx: ChatTurnContext,
        tool_results,
        depth: int,
        streamed_text: str = "",
    ) -> None:
        from core.providers.tts.dto.dto import ContentType

        conn = ctx.voice_conn
        need_llm_tools = []
        record_tools = []

        for result, tool_call_data in tool_results:
            if result.action in [
                Action.RESPONSE,
                Action.NOTFOUND,
                Action.ERROR,
            ]:
                text = result.response if result.response else result.result
                if streamed_text and text in streamed_text:
                    logger.bind(tag=TAG).debug(
                        f"Skipping duplicate TTS for tool {tool_call_data['name']}, already streamed"
                    )
                else:
                    if conn is not None and getattr(conn, "tts", None) is not None:
                        conn.tts.tts_one_sentence(
                            conn, ContentType.TEXT, content_detail=text
                        )
                        conn.tts.store_tts_text(conn.sentence_id, text)
                ctx.dialogue.put(Message(role="assistant", content=text))
            elif result.action == Action.REQLLM:
                need_llm_tools.append((result, tool_call_data))
            elif result.action == Action.RECORD:
                record_tools.append((result, tool_call_data))

        if record_tools:
            all_tool_calls = [
                {
                    "id": tool_call_data["id"],
                    "function": {
                        "arguments": (
                            "{}"
                            if tool_call_data["arguments"] == ""
                            else tool_call_data["arguments"]
                        ),
                        "name": tool_call_data["name"],
                    },
                    "type": "function",
                    "index": idx,
                }
                for idx, (_, tool_call_data) in enumerate(record_tools)
            ]
            ctx.dialogue.put(Message(role="assistant", tool_calls=all_tool_calls))

            for result, tool_call_data in record_tools:
                text = result.result or ""
                ctx.dialogue.put(
                    Message(
                        role="tool",
                        tool_call_id=(
                            str(uuid.uuid4())
                            if tool_call_data["id"] is None
                            else tool_call_data["id"]
                        ),
                        content=text,
                    )
                )

            response_parts = []
            for result, _ in record_tools:
                resp = result.response or result.result
                if resp:
                    response_parts.append(resp)
            if response_parts:
                ctx.dialogue.put(
                    Message(role="assistant", content="，".join(response_parts))
                )

        if need_llm_tools:
            all_tool_calls = [
                {
                    "id": tool_call_data["id"],
                    "function": {
                        "arguments": (
                            "{}"
                            if tool_call_data["arguments"] == ""
                            else tool_call_data["arguments"]
                        ),
                        "name": tool_call_data["name"],
                    },
                    "type": "function",
                    "index": idx,
                }
                for idx, (_, tool_call_data) in enumerate(need_llm_tools)
            ]
            ctx.dialogue.put(Message(role="assistant", tool_calls=all_tool_calls))

            for result, tool_call_data in need_llm_tools:
                text = result.result
                if text is not None and len(text) > 0:
                    ctx.dialogue.put(
                        Message(
                            role="tool",
                            tool_call_id=(
                                str(uuid.uuid4())
                                if tool_call_data["id"] is None
                                else tool_call_data["id"]
                            ),
                            content=text,
                        )
                    )

            self.chat_sync(None, ctx, depth=depth + 1)

    @staticmethod
    def _extract_direct_answer_response(arguments_str):
        if not arguments_str:
            return ""
        try:
            data = json.loads(arguments_str)
            if isinstance(data, dict) and "response" in data:
                return data["response"]
        except (json.JSONDecodeError, TypeError):
            pass
        marker = '"response": "'
        idx = arguments_str.find(marker)
        if idx < 0:
            marker = '"response":"'
            idx = arguments_str.find(marker)
        if idx < 0:
            return ""
        start = idx + len(marker)
        raw = arguments_str[start:]
        if raw.endswith('"}'):
            raw = raw[:-2]
        elif raw.endswith('"'):
            raw = raw[:-1]
        raw = raw.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        return raw

    @staticmethod
    def _clean_response_garbage(text):
        if not text:
            return text
        garbage_chars = frozenset('")\'}）')
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if (
                stripped
                and len(stripped) <= 8
                and all(c in garbage_chars for c in stripped)
            ):
                continue
            cleaned.append(line)
        result = "\n".join(cleaned)
        result = re.sub(r'["\'}\]]+$', "", result.rstrip()).rstrip()
        return result

    def _merge_tool_calls(self, tool_calls_list, tools_call):
        for tool_call in tools_call:
            tool_index = getattr(tool_call, "index", None)
            if tool_index is None:
                if tool_call.function.name:
                    tool_index = len(tool_calls_list)
                else:
                    tool_index = len(tool_calls_list) - 1 if tool_calls_list else 0

            if tool_index >= len(tool_calls_list):
                tool_calls_list.append({"id": "", "name": "", "arguments": ""})

            if tool_call.id:
                tool_calls_list[tool_index]["id"] = tool_call.id
            if tool_call.function.name:
                tool_calls_list[tool_index]["name"] = tool_call.function.name
            if tool_call.function.arguments:
                tool_calls_list[tool_index]["arguments"] += tool_call.function.arguments
