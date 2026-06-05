from typing import TYPE_CHECKING

from config.logger import setup_logging
from plugins_func.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

MEMORY_WRITE_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "memory_write",
        "description": (
            "Persist a user preference or fact for later recall. Use when the user "
            "asks you to remember something about them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Stable memory key, e.g. lighting_preference.",
                },
                "value": {
                    "type": "string",
                    "description": "Value to store.",
                },
            },
            "required": ["key", "value"],
        },
    },
}


@register_function("memory_write", MEMORY_WRITE_FUNCTION_DESC, ToolType.SYSTEM_CTL)
def memory_write(conn: "ConnectionHandler", key: str = "", value: str = ""):
    logger.bind(tag=TAG).info(
        f"[xiaoclaw.memory_write] stub key={key!r} value={value!r}"
    )
    return ActionResponse(Action.RESPONSE, "memory_write stub ok", None)
