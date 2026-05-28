from typing import Optional

from core.exec.runner import ExecRunner, exec_config

_exec_runner: Optional[ExecRunner] = None
_exec_config_key: tuple | None = None


def is_exec_enabled(config: dict) -> bool:
    return bool(exec_config(config).get("enabled", True))


def get_exec_runner(config: dict) -> ExecRunner:
    global _exec_runner, _exec_config_key
    cfg_key = (
        is_exec_enabled(config),
        exec_config(config).get("workspace"),
        exec_config(config).get("timeout_seconds"),
        exec_config(config).get("max_output_bytes"),
        exec_config(config).get("allow_network"),
        tuple(exec_config(config).get("deny_patterns") or ()),
    )
    if _exec_runner is None or _exec_config_key != cfg_key:
        _exec_runner = ExecRunner(config)
        _exec_config_key = cfg_key
    return _exec_runner
