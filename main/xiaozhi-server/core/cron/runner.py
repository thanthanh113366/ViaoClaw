import os
import re
import subprocess
from typing import Any

from config.logger import setup_logging
from core.cron.store import resolve_data_path

TAG = __name__
logger = setup_logging()


class ExecRunner:
    def __init__(self, config: dict):
        cron_cfg = config.get("cron") or {}
        exec_cfg = cron_cfg.get("exec") or {}
        workspace = exec_cfg.get("workspace", "/home/mq/.xiaoclaw/workspace")
        self.workspace = resolve_data_path(workspace) if not os.path.isabs(workspace) else workspace
        self.timeout_seconds = int(exec_cfg.get("timeout_seconds", 60))
        self.max_output_bytes = int(exec_cfg.get("max_output_bytes", 65536))
        self.allow_network = bool(exec_cfg.get("allow_network", True))
        patterns = exec_cfg.get("deny_patterns") or [
            r"rm\s+-rf",
            r"mkfs",
            r":\(\)\s*\{",
        ]
        self._deny_patterns = [re.compile(pattern) for pattern in patterns]

    def _ensure_workspace(self) -> None:
        os.makedirs(self.workspace, exist_ok=True)

    def _guard_command(self, command: str) -> str | None:
        for pattern in self._deny_patterns:
            if pattern.search(command):
                return f"command blocked by deny pattern: {pattern.pattern}"
        return None

    def run(self, command: str) -> str:
        blocked = self._guard_command(command)
        if blocked:
            raise RuntimeError(blocked)

        self._ensure_workspace()
        env = os.environ.copy()
        if not self.allow_network:
            env["http_proxy"] = "http://127.0.0.1:9"
            env["https_proxy"] = "http://127.0.0.1:9"

        try:
            completed = subprocess.run(
                ["/bin/bash", "-c", command],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"command timed out after {self.timeout_seconds}s"
            ) from exc

        output_parts = []
        if completed.stdout:
            output_parts.append(completed.stdout.rstrip())
        if completed.stderr:
            output_parts.append(completed.stderr.rstrip())
        output = "\n".join(part for part in output_parts if part)
        if len(output.encode("utf-8")) > self.max_output_bytes:
            output = output.encode("utf-8")[: self.max_output_bytes].decode(
                "utf-8", errors="ignore"
            )
            output += "\n...(truncated)"

        if completed.returncode != 0:
            detail = output or f"exit code {completed.returncode}"
            raise RuntimeError(detail)

        return f"Scheduled command '{command}' executed:\n{output or '(no output)'}"
