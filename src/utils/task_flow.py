"""Helpers for task dequeue, selector resolution, and preflight checks."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.error
import urllib.request
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qs, urlparse

from scripts.crawler import _normalize_selector_modules

from .env import str_to_bool
from .selector_remote import resolve_selector_payload
from .selectors import guard_fragile_locators, normalize_elements_config, validate_selector_payload


logger = logging.getLogger(__name__)


def parse_dequeue_payload(raw: str) -> Dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Dequeue response is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Dequeue response must be a JSON object.")
    return payload


def derive_step_status(result: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    login_ok = True
    open_link_ok = True
    fetch_info_ok = True

    error = result.get("error") if isinstance(result, dict) else None
    if isinstance(error, str) and error.strip():
        fetch_info_ok = False
        if error.startswith("login_failed"):
            login_ok = False
            open_link_ok = False
        else:
            open_link_ok = False

    return {
        "login": {"ok": login_ok},
        "open_link": {"ok": open_link_ok},
        "fetch_info": {"ok": fetch_info_ok, "data": result},
    }


def post_event(api_key: str, event_url: str, task_id: str, result: Dict[str, Any]) -> None:
    payload = {
        "task_id": task_id,
        "event_type": "complete",
        "payload": {
            "steps": derive_step_status(result),
        },
    }
    cmd = [
        "curl",
        "-sS",
        "-X",
        "POST",
        event_url,
        "-H",
        "Content-Type: application/json",
        "-H",
        f"Authorization: Bearer {api_key}",
        "-d",
        json.dumps(payload, ensure_ascii=False),
    ]
    response = subprocess.run(cmd, capture_output=True, text=True)
    if response.returncode != 0:
        logger.error(
            "[event] Failed to post task_id=%s: %s",
            task_id,
            response.stderr.strip(),
        )
    elif response.stdout.strip():
        logger.info("[event] Response for task_id=%s: %s", task_id, response.stdout.strip())


def extract_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("Dequeue response has no items to crawl.")
    return [item for item in items if isinstance(item, dict)]


def collect_uids(items: List[Dict[str, Any]]) -> List[str]:
    uids: List[str] = []
    for item in items:
        uid = item.get("uid")
        if isinstance(uid, str) and uid.strip():
            uids.append(uid.strip())
    if not uids:
        raise ValueError("No valid uid values found in dequeue items.")
    return uids


def extract_account_uid(item: Dict[str, Any]) -> str | None:
    account = item.get("account")
    if isinstance(account, dict):
        account_uid = account.get("uid")
        if account_uid is not None:
            return str(account_uid).strip() or None
    return None


def extract_account_cookie(
    item: Dict[str, Any],
    account_cookies: Dict[str, str],
) -> str | None:
    account = item.get("account")
    if isinstance(account, dict):
        cookies_value = account.get("cookies")
        if isinstance(cookies_value, str) and cookies_value.strip():
            return cookies_value.strip()
        uid = account.get("uid")
        if uid is not None:
            lookup = account_cookies.get(str(uid).strip())
            if lookup:
                return lookup
    return None


def load_account_cookies(path: str | None) -> Dict[str, str]:
    cookies: Dict[str, str] = {}
    if not path:
        return cookies
    if not os.path.exists(path):
        logger.warning("[account] Cookies file not found: %s", path)
        return cookies
    with open(path, "r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) < 6:
                continue
            uid = parts[0].strip()
            cookie_value = parts[5].strip()
            if uid and cookie_value:
                cookies[uid] = cookie_value
    return cookies


def infer_selector_module(
    items: List[Dict[str, Any]],
    selector_modules: Dict[str, Dict[str, Any]],
    explicit_module: str | None,
) -> str | None:
    if explicit_module:
        return explicit_module

    crawl_types: List[str] = []
    for item in items:
        types = item.get("crawl_types")
        if isinstance(types, list):
            crawl_types.extend(
                [str(value).lower() for value in types if value is not None]
            )

    if any("profile" in value for value in crawl_types):
        if "profile" in selector_modules:
            return "profile"
    if any("page" in value for value in crawl_types):
        if "page" in selector_modules:
            return "page"

    if "profile" in selector_modules:
        return "profile"
    if "page" in selector_modules:
        return "page"

    if selector_modules:
        return next(iter(selector_modules.keys()))
    return None


def _normalize_fb_url(raw: str) -> str:
    value = raw.strip()
    if not value:
        return value
    if "://" not in value:
        return f"https://{value}"
    return value


def _maybe_facebook_url(raw: str) -> str:
    value = raw.strip()
    if not value:
        return value
    if "://" in value:
        return value
    if "facebook.com" in value or value.startswith(("www.facebook.com", "m.facebook.com", "web.facebook.com")):
        return f"https://{value.lstrip('/')}"
    return value


def _is_facebook_host(host: str) -> bool:
    host = host.lower().strip()
    return host.endswith("facebook.com") or host.endswith("fb.com") or host.endswith("fb.me")


def precheck_facebook_uid(
    uid: str,
    *,
    timeout: float,
    user_agent: str,
) -> Tuple[str, str | None, str | None]:
    raw = uid.strip() if isinstance(uid, str) else ""
    if not raw:
        return "invalid", "empty_uid", None

    candidate = _maybe_facebook_url(raw)
    parsed = urlparse(candidate)
    if not parsed.netloc:
        return "unknown", "not_a_url", None

    host = parsed.netloc.lower()
    if not _is_facebook_host(host):
        return "invalid", "not_facebook_url", candidate

    if "://" not in candidate:
        candidate = _normalize_fb_url(candidate)

    headers = {"User-Agent": user_agent or "Mozilla/5.0"}
    request = urllib.request.Request(candidate, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.getcode()
            final_url = response.geturl()
            body = response.read(65536).decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        status = exc.code
        final_url = exc.geturl()
        try:
            body = exc.read(65536).decode("utf-8", errors="ignore")
        except Exception:
            body = ""
    except Exception as exc:
        logger.warning("[precheck] Failed to validate uid=%s: %s", uid, exc)
        return "unknown", f"precheck_failed: {exc}", candidate

    if status in (404, 410):
        return "invalid", f"http_{status}", final_url or candidate

    body_lower = body.lower()
    invalid_phrases = (
        "this page isn't available",
        "page isn't available",
        "sorry, this page isn't available",
        "content isn't available",
        "the link you followed may be broken",
    )
    if any(phrase in body_lower for phrase in invalid_phrases):
        return "invalid", "page_not_available", final_url or candidate

    return "valid", None, final_url or candidate


def infer_fb_type_from_url(uid: str | None) -> str | None:
    if not uid or not isinstance(uid, str):
        return None
    normalized = _normalize_fb_url(uid)
    parsed = urlparse(normalized)
    path = (parsed.path or "").strip("/").lower()
    query = parse_qs(parsed.query or "")

    if path.startswith("groups/") or path == "groups" or path.startswith("group.php"):
        return "group"
    if "gid" in query:
        return "group"

    if path.startswith("pages/") or path.startswith("pg/"):
        return "page"
    if "page_id" in query or "pageid" in query:
        return "page"

    if path.startswith("profile.php") or path.startswith("people/") or path.startswith("profile/"):
        return "profile"

    return None


def infer_module_from_crawl_types(
    crawl_types: Any,
    selector_modules: Dict[str, Dict[str, Any]],
) -> str | None:
    if not isinstance(crawl_types, list):
        return None
    types = [str(value).lower() for value in crawl_types if value is not None]
    has_profile = any("profile" in value for value in types)
    has_page = any("page" in value for value in types)
    has_group = any("group" in value for value in types)

    if has_group and "group" in selector_modules:
        return "group"
    if has_profile and not has_page and "profile" in selector_modules:
        return "profile"
    if has_page and not has_profile and "page" in selector_modules:
        return "page"
    return None


def fallback_selector_module(
    selector_modules: Dict[str, Dict[str, Any]],
) -> str | None:
    if len(selector_modules) == 1:
        return next(iter(selector_modules.keys()))
    if "profile" in selector_modules:
        return "profile"
    if "page" in selector_modules:
        return "page"
    if "group" in selector_modules:
        return "group"
    if selector_modules:
        return next(iter(selector_modules.keys()))
    return None


def infer_module_for_item(
    item: Dict[str, Any],
    selector_modules: Dict[str, Dict[str, Any]],
    explicit_module: str | None,
) -> str | None:
    if explicit_module:
        return explicit_module

    module = infer_module_from_crawl_types(item.get("crawl_types"), selector_modules)
    if module:
        return module

    module = infer_fb_type_from_url(item.get("uid"))
    if module and module in selector_modules:
        return module

    return fallback_selector_module(selector_modules)


def load_user_agents(user_agents_file: str, fallback: str) -> List[str]:
    user_agents: List[str] = []
    if os.path.exists(user_agents_file):
        with open(user_agents_file, "r", encoding="utf-8") as file:
            user_agents = [
                line.strip()
                for line in file
                if line.strip() and not line.lstrip().startswith("#")
            ]
        if not user_agents:
            logger.warning(
                "[user-agent] %s is empty; using USER_AGENT from .env",
                user_agents_file,
            )
    else:
        logger.warning(
            "[user-agent] %s not found; using USER_AGENT from .env",
            user_agents_file,
        )
    if user_agents:
        return user_agents
    return [fallback] if fallback else []


def build_selector_config(
    config: Dict[str, Any],
    crawl_cfg: Dict[str, Any],
    env: Dict[str, Any],
    selector_module: str | None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any] | None, Dict[str, Any] | None]:
    selector_root = None
    if isinstance(config.get("selectors"), dict):
        selector_root = config["selectors"]
    elif isinstance(crawl_cfg.get("selectors"), dict):
        selector_root = crawl_cfg["selectors"]

    selector_modules = _normalize_selector_modules(selector_root)

    if selector_module and selector_module not in selector_modules:
        logger.warning(
            "[selectors] module '%s' not found in config; using fallback.",
            selector_module,
        )
        selector_module = None

    if selector_module is None:
        if len(selector_modules) == 1:
            selector_module = next(iter(selector_modules.keys()))
        elif "page" in selector_modules:
            selector_module = "page"
        elif selector_modules:
            selector_module = next(iter(selector_modules.keys()))

    local_selector = (
        selector_modules.get(selector_module)
        if selector_modules
        else selector_root
    )

    selector_payload = None
    selector_debug_cfg: Dict[str, Any] | None = None
    default_wait_cfg: Dict[str, Any] | None = None
    selector_source = "none"

    resolved_payload, selector_source = resolve_selector_payload(local_selector, env)
    if resolved_payload is not None:
        try:
            selector_payload = validate_selector_payload(resolved_payload)
        except ValueError as exc:
            logger.warning(
                "[selectors] invalid selector payload from %s: %s",
                selector_source,
                exc,
            )
            if local_selector and local_selector is not resolved_payload:
                selector_payload = validate_selector_payload(local_selector)
                selector_source = "local"
            else:
                raise

    if selector_payload is not None:
        selector_debug_cfg = {}
        if isinstance(selector_payload.get("debug"), dict):
            selector_debug_cfg.update(selector_payload["debug"])
        if isinstance(selector_payload.get("defaults"), dict):
            defaults_debug = selector_payload["defaults"].get("debug")
            if isinstance(defaults_debug, dict):
                selector_debug_cfg.update(defaults_debug)
        if "SELECTOR_DEBUG" in env:
            selector_debug_cfg["enabled"] = str_to_bool(env.get("SELECTOR_DEBUG"))
        if "SELECTOR_LOG_CONFIG" in env:
            selector_debug_cfg["log_config"] = str_to_bool(env.get("SELECTOR_LOG_CONFIG"))
        if "SELECTOR_CAPTURE" in env:
            selector_debug_cfg["capture_on_fail"] = str_to_bool(env.get("SELECTOR_CAPTURE"))
        if "SELECTOR_CAPTURE_DIR" in env:
            selector_debug_cfg["capture_dir"] = env.get("SELECTOR_CAPTURE_DIR")

        if not selector_debug_cfg:
            selector_debug_cfg = None

        default_wait_cfg = (
            selector_payload.get("defaults", {}).get("wait")
            if isinstance(selector_payload.get("defaults"), dict)
            else None
        )
        raw_elements_cfg = selector_payload.get("elements", selector_payload)
    else:
        raw_elements_cfg = crawl_cfg.get("elements", [])

    elements_cfg = normalize_elements_config(raw_elements_cfg)

    locator_guard_cfg = None
    if selector_payload and isinstance(selector_payload.get("defaults"), dict):
        locator_guard_cfg = selector_payload["defaults"].get("locator_guard")

    locator_guard_mode = None
    if isinstance(locator_guard_cfg, dict):
        locator_guard_mode = (
            locator_guard_cfg.get("mode")
            or locator_guard_cfg.get("level")
            or locator_guard_cfg.get("severity")
        )
    elif isinstance(locator_guard_cfg, str):
        locator_guard_mode = locator_guard_cfg

    locator_guard_mode = env.get("LOCATOR_GUARD") or locator_guard_mode or "warn"
    guard_fragile_locators(elements_cfg, locator_guard_mode)

    if selector_payload and selector_debug_cfg:
        debug_enabled = selector_debug_cfg.get("enabled")
        if debug_enabled is None:
            debug_enabled = bool(
                selector_debug_cfg.get("log_config")
                or selector_debug_cfg.get("capture_on_fail")
            )
        if debug_enabled:
            version = selector_payload.get("version") or "unknown"
            site = selector_payload.get("site") or "unknown"
            module = selector_payload.get("module") or "unknown"
            page = selector_payload.get("page") or "unknown"
            env_name = selector_payload.get("environment") or "unknown"
            logger.info(
                "[selectors] Using selector config "
                "site=%s module=%s page=%s env=%s version=%s source=%s",
                site,
                module,
                page,
                env_name,
                version,
                selector_source,
            )
            if selector_debug_cfg.get("log_config", True):
                logger.info(
                    "[selectors] Config:\n%s",
                    json.dumps(selector_payload, ensure_ascii=False, indent=2),
                )

    if not elements_cfg:
        raise ValueError(
            "No elements configured. Please add elements under selectors.elements "
            "in configs/modules/*.json or under crawl.elements (legacy)."
        )

    return elements_cfg, default_wait_cfg, selector_debug_cfg

__all__ = [
    "build_selector_config",
    "collect_uids",
    "derive_step_status",
    "extract_account_cookie",
    "extract_account_uid",
    "extract_items",
    "fallback_selector_module",
    "infer_fb_type_from_url",
    "infer_module_for_item",
    "infer_module_from_crawl_types",
    "infer_selector_module",
    "load_account_cookies",
    "load_user_agents",
    "parse_dequeue_payload",
    "post_event",
    "precheck_facebook_uid",
]
