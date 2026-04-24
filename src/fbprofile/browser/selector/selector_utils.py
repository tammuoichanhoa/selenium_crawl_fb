"""
selector_utils.py
-----------------
Tải selector config từ file JSON, build locator chain và resolve namespace (profile/group).
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

from src.utils.selectors import validate_selector_payload


MODULE_SELECTOR_CONFIG_PATHS = {
    "profile": Path(__file__).resolve().parents[1] / "configs" / "modules" / "profile.json",
    "group": "/home/baoanh/Desktop/fb_crawler/selenium_crawl_fb/configs/modules/group.json",
}


@lru_cache(maxsize=None)
def load_selector_config(module_name: str) -> dict:
    config_path = (
        MODULE_SELECTOR_CONFIG_PATHS.get(module_name)
        or MODULE_SELECTOR_CONFIG_PATHS["profile"]
    )
    with open(config_path, "r", encoding="utf-8") as f:
        return validate_selector_payload(json.load(f))


def build_locator_chain(selector_name: str) -> List[Dict[str, Any]]:
    module_name = selector_name.split(".", 1)[0] if "." in selector_name else "profile"
    selector_cfg = (
        load_selector_config(module_name).get("elements", {}).get(selector_name)
    )
    if not isinstance(selector_cfg, dict):
        return []

    locators: List[Dict[str, Any]] = []
    primary = selector_cfg.get("primary")
    if isinstance(primary, dict):
        locators.append(dict(primary))

    fallbacks = selector_cfg.get("fallbacks")
    if isinstance(fallbacks, list):
        locators.extend(dict(fb) for fb in fallbacks if isinstance(fb, dict))

    return locators


def resolve_post_selector_namespace(source_url: str) -> str:
    if isinstance(source_url, str) and "/groups/" in source_url:
        return "group"
    return "profile"


def post_selector(selector_suffix: str, source_url: str) -> str:
    return f"{resolve_post_selector_namespace(source_url)}.posts.{selector_suffix}"
