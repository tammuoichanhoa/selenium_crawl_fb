from __future__ import annotations

import json
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from logs.loging_config import logger
from src.utils.selectors import resolve_locator, validate_selector_payload

try:
    from .get_graphql_response import GraphQLTokens, extract_graphql_tokens_from_html
except ImportError:
    from groups.get_graphql_response import (  # type: ignore
        GraphQLTokens,
        extract_graphql_tokens_from_html,
    )


# GROUP_SELECTOR_CONFIG_PATH = PROJECT_ROOT / "configs" / "modules" / "group.json"
GROUP_SELECTOR_CONFIG_PATH = "/home/baoanh/Desktop/fb_crawler/selenium_crawl_fb/configs/modules/group.json"

@lru_cache(maxsize=1)
def _load_group_selector_config() -> dict[str, Any]:
    with open(GROUP_SELECTOR_CONFIG_PATH, "r", encoding="utf-8") as config_file:
        return validate_selector_payload(json.load(config_file))


def _get_group_selector_entry(selector_name: str) -> dict[str, Any] | None:
    return _load_group_selector_config().get("elements", {}).get(selector_name)


def _build_group_locator_chain(selector_name: str) -> list[dict[str, Any]]:
    selector_cfg = _get_group_selector_entry(selector_name)
    if not isinstance(selector_cfg, dict):
        logger.warning("[GROUP_MEDIA] Missing group selector config: %s", selector_name)
        return []

    locators: list[dict[str, Any]] = []
    primary = selector_cfg.get("primary")
    if isinstance(primary, dict):
        locators.append(dict(primary))

    fallbacks = selector_cfg.get("fallbacks")
    if isinstance(fallbacks, list):
        locators.extend(dict(fallback) for fallback in fallbacks if isinstance(fallback, dict))

    return locators


def _resolve_group_wait(selector_name: str, timeout: int | None = None) -> tuple[str, float]:
    selector_cfg = _get_group_selector_entry(selector_name) or {}
    wait_cfg = selector_cfg.get("wait")
    wait_cfg = wait_cfg if isinstance(wait_cfg, dict) else {}

    state = str(wait_cfg.get("state") or "presence").strip().lower()
    if state not in {"presence", "visible", "clickable"}:
        state = "presence"

    timeout_ms = wait_cfg.get("timeout_ms")
    try:
        seconds = float(timeout_ms) / 1000 if timeout_ms is not None else float(timeout or 5)
    except (TypeError, ValueError):
        seconds = float(timeout or 5)

    if timeout is not None:
        seconds = float(timeout)

    return state, seconds


def _find_group_elements_by_selector(driver, selector_name: str, timeout: int | None = None):
    locators = _build_group_locator_chain(selector_name)
    if not locators:
        return []

    wait_state, wait_seconds = _resolve_group_wait(selector_name, timeout=timeout)
    condition_map = {
        "presence": EC.presence_of_element_located,
        "visible": EC.visibility_of_element_located,
        "clickable": EC.element_to_be_clickable,
    }
    condition = condition_map[wait_state]

    for locator in locators:
        try:
            by, value = resolve_locator(locator)
            WebDriverWait(driver, wait_seconds).until(condition((by, value)))
            elements = driver.find_elements(by, value)
            if elements:
                return elements
        except Exception:
            continue

    return []


def _extract_group_attr_from_selector(
    driver,
    selector_name: str,
    attr: str,
    timeout: int | None = None,
) -> str | None:
    elements = _find_group_elements_by_selector(driver, selector_name, timeout=timeout)
    for element in elements:
        try:
            value = element.get_attribute(attr)
        except Exception:
            value = None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def resolve_graphql_tokens(
    driver,
    *,
    cookie_header: str,
    group_url: str,
    fb_dtsg: str | None = None,
) -> GraphQLTokens:
    candidates: list[GraphQLTokens] = []

    try:
        candidates.append(
            extract_graphql_tokens_from_html(
                driver.page_source or "",
                cookie_header,
                fb_dtsg=fb_dtsg,
            )
        )
    except Exception:
        pass

    if not any(candidate.is_complete for candidate in candidates):
        try:
            driver.get(group_url)
            time.sleep(2)
            candidates.append(
                extract_graphql_tokens_from_html(
                    driver.page_source or "",
                    cookie_header,
                    fb_dtsg=fb_dtsg,
                )
            )
        except Exception:
            pass

    for candidate in candidates:
        if candidate.is_complete:
            return candidate

    fallback = candidates[-1] if candidates else GraphQLTokens(fb_dtsg=fb_dtsg)
    if fallback.fb_dtsg and fallback.av:
        return fallback

    raise ValueError(
        "Không lấy được token GraphQL đầy đủ từ phiên Facebook hiện tại. "
        "Hãy đăng nhập lại hoặc truyền FB_DTSG hợp lệ."
    )


def _extract_group_cover_photo(driver) -> str | None:
    cover_url = _extract_group_attr_from_selector(driver, "group.cover", "src", timeout=10)
    if cover_url:
        return cover_url

    elements = _find_group_elements_by_selector(driver, "group.cover", timeout=10)
    for element in elements:
        for child_tag, attr_name in (
            ("img", "src"),
            ("image", "xlink:href"),
            ("image", "href"),
        ):
            try:
                child = element.find_element("tag name", child_tag)
                value = child.get_attribute(attr_name)
            except Exception:
                value = None
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _extract_group_text_from_selector(
    driver,
    selector_name: str,
    timeout: int | None = None,
) -> str | None:
    elements = _find_group_elements_by_selector(driver, selector_name, timeout=timeout)
    for element in elements:
        try:
            text_value = (element.text or "").strip()
        except Exception:
            continue
        if text_value:
            return text_value
    return None


def extract_group_general_info(driver, *, group_url: str) -> dict[str, Any]:
    if group_url not in (driver.current_url or ""):
        driver.get(group_url)
        time.sleep(2)

    return {
        "name": _extract_group_text_from_selector(driver, "group.name", timeout=10),
        "members": _extract_group_text_from_selector(driver, "group.members_count", timeout=8),
        "cover_photo": _extract_group_cover_photo(driver),
        "privacy": _extract_group_text_from_selector(driver, "group.privacy", timeout=5),
        "description": _extract_group_text_from_selector(driver, "group.description", timeout=8),
    }


def merge_group_general_info(result: dict[str, Any], group_general_info: dict[str, Any]) -> dict[str, Any]:
    basic_info = result.setdefault("basic_info", {})

    name = group_general_info.get("name")
    if isinstance(name, str) and name.strip():
        basic_info["name"] = name.strip()

    members = group_general_info.get("members")
    if isinstance(members, str) and members.strip():
        basic_info["members"] = members.strip()

    cover_photo = group_general_info.get("cover_photo")
    if isinstance(cover_photo, str) and cover_photo.strip():
        basic_info["cover_photo"] = cover_photo.strip()

    privacy = group_general_info.get("privacy")
    if isinstance(privacy, str) and privacy.strip():
        basic_info["privacy"] = privacy.strip()

    description = group_general_info.get("description")
    if isinstance(description, str) and description.strip():
        introduction = result.setdefault("introduction", {})
        info_items = introduction.setdefault("info", [])
        if description.strip() not in info_items:
            info_items.append(description.strip())

    return result

