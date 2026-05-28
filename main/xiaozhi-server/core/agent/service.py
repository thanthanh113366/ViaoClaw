from __future__ import annotations

from typing import Any, Optional

from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

_agent_runtime: Optional["AgentRuntime"] = None


def _xiaoclaw_config(config: dict) -> dict:
    return config.get("xiaoclaw") or {}


def is_bridge_enabled(config: dict) -> bool:
    xc = _xiaoclaw_config(config)
    bridge = xc.get("bridge") or {}
    if "enabled" in bridge:
        return bool(bridge.get("enabled"))
    return bool(xc.get("enabled"))


def is_agent_enabled(config: dict) -> bool:
    xc = _xiaoclaw_config(config)
    agent = xc.get("agent") or {}
    return bool(agent.get("enabled"))


def telegram_cfg(config: dict) -> dict:
    return _xiaoclaw_config(config).get("telegram") or {}


def is_telegram_enabled(config: dict) -> bool:
    return bool(telegram_cfg(config).get("enabled"))


def validate_xiaoclaw_config(config: dict) -> None:
    if is_bridge_enabled(config) and is_agent_enabled(config):
        raise ValueError(
            "xiaoclaw.bridge.enabled and xiaoclaw.agent.enabled cannot both be true"
        )
    if is_telegram_enabled(config):
        if not is_agent_enabled(config):
            raise ValueError("xiaoclaw.telegram.enabled requires xiaoclaw.agent.enabled")
        token = telegram_cfg(config).get("bot_token")
        if not token or not str(token).strip():
            raise ValueError(
                "xiaoclaw.telegram.enabled requires non-empty bot_token in config"
            )


def ensure_agent_started(config: dict) -> Optional["AgentRuntime"]:
    global _agent_runtime
    validate_xiaoclaw_config(config)
    if not is_agent_enabled(config):
        return None
    if _agent_runtime is not None:
        return _agent_runtime
    from core.agent.runtime import AgentRuntime

    _agent_runtime = AgentRuntime(config)
    logger.bind(tag=TAG).info("[xiaoclaw.agent] runtime created")
    return _agent_runtime


def get_agent_runtime() -> "AgentRuntime":
    if _agent_runtime is None:
        raise RuntimeError(
            "AgentRuntime chưa khởi tạo hoặc xiaoclaw.agent.enabled=false"
        )
    return _agent_runtime


def get_agent_runtime_optional() -> Optional["AgentRuntime"]:
    return _agent_runtime
