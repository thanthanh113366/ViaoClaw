from typing import TYPE_CHECKING

from config.logger import setup_logging
from plugins_func.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

MEMORY_READ_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "memory_read",
        "description": (
            "Read a previously stored user preference or fact by key. Use when the "
            "user asks what you remember about them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Memory key to look up.",
                },
            },
            "required": ["key"],
        },
    },
}


@register_function("memory_read", MEMORY_READ_FUNCTION_DESC, ToolType.SYSTEM_CTL)
def memory_read(conn: "ConnectionHandler", key: str = ""):
    logger.bind(tag=TAG).info(f"[xiaoclaw.memory_read] stub key={key!r}")
    return ActionResponse(Action.RESPONSE, "memory_read stub ok", None)
