"""Config loading helpers for crawler settings."""

from __future__ import annotations

import json  # parse config JSON
import os  # file existence checks
from typing import Any, Dict  # type hints

DEFAULT_CONFIG_PATH = os.path.join("configs", "base.json")


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config JSON must be an object at top level: {path}")
    return data


def _load_modules_from_dir(modules_dir: str) -> Dict[str, Dict[str, Any]]:
    if not os.path.isdir(modules_dir):
        return {}
    modules: Dict[str, Dict[str, Any]] = {}
    for name in sorted(os.listdir(modules_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(modules_dir, name)
        if not os.path.isfile(path):
            continue
        payload = _read_json(path)
        module_name = os.path.splitext(name)[0]
        modules[module_name] = payload
    return modules


def _resolve_modules(
    selectors: Dict[str, Any],
    base_dir: str,
) -> Dict[str, Dict[str, Any]]:
    modules: Dict[str, Dict[str, Any]] = {}

    raw_modules = selectors.get("modules")
    if isinstance(raw_modules, dict):
        for name, payload in raw_modules.items():
            if isinstance(payload, dict):
                modules[str(name)] = payload
            elif isinstance(payload, str):
                path = payload
                if not os.path.isabs(path):
                    if not os.path.exists(path):
                        path = os.path.join(base_dir, path)
                if os.path.exists(path):
                    modules[str(name)] = _read_json(path)

    if not modules:
        modules_dir = selectors.get("modules_dir")
        if isinstance(modules_dir, str) and modules_dir.strip():
            path = modules_dir.strip()
            if not os.path.isabs(path):
                if not os.path.exists(path):
                    path = os.path.join(base_dir, path)
            modules = _load_modules_from_dir(path)

    return modules


def load_config(path: str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Load base config and selector module configs, with legacy fallback."""
    resolved_path = path
    if not os.path.exists(resolved_path):
        if path == DEFAULT_CONFIG_PATH and os.path.exists("config.json"):
            resolved_path = "config.json"
        else:
            raise FileNotFoundError(f"Config file not found: {path}")

    data = _read_json(resolved_path)

    if isinstance(data.get("config_base"), str):
        base_path = data["config_base"]
        if not os.path.isabs(base_path):
            base_path = os.path.join(os.path.dirname(resolved_path), base_path)
        data = _read_json(base_path)
        resolved_path = base_path

    if not isinstance(data, dict):
        raise ValueError("Config JSON must be a JSON object at the top level.")

    data.setdefault("login", {})
    data.setdefault("crawl", {})
    data["crawl"].setdefault("elements", [])

    selectors = data.get("selectors")
    if isinstance(selectors, dict):
        base_dir = os.path.dirname(resolved_path) or "."
        modules = _resolve_modules(selectors, base_dir)
        if modules:
            selectors = dict(selectors)
            selectors["modules"] = modules
            data["selectors"] = selectors

    return data
