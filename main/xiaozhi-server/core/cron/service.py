import threading
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config.logger import setup_logging
from core.cron.fire import CronFireHandler
from core.cron.store import JobStore, _now_ms, generate_job_id

TAG = __name__
logger = setup_logging()

_cron_service: Optional["CronService"] = None


def ensure_cron_started(config: dict) -> Optional["CronService"]:
    global _cron_service
    cron_cfg = config.get("cron") or {}
    if not cron_cfg.get("enabled", False):
        return None
    if _cron_service is not None:
        if not _cron_service._running:
            _cron_service.start()
        return _cron_service
    from core.cron.registry import init_connection_registry

    registry = init_connection_registry(config)
    _cron_service = _build_cron_service(config, registry)
    if _cron_service is not None:
        _cron_service.start()
    return _cron_service


def init_cron_service(config: dict, registry) -> Optional["CronService"]:
    global _cron_service
    cron_cfg = config.get("cron") or {}
    if not cron_cfg.get("enabled", False):
        _cron_service = None
        return None
    _cron_service = _build_cron_service(config, registry)
    return _cron_service


def _build_cron_service(config: dict, registry) -> "CronService":
    from core.exec.service import get_exec_runner

    job_store = JobStore(config)
    exec_runner = get_exec_runner(config)
    fire_handler = CronFireHandler(
        registry, registry.pending_store, exec_runner, config
    )
    return CronService(config, job_store, fire_handler)


def get_cron_service() -> "CronService":
    if _cron_service is None:
        raise RuntimeError("CronService chưa khởi tạo hoặc cron.enabled=false")
    return _cron_service


def compute_next_run_ms(schedule: dict, now_ms: int | None = None) -> int | None:
    now_ms = now_ms if now_ms is not None else _now_ms()
    kind = schedule.get("kind")
    if kind == "at":
        at_ms = schedule.get("atMs")
        if at_ms is None:
            return None
        return int(at_ms) if int(at_ms) > now_ms else None
    if kind == "every":
        every_ms = int(schedule.get("everyMs") or 0)
        return now_ms + every_ms if every_ms > 0 else None
    if kind == "cron":
        expr = schedule.get("expr") or ""
        tz_name = schedule.get("tz") or "Asia/Ho_Chi_Minh"
        if not expr:
            return None
        trigger = CronTrigger.from_crontab(expr, timezone=ZoneInfo(tz_name))
        next_dt = trigger.get_next_fire_time(
            None, datetime.fromtimestamp(now_ms / 1000, tz=ZoneInfo(tz_name))
        )
        if next_dt is None:
            return None
        return int(next_dt.timestamp() * 1000)
    return None


