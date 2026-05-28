import asyncio
from typing import TYPE_CHECKING

from config.logger import setup_logging
from core.exec.service import get_exec_runner, is_exec_enabled
from plugins_func.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

EXEC_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "exec",
        "description": (
            "Execute shell commands inside the configured workspace sandbox. "
            "Use action=run with a real bash command (e.g. 'ls', 'df -h'). "
            "Do not use for Home Assistant or device control — use hass_* tools instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["run", "list", "kill"],
                    "description": "Action: run (execute command). list/kill reserved for future use.",
                },
                "command": {
                    "type": "string",
                    "description": "Shell command to execute (required when action=run).",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory (must stay inside exec workspace).",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (overrides config default).",
                },
            },
            "required": ["action"],
        },
    },
}


def _run_command(
    conn: "ConnectionHandler",
    command: str,
    cwd: str | None,
    timeout: int | None,
) -> str:
    runner = get_exec_runner(conn.config)
    wait_timeout = (timeout if timeout is not None else runner.timeout_seconds) + 5

    if conn.loop is not None:
        future = asyncio.run_coroutine_threadsafe(
            asyncio.to_thread(runner.run, command, cwd=cwd, timeout=timeout),
            conn.loop,
        )
        return future.result(timeout=wait_timeout)

    if conn.executor is not None:
        future = conn.executor.submit(
            runner.run, command, cwd=cwd, timeout=timeout
        )
        return future.result(timeout=wait_timeout)

    return runner.run(command, cwd=cwd, timeout=timeout)


@register_function("exec", EXEC_FUNCTION_DESC, ToolType.SYSTEM_CTL)
def exec_tool(
    conn: "ConnectionHandler",
    action: str,
    command: str | None = None,
    cwd: str | None = None,
    timeout: int | None = None,
):
    if not is_exec_enabled(conn.config):
        return ActionResponse(Action.ERROR, "command execution is disabled", None)

    action = (action or "").strip().lower()
    if action in ("list", "kill"):
        return ActionResponse(
            Action.ERROR,
            f"exec action '{action}' is not implemented yet",
            None,
        )
    if action != "run":
        return ActionResponse(Action.ERROR, f"unknown action: {action}", None)

    command = (command or "").strip()
    if not command:
        return ActionResponse(Action.ERROR, "command is required for action=run", None)

    logger.bind(tag=TAG).info(f"[xiaoclaw.exec] tool run command={command!r}")

    try:
        output = _run_command(conn, command, cwd, timeout)
    except Exception as exc:
        logger.bind(tag=TAG).warning(f"[xiaoclaw.exec] failed: {exc}")
        return ActionResponse(
            Action.REQLLM,
            f"Command failed: {exc}",
            None,
        )

    return ActionResponse(
        Action.REQLLM,
        f"Command output:\n{output}",
        None,
    )
