from typing import TYPE_CHECKING

from config.logger import setup_logging
from plugins_func.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

TELEGRAM_SEND_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "telegram_send",
        "description": (
            "Send a text message to Telegram proactively. Use when the user asks to "
            "notify someone, send a Telegram message, or message family on Telegram."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Message body to send.",
                },
                "target": {
                    "type": "string",
                    "description": "Optional recipient group, e.g. family.",
                },
                "chat_id": {
                    "type": "string",
                    "description": "Optional explicit Telegram chat id.",
                },
            },
            "required": ["text"],
        },
    },
}


@register_function("telegram_send", TELEGRAM_SEND_FUNCTION_DESC, ToolType.SYSTEM_CTL)
def telegram_send(
    conn: "ConnectionHandler",
    text: str = "",
    target: str | None = None,
    chat_id: str | None = None,
):
    logger.bind(tag=TAG).info(
        f"[xiaoclaw.telegram_send] stub text={text!r} target={target!r} chat_id={chat_id!r}"
    )
    return ActionResponse(Action.RESPONSE, "telegram_send stub ok", None)
