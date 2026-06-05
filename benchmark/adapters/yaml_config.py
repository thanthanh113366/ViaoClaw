from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml


def resolve_env_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped.startswith("${") and stripped.endswith("}") and len(stripped) > 3:
        return os.getenv(stripped[2:-1], "")
    if stripped.startswith("$") and len(stripped) > 1:
        return os.getenv(stripped[1:], "")
    return value


def read_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def merge_configs(default_config: Mapping[str, Any], custom_config: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(default_config, Mapping) or not isinstance(custom_config, Mapping):
        return dict(custom_config)
    merged = dict(default_config)
    for key, value in custom_config.items():
        if key in merged and isinstance(merged[key], Mapping) and isinstance(value, Mapping):
            merged[key] = merge_configs(merged[key], value)
        else:
            merged[key] = value
    return merged
