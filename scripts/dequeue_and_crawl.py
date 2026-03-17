#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from crawler import crawl_pages_batch, _normalize_selector_modules
from scripts.dequeue_task import run_curl
from utils import (
    build_port_queue,
    guard_fragile_locators,
    load_config,
    load_env_file,
    normalize_elements_config,
    resolve_max_workers,
    resolve_profile_dirs,
    resolve_selector_payload,
    select_working_proxy,
    setup_logging,
    split_pages_for_workers,
    str_to_bool,
    validate_selector_payload,
)


DEFAULT_CONFIG_PATH = "config.json"
logger = logging.getLogger(__name__)
DEFAULT_EVENTS_URL = "https://gasoline-asn-protecting-pictures.trycloudflare.com/events"


def _parse_dequeue_payload(raw: str) -> Dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Dequeue response is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Dequeue response must be a JSON object.")
    return payload


def _post_event(api_key: str, event_url: str, task_id: str, result: Dict[str, Any]) -> None:
    payload = {
        "task_id": task_id,
        "event_type": "complete",
        "payload": {
            "steps": {
                "login": {"ok": True},
                "open_link": {"ok": True},
                "fetch_info": {"ok": True, "data": result},
            }
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


def _extract_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("Dequeue response has no items to crawl.")
    return [item for item in items if isinstance(item, dict)]


def _collect_uids(items: List[Dict[str, Any]]) -> List[str]:
    uids: List[str] = []
    for item in items:
        uid = item.get("uid")
        if isinstance(uid, str) and uid.strip():
            uids.append(uid.strip())
    if not uids:
        raise ValueError("No valid uid values found in dequeue items.")
    return uids


def _infer_selector_module(
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


def _load_user_agents(user_agents_file: str, fallback: str) -> List[str]:
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


def _build_selector_config(
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
            "in config.json or under crawl.elements (legacy)."
        )

    return elements_cfg, default_wait_cfg, selector_debug_cfg


def _crawl_from_uids(
    uids: List[str],
    *,
    config: Dict[str, Any],
    selector_module: str | None,
    max_workers_override: int | None,
) -> List[Dict[str, Any]]:
    crawl_cfg = config["crawl"]
    login_cfg = config["login"]

    env = load_env_file(".env")
    cookies_raw = env.get("COOKIES", "")
    user_agent = env.get("USER_AGENT", "")
    user_agents_file = env.get("USER_AGENTS_FILE", "user_agents.txt").strip() or "user_agents.txt"
    user_agents = _load_user_agents(user_agents_file, user_agent)
    chrome_binary = env.get("CHROME_BINARY", "").strip() or None
    chrome_binary_win_path = env.get("CHROME_BINARY_WIN_PATH", "").strip() or None
    chrome_binary_candidates_raw = env.get("CHROME_BINARY_CANDIDATES", "").strip()
    chrome_binary_candidates = (
        [item.strip() for item in chrome_binary_candidates_raw.split(",") if item.strip()]
        if chrome_binary_candidates_raw
        else None
    )
    fb_home_url = env.get("FB_HOME_URL", "").strip() or None
    fb_locale_url = env.get("FB_LOCALE_URL", "").strip() or None
    proxies_file = env.get("PROXIES_FILE", "proxies.txt").strip() or "proxies.txt"
    proxy = select_working_proxy(env.get("PROXY"), proxies_file)

    login_method = (
        env.get("LOGIN_METHOD")
        or login_cfg.get("method")
        or "cookies"
    ).strip().lower()
    profile_dirs = resolve_profile_dirs(env, crawl_cfg, login_cfg)
    for profile_dir in profile_dirs:
        os.makedirs(profile_dir, exist_ok=True)

    headless = str_to_bool(env.get("HEADLESS"), login_cfg.get("headless", False))
    wait_after_load = int(crawl_cfg.get("wait_after_load", 3))
    wait_between_pages = int(crawl_cfg.get("wait_between_pages", 0))
    element_timeout = int(crawl_cfg.get("element_timeout", 15))
    login_stagger_seconds = int(crawl_cfg.get("login_stagger_seconds", 2))

    elements_cfg, default_wait_cfg, selector_debug_cfg = _build_selector_config(
        config,
        crawl_cfg,
        env,
        selector_module,
    )

    configured_max_workers = (
        max_workers_override
        if max_workers_override is not None
        else env.get("MAX_WORKERS") or crawl_cfg.get("max_workers") or min(5, len(uids))
    )
    max_workers = resolve_max_workers(
        configured_max_workers,
        len(uids),
        login_method,
        len(profile_dirs),
    )
    page_batches = split_pages_for_workers(uids, max_workers)

    port_min = int(env.get("PORT_RANGE_MIN") or login_cfg.get("port_min") or 8000)
    port_max = int(env.get("PORT_RANGE_MAX") or login_cfg.get("port_max") or 9999)
    port_pool_size = int(
        env.get("PORT_POOL_SIZE")
        or login_cfg.get("port_pool_size")
        or max_workers
    )
    port_pool_size = max(port_pool_size, max_workers)
    port_queue = build_port_queue(port_min, port_max, port_pool_size)

    indexed_results: Dict[int, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                crawl_pages_batch,
                worker_id,
                batch,
                login_method=login_method,
                cookies_raw=cookies_raw,
                user_agents=user_agents,
                user_agent_fallback=user_agent,
                headless=headless,
                profile_dir=profile_dirs[(worker_id - 1) % len(profile_dirs)],
                proxy=proxy or None,
                chrome_binary=chrome_binary,
                chrome_binary_win_path=chrome_binary_win_path,
                chrome_binary_candidates=chrome_binary_candidates,
                fb_home_url=fb_home_url,
                fb_locale_url=fb_locale_url,
                port_queue=port_queue,
                elements_cfg=elements_cfg,
                wait_after_load=wait_after_load,
                wait_between_pages=wait_between_pages,
                element_timeout=element_timeout,
                login_stagger_seconds=login_stagger_seconds,
                default_wait_cfg=default_wait_cfg,
                selector_debug_cfg=selector_debug_cfg,
            )
            for worker_id, batch in enumerate(page_batches, start=1)
        ]

        for future in as_completed(futures):
            for index, page_data in future.result():
                indexed_results[index] = page_data

    return [indexed_results[index] for index in range(len(uids))]


def main() -> int:
    setup_logging()
    parser = argparse.ArgumentParser(
        description="Dequeue tasks and crawl using uid links from the response.",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("API_KEY"),
        help="API key for Authorization header (or set API_KEY env var).",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        dest="max_workers",
        help="Override max worker threads (takes precedence over .env/config).",
    )
    parser.add_argument(
        "--selector-module",
        dest="selector_module",
        help="Selector module to use (overrides inference).",
    )
    parser.add_argument(
        "--out",
        help="Optional output JSON file to write crawl results.",
    )
    parser.add_argument(
        "--events-url",
        default=DEFAULT_EVENTS_URL,
        help="Events endpoint URL to post completion payload.",
    )
    args = parser.parse_args()

    if not args.api_key:
        logger.error("Missing API key. Provide --api-key or set API_KEY env var.")
        return 2

    result = run_curl(args.api_key)
    if result.returncode != 0:
        logger.error("Dequeue request failed: %s", result.stderr.strip())
        return result.returncode

    payload = _parse_dequeue_payload(result.stdout or "")
    items = _extract_items(payload)
    uids = _collect_uids(items)
    print(">>>>>>>>>>>>>>", uids)

    config = load_config(DEFAULT_CONFIG_PATH)
    selector_root = None
    if isinstance(config.get("selectors"), dict):
        selector_root = config["selectors"]
    selector_modules = _normalize_selector_modules(selector_root)
    inferred_module = _infer_selector_module(items, selector_modules, args.selector_module)

    results = _crawl_from_uids(
        uids,
        config=config,
        selector_module=inferred_module,
        max_workers_override=args.max_workers,
    )

    response_items: List[Dict[str, Any]] = []
    for item, page_result in zip(items, results):
        response_items.append(
            {
                "task_id": item.get("task_id"),
                "uid": item.get("uid"),
                "social_type": item.get("social_type"),
                "crawl_types": item.get("crawl_types"),
                "result": page_result,
            }
        )

    output = {
        "count": len(response_items),
        "items": response_items,
    }

    output_json = json.dumps(output, ensure_ascii=False, indent=2)
    print(output_json)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as file:
            file.write(output_json)

    for item in response_items:
        task_id = item.get("task_id")
        result_payload = item.get("result")
        if task_id and isinstance(result_payload, dict):
            _post_event(args.api_key, args.events_url, str(task_id), result_payload)
        else:
            logger.warning("[event] Skipped invalid event payload for item: %s", item)

    return 0


if __name__ == "__main__":
    sys.exit(main())
