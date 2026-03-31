"""Environment file parsing helpers."""

from __future__ import annotations

import os  # file/path checks for env loading
from typing import Dict  # type hints for env dict


def load_env_file(path: str = ".env") -> Dict[str, str]:
    """Load key=value pairs from a .env-style file."""
    env: Dict[str, str] = {}
    if not os.path.exists(path):
        return env

    with open(path, "r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def str_to_bool(value: str | bool | None, fallback: bool = False) -> bool:
    """Parse common truthy/falsey values with a fallback."""
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return fallback
