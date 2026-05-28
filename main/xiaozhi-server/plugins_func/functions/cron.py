import time
from typing import TYPE_CHECKING

from config.logger import setup_logging
from core.cron.service import get_cron_service
from core.exec.service import is_exec_enabled
from plugins_func.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

CRON_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "cron",
        "description": (
            "Schedule reminders, tasks, or system commands. IMPORTANT: When user asks "
            "to be reminded or scheduled, you MUST call this tool. Use 'at_seconds' for "
            "one-time reminders (e.g., 'remind me in 10 minutes' → at_seconds=600). Use "
            "'every_seconds' ONLY for recurring tasks. Use 'cron_expr' for complex schedules. "
            "Use 'command' ONLY for real shell commands (e.g. 'df -h', 'curl ...'). "
            "Never put AI tool names (hass_set_state, hass_get_state, cron, etc.) in 'command'. "
            "For Home Assistant / device control at fire time: set deliver=false, leave command "
            "empty, and write message as a future self-instruction (see 'message' parameter). "
            "Reminders play on the speaker only when the device is online or when the user "
            "turns it on again; if the device was off for a long time, delivery may be delayed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "remove", "enable", "disable"],
                    "description": "Action to perform.",
                },
                "message": {
                    "type": "string",
                    "description": (
                        "Required for add. Content depends on deliver:\n"
                        "- deliver=true (default): a short, warm, natural reminder spoken "
                        "verbatim via TTS to the user — as if talking to a close friend.\n"
                        "- deliver=false: an instruction written for your FUTURE self when "
                        "the job fires (injected as [cron job_id] + this message). Be explicit "
                        "about what to do: which tools to call, entity_id, action, then what "
                        "to say to the user. Example: 'Gọi hass_set_state bật switch.phong_ngu_led "
                        "(turn_on). Sau đó nói ngắn: Đèn phòng ngủ đã bật.'"
                    ),
                },
                "command": {
                    "type": "string",
                    "description": (
                        "Optional real shell/bash command only (e.g. 'df -h'). "
                        "Forces deliver=false. Do NOT use AI tool names here."
                    ),
                },
                "command_confirm": {
                    "type": "boolean",
                    "description": "Required when allow_command is false.",
                },
                "at_seconds": {
                    "type": "integer",
                    "description": "One-time: seconds from now.",
                },
                "every_seconds": {
                    "type": "integer",
                    "description": "Recurring interval in seconds.",
                },
                "cron_expr": {
                    "type": "string",
                    "description": "Cron expression, e.g. '0 9 * * *'.",
                },
                "job_id": {
                    "type": "string",
                    "description": "Job ID for remove/enable/disable.",
                },
                "deliver": {
                    "type": "boolean",
                    "description": (
                        "How message is used at fire time. "
                        "- true : speak message on the speaker via TTS — use a friendly user-facing reminder. "
                        "- false (default): send message to yourself as a chat task — use an explicit self-instruction to call tools / perform actions. "
                        "Forced false when command is set."
                    ),
                },
                "target_channel": {
                    "type": "string",
                    "description": "xiaozhi (default).",
                },
                "target_id": {
                    "type": "string",
                    "description": "device_id override.",
                },
            },
            "required": ["action"],
        },
    },
}


@register_function("cron", CRON_FUNCTION_DESC, ToolType.SYSTEM_CTL)
def cron(
    conn: "ConnectionHandler",
    action: str,
    message: str | None = None,
    command: str | None = None,
    command_confirm: bool | None = None,
    at_seconds: int | None = None,
    every_seconds: int | None = None,
    cron_expr: str | None = None,
    job_id: str | None = None,
    deliver: bool | None = None,
    target_channel: str | None = None,
    target_id: str | None = None,
):
    try:
        svc = get_cron_service()
    except RuntimeError as exc:
        return ActionResponse(Action.ERROR, str(exc), None)

    action = (action or "").strip().lower()
    if action == "add":
        return _add_job(
            conn,
            svc,
            message=message,
            command=command,
            command_confirm=bool(command_confirm),
            at_seconds=at_seconds,
            every_seconds=every_seconds,
            cron_expr=cron_expr,
            deliver=deliver,
            target_channel=target_channel,
            target_id=target_id,
        )
    if action == "list":
        return _list_jobs(svc)
    if action == "remove":
        return _remove_job(svc, job_id)
    if action == "enable":
        return _enable_job(svc, job_id, True)
    if action == "disable":
        return _enable_job(svc, job_id, False)
    return ActionResponse(Action.ERROR, f"unknown action: {action}", None)


