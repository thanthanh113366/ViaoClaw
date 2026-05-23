import json
import os
import secrets
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from config.config_loader import get_project_dir
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()


def resolve_data_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.join(get_project_dir(), path)


def _atomic_write_json(path: str, data: dict) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory or None, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def generate_job_id() -> str:
    return "job_" + secrets.token_hex(4)


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _empty_jobs_store() -> dict:
    return {"version": 1, "jobs": []}


def _empty_pending_store() -> dict:
    return {"version": 1, "items": []}


class JobStore:
    def __init__(self, config: dict):
        cron_cfg = config.get("cron") or {}
        self.path = resolve_data_path(cron_cfg.get("store_path", "data/cron/jobs.json"))
        self._lock = threading.Lock()
        self._store = self.load()

    def load(self) -> dict:
        if not os.path.exists(self.path):
            store = _empty_jobs_store()
            save_store(self.path, store)
            return store
        with open(self.path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return _empty_jobs_store()
        data.setdefault("version", 1)
        data.setdefault("jobs", [])
        return data

    def save(self) -> None:
        save_store(self.path, self._store)

    def get_jobs(self) -> list[dict]:
        with self._lock:
            return list(self._store.get("jobs", []))

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            for job in self._store.get("jobs", []):
                if job.get("id") == job_id:
                    return dict(job)
        return None

    def add_job(self, job: dict) -> dict:
        with self._lock:
            self._store.setdefault("jobs", []).append(job)
            self.save()
            return dict(job)

    def update_job(self, job: dict) -> None:
        with self._lock:
            jobs = self._store.setdefault("jobs", [])
            for index, existing in enumerate(jobs):
                if existing.get("id") == job.get("id"):
                    jobs[index] = job
                    self.save()
                    return
            raise KeyError(f"job not found: {job.get('id')}")

    def remove_job(self, job_id: str) -> bool:
        with self._lock:
            jobs = self._store.setdefault("jobs", [])
            new_jobs = [job for job in jobs if job.get("id") != job_id]
            if len(new_jobs) == len(jobs):
                return False
            self._store["jobs"] = new_jobs
            self.save()
            return True

    def replace_jobs(self, jobs: list[dict]) -> None:
        with self._lock:
            self._store["jobs"] = jobs
            self.save()


def save_store(path: str, store: dict) -> None:
    _atomic_write_json(path, store)


class PendingStore:
    def __init__(self, config: dict):
        cron_cfg = config.get("cron") or {}
        self.path = resolve_data_path(
            cron_cfg.get("pending_path", "data/cron/pending_notifications.json")
        )
        self.ttl_hours = int(cron_cfg.get("pending_ttl_hours", 24))
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            store = _empty_pending_store()
            _atomic_write_json(self.path, store)
            return store
        with open(self.path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return _empty_pending_store()
        data.setdefault("version", 1)
        data.setdefault("items", [])
        return data

    def _save(self, store: dict) -> None:
        _atomic_write_json(self.path, store)

    def _purge_expired(self, items: list[dict]) -> list[dict]:
        if self.ttl_hours <= 0:
            return items
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.ttl_hours)
        kept = []
        for item in items:
            created_at = item.get("created_at")
            if not created_at:
                kept.append(item)
                continue
            try:
                created = datetime.fromisoformat(created_at)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created >= cutoff:
                    kept.append(item)
            except ValueError:
                kept.append(item)
        return kept

    def append(
        self,
        *,
        channel: str,
        target_id: str,
        text: str,
        mode: str,
        source: str = "cron",
        job_id: str | None = None,
    ) -> dict:
        item = {
            "id": "n_" + secrets.token_hex(4),
            "channel": channel,
            "target_id": target_id,
            "text": text,
            "mode": mode,
            "source": source,
            "job_id": job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            store = self._load()
            items = store.setdefault("items", [])
            items.append(item)
            store["items"] = self._purge_expired(items)
            self._save(store)
        logger.bind(tag=TAG).info(
            f"[cron] pending appended target={target_id} mode={mode} job_id={job_id}"
        )
        return item

    def pop_for_device(self, target_id: str, limit: int = 5) -> list[dict]:
        with self._lock:
            store = self._load()
            items = self._purge_expired(store.get("items", []))
            matched = [item for item in items if item.get("target_id") == target_id]
            remaining = [item for item in items if item.get("target_id") != target_id]
            popped = matched[:limit]
            if popped:
                remaining.extend(matched[limit:])
            store["items"] = remaining
            self._save(store)
            return popped