class CronService:
    def __init__(self, config: dict, job_store: JobStore, fire_handler: CronFireHandler):
        self.config = config
        self.cron_cfg = config.get("cron") or {}
        self.job_store = job_store
        self.fire_handler = fire_handler
        self.tz = ZoneInfo(self.cron_cfg.get("timezone", "Asia/Ho_Chi_Minh"))
        self.allow_command = bool(self.cron_cfg.get("allow_command", True))
        self._lock = threading.Lock()
        self._scheduler = BackgroundScheduler(timezone=self.tz)
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        for job in self.job_store.get_jobs():
            if job.get("enabled"):
                self._schedule_job(job)
        self._scheduler.start()
        self._running = True
        logger.bind(tag=TAG).info(
            f"[cron] started jobs={len(self.job_store.get_jobs())} store={self.job_store.path}"
        )

    def stop(self) -> None:
        if not self._running:
            return
        self._scheduler.shutdown(wait=False)
        self._running = False
        logger.bind(tag=TAG).info("[cron] stopped")

    def list_jobs(self, include_disabled: bool = False) -> list[dict]:
        jobs = self.job_store.get_jobs()
        if include_disabled:
            return jobs
        return [job for job in jobs if job.get("enabled")]

    def add_job(
        self,
        name: str,
        schedule: dict,
        message: str,
        deliver: bool,
        channel: str,
        target_id: str,
        command: str = "",
    ) -> dict:
        now_ms = _now_ms()
        delete_after_run = schedule.get("kind") == "at"
        job = {
            "id": generate_job_id(),
            "name": name,
            "enabled": True,
            "schedule": schedule,
            "payload": {
                "kind": "agent_turn",
                "message": message,
                "command": command or "",
                "deliver": deliver,
                "channel": channel,
                "to": target_id,
            },
            "state": {
                "nextRunAtMs": compute_next_run_ms(schedule, now_ms),
                "lastRunAtMs": None,
                "lastStatus": "",
                "lastError": "",
            },
            "createdAtMs": now_ms,
            "updatedAtMs": now_ms,
            "deleteAfterRun": delete_after_run,
        }
        saved = self.job_store.add_job(job)
        if saved.get("enabled"):
            self._schedule_job(saved)
        logger.bind(tag=TAG).info(
            f"[cron] added job id={saved['id']} name={name}"
        )
        return saved

    def update_job(self, job: dict) -> None:
        job["updatedAtMs"] = _now_ms()
        self.job_store.update_job(job)
        if job.get("enabled"):
            self._schedule_job(job)
        else:
            self._unschedule_job(job["id"])

    def remove_job(self, job_id: str) -> bool:
        removed = self.job_store.remove_job(job_id)
        if removed:
            self._unschedule_job(job_id)
            logger.bind(tag=TAG).info(f"[cron] removed job id={job_id}")
        return removed

    def enable_job(self, job_id: str, enabled: bool) -> dict | None:
        job = self.job_store.get_job(job_id)
        if job is None:
            return None
        job["enabled"] = enabled
        job["updatedAtMs"] = _now_ms()
        if enabled:
            job.setdefault("state", {})["nextRunAtMs"] = compute_next_run_ms(
                job.get("schedule") or {}, _now_ms()
            )
            self._schedule_job(job)
        else:
            job.setdefault("state", {})["nextRunAtMs"] = None
            self._unschedule_job(job_id)
        self.job_store.update_job(job)
        return job

    def _scheduler_job_id(self, job_id: str) -> str:
        return f"cron:{job_id}"

    def _unschedule_job(self, job_id: str) -> None:
        scheduler_id = self._scheduler_job_id(job_id)
        if self._scheduler.get_job(scheduler_id):
            self._scheduler.remove_job(scheduler_id)

    def _build_trigger(self, schedule: dict):
        kind = schedule.get("kind")
        if kind == "at":
            at_ms = schedule.get("atMs")
            if at_ms is None:
                return None
            run_date = datetime.fromtimestamp(int(at_ms) / 1000, tz=self.tz)
            return DateTrigger(run_date=run_date)
        if kind == "every":
            every_ms = int(schedule.get("everyMs") or 0)
            if every_ms <= 0:
                return None
            return IntervalTrigger(seconds=every_ms / 1000, timezone=self.tz)
        if kind == "cron":
            expr = schedule.get("expr") or ""
            if not expr:
                return None
            tz_name = schedule.get("tz") or self.cron_cfg.get(
                "timezone", "Asia/Ho_Chi_Minh"
            )
            return CronTrigger.from_crontab(expr, timezone=ZoneInfo(tz_name))
        return None

    def _schedule_job(self, job: dict) -> None:
        job_id = job.get("id")
        if not job_id:
            return
        trigger = self._build_trigger(job.get("schedule") or {})
        if trigger is None:
            logger.bind(tag=TAG).warning(
                f"[cron] skip schedule invalid job id={job_id}"
            )
            return
        self._scheduler.add_job(
            self._on_fire,
            trigger=trigger,
            id=self._scheduler_job_id(job_id),
            args=[job_id],
            replace_existing=True,
            misfire_grace_time=3600,
        )

    def _on_fire(self, job_id: str) -> None:
        job = self.job_store.get_job(job_id)
        if not job or not job.get("enabled"):
            return
        start_ms = _now_ms()
        status = "ok"
        error = ""
        try:
            self.fire_handler.handle(job)
        except Exception as exc:
            status = "error"
            error = str(exc)
            logger.bind(tag=TAG).error(
                f"[cron] job id={job_id} failed: {error}", exc_info=True
            )

        job = self.job_store.get_job(job_id)
        if job is None:
            return

        state = job.setdefault("state", {})
        state["lastRunAtMs"] = start_ms
        state["lastStatus"] = status
        state["lastError"] = error
        job["updatedAtMs"] = _now_ms()

        schedule = job.get("schedule") or {}
        if schedule.get("kind") == "at":
            if job.get("deleteAfterRun", True):
                self.remove_job(job_id)
                return
            job["enabled"] = False
            state["nextRunAtMs"] = None
            self._unschedule_job(job_id)
        else:
            state["nextRunAtMs"] = compute_next_run_ms(schedule, _now_ms())

        self.job_store.update_job(job)
        logger.bind(tag=TAG).info(
            f"[cron] job id={job_id} finished status={status}"
        )
