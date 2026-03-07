from __future__ import annotations

import json
import os
from typing import Any, Dict

DEFAULT_CONFIG_PATH = "config.json"


def load_config(path: str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file) or {}
    if not isinstance(data, dict):
        raise ValueError("Config JSON must be a JSON object at the top level.")
    data.setdefault("login", {})
    data.setdefault("crawl", {})
    data["crawl"].setdefault("elements", [])
    return data