def _add_job(
    conn,
    svc,
    *,
    message,
    command,
    command_confirm,
    at_seconds,
    every_seconds,
    cron_expr,
    deliver,
    target_channel,
    target_id,
):
    device_id = target_id or conn.device_id
    if not device_id:
        return ActionResponse(
            Action.ERROR, "device_id not available for cron job", None
        )
    if not message:
        return ActionResponse(Action.ERROR, "message is required for add", None)

    schedule = _build_schedule(at_seconds, every_seconds, cron_expr)
    if schedule is None:
        return ActionResponse(
            Action.ERROR,
            "one of at_seconds, every_seconds, or cron_expr is required",
            None,
        )

    deliver_value = bool(deliver) if deliver is not None else False
    command = (command or "").strip()
    if command:
        if not is_exec_enabled(conn.config):
            return ActionResponse(
                Action.ERROR, "command execution is disabled", None
            )
        if not svc.allow_command and not command_confirm:
            return ActionResponse(
                Action.ERROR,
                "command_confirm=true is required when allow_command is disabled",
                None,
            )
        deliver_value = False

    channel = target_channel or "xiaozhi"
    name = message[:30]
    job = svc.add_job(
        name=name,
        schedule=schedule,
        message=message,
        deliver=deliver_value,
        channel=channel,
        target_id=device_id,
        command=command,
    )
    schedule_info = _format_schedule_info(schedule)
    return ActionResponse(
        Action.REQLLM,
        (
            f"Cron job scheduled. id={job['id']}, message={message!r}, "
            f"schedule={schedule_info}, deliver_at_fire={deliver_value}. "
            f"Briefly confirm to the user in Vietnamese that the reminder is set; "
            f"do not repeat the full reminder message (that will be spoken at fire time)."
        ),
        None,
    )


def _format_schedule_info(schedule: dict) -> str:
    kind = schedule.get("kind")
    if kind == "at":
        at_ms = int(schedule.get("atMs") or 0)
        seconds = max(0, (at_ms - int(time.time() * 1000)) // 1000)
        return f"one-time in {seconds}s"
    if kind == "every":
        every_ms = int(schedule.get("everyMs") or 0)
        return f"every {every_ms // 1000}s"
    if kind == "cron":
        return f"cron {schedule.get('expr', '')} ({schedule.get('tz', svc_tz_default())})"
    return "unknown"


def _build_schedule(at_seconds, every_seconds, cron_expr):
    if at_seconds is not None and int(at_seconds) > 0:
        at_ms = int(time.time() * 1000) + int(at_seconds) * 1000
        return {"kind": "at", "atMs": at_ms}
    if every_seconds is not None and int(every_seconds) > 0:
        return {"kind": "every", "everyMs": int(every_seconds) * 1000}
    if cron_expr:
        return {
            "kind": "cron",
            "expr": cron_expr,
            "tz": svc_tz_default(),
        }
    return None


def svc_tz_default():
    return "Asia/Ho_Chi_Minh"


def _list_jobs(svc):
    jobs = svc.list_jobs(include_disabled=False)
    if not jobs:
        return ActionResponse(Action.REQLLM, "No scheduled jobs", None)
    lines = ["Scheduled jobs:"]
    for job in jobs:
        schedule = job.get("schedule") or {}
        kind = schedule.get("kind")
        if kind == "every" and schedule.get("everyMs"):
            info = f"every {int(schedule['everyMs']) // 1000}s"
        elif kind == "cron":
            info = schedule.get("expr", "cron")
        elif kind == "at":
            info = "one-time"
        else:
            info = "unknown"
        lines.append(f"- {job.get('name')} (id: {job.get('id')}, {info})")
    return ActionResponse(Action.REQLLM, "\n".join(lines), None)


def _remove_job(svc, job_id):
    if not job_id:
        return ActionResponse(Action.ERROR, "job_id is required for remove", None)
    if svc.remove_job(job_id):
        return ActionResponse(
            Action.RECORD,
            f"Cron job removed: {job_id}",
            f"Đã xóa job {job_id}",
        )
    return ActionResponse(Action.ERROR, f"Job {job_id} not found", None)


def _enable_job(svc, job_id, enabled: bool):
    if not job_id:
        return ActionResponse(
            Action.ERROR, "job_id is required for enable/disable", None
        )
    job = svc.enable_job(job_id, enabled)
    if job is None:
        return ActionResponse(Action.ERROR, f"Job {job_id} not found", None)
    status = "enabled" if enabled else "disabled"
    return ActionResponse(
        Action.RECORD,
        f"Cron job '{job.get('name')}' {status}",
        f"Job {job.get('name')} đã {status}",
    )
