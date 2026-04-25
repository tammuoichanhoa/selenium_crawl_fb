#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List
from urllib.parse import parse_qs, urlparse
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from crawler import (
    crawl_urls_batch,
    extract_go_to_date_value,
    has_cursor_rid,
    parse_go_to_date,
    should_run_anonymous_first_for_target,
    _normalize_selector_modules,
)
from scripts.dequeue_task import run_request
from src.utils import (
    build_port_queue,
    build_service_url,
    guard_fragile_locators,
    DEFAULT_CONFIG_PATH,
    load_config,
    load_env_file,
    normalize_elements_config,
    parse_profile_dirs,
    resolve_max_workers,
    resolve_profile_dirs,
    resolve_selector_payload,
    select_working_proxy,
    setup_logging,
    split_urls_for_workers,
    str_to_bool,
    validate_selector_payload,
)
from src.utils.task_flow import (
    build_selector_config,
    # _build_selector_config,
    extract_account_cookie,
    extract_account_uid,
    extract_items,
    infer_module_for_item,
    infer_selector_module,
    load_account_cookies,
    load_user_agents,
    parse_dequeue_payload,
    post_event,
    precheck_facebook_uid,
    post_type_clone_event,
    infer_fb_type_from_url,
)


logger = logging.getLogger(__name__)
DEFAULT_EVENTS_URL = load_env_file(".env").get("EVENTS_URL")
# DEFAULT_ACCOUNT_COOKIES_FILE = "V1CM69c1f0b094cbc.txt"
def pick_value(cli_value: Any, env: Dict[str, Any], env_key: str, default: Any = None) -> Any:
    if cli_value is not None:
        return cli_value
    value = env.get(env_key)
    if value not in (None, ""):
        return value
    return default


def pick_int(
    cli_value: Any,
    env: Dict[str, Any],
    env_key: str,
    default: Any,
    fallback: int,
) -> int:
    value = pick_value(cli_value, env, env_key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning(
            "[config] Invalid integer for %s=%r; using fallback=%s",
            env_key,
            value,
            fallback,
        )
        return fallback


def pick_bool(
    cli_value: Any,
    env: Dict[str, Any],
    env_key: str,
    default: Any,
    fallback: bool,
) -> bool:
    return str_to_bool(
        pick_value(cli_value, env, env_key, default),
        str_to_bool(default, fallback),
    )


def _crawl_types_contains(item: Dict[str, Any], target_type: str) -> bool:
    crawl_types = item.get("crawl_types")
    if not isinstance(crawl_types, list):
        return False
    needle = target_type.lower()
    return any(needle in str(value).strip().lower() for value in crawl_types)


def _declared_target_type(
    item: Dict[str, Any],
    bucket_module: str | None,
) -> str | None:
    actual_type = infer_fb_type_from_url(item.get("uid"))
    if actual_type in {"profile", "page", "group"}:
        return actual_type

    for target_type in ("group", "page", "profile"):
        if _crawl_types_contains(item, target_type):
            return target_type

    module = item.get("selector_module") or bucket_module
    if isinstance(module, str) and module.strip().lower() in {"profile", "page", "group"}:
        return module.strip().lower()

    return None


def _response_item_from_result(
    item: Dict[str, Any],
    result: Dict[str, Any],
) -> Dict[str, Any]:
    response_item = {
        "task_id": item.get("task_id"),
        "uid": item.get("uid"),
        "social_type": item.get("social_type"),
        "crawl_types": item.get("crawl_types"),
        "cursor": item.get("cursor"),
        "selector_module": item.get("selector_module"),
        "result": result,
    }
    if item.get("needs_type_clone"):
        response_item["needs_type_clone"] = True
        response_item["detected_crawl_type"] = item.get(
            "detected_crawl_type",
            "profile",
        )
    return response_item


def _post_backend_event_for_item(
    *,
    api_key: str | None,
    events_url: str,
    node_id: str,
    item: Dict[str, Any],
) -> None:
    task_id = item.get("task_id")
    result_payload = item.get("result")
    try:
        with open("last_result_payload.json", "w", encoding="utf-8") as file:
            json.dump(result_payload, file, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning("[event] Failed writing last_result_payload.json: %s", exc)

    if not task_id:
        return

    if item.get("needs_type_clone"):
        logger.warning(
            "[event] needs_type_clone for task_id=%s: %s",
            task_id,
            result_payload,
        )
        post_type_clone_event(
            api_key,
            events_url,
            str(task_id),
            detected_crawl_type=item.get("detected_crawl_type", "profile"),
            result=result_payload,
            reason="Anonymous mode strictly restricted from crawling personal profiles",
            node_id=node_id,
        )
        return

    if isinstance(result_payload, dict) and (
        result_payload.get("needs_account_error")
        or "login_required" in str(result_payload.get("error", ""))
    ):
        logger.warning(
            "[event] needs_account_error for task_id=%s: %s",
            task_id,
            result_payload,
        )
        status_progress = (
            result_payload.get("status_progress")
            or result_payload.get("needs_account_reason")
        )
        if not status_progress:
            status_progress = (
                "login_required"
                if "login_required" in str(result_payload.get("error", ""))
                else "needs_account"
            )
        post_event(
            api_key,
            events_url,
            str(task_id),
            result_payload,
            event_type="report",
            needs_account=True,
            status_progress=status_progress,
        )
        return

    if isinstance(result_payload, dict):
        logger.warning("[event] Complete for task_id=%s: %s", task_id, result_payload)
        post_event(api_key, events_url, str(task_id), result_payload)
    else:
        logger.warning("[event] Skipped invalid event payload for item: %s", item)


def _crawl_from_uids(
    items: List[Dict[str, Any]],
    *,
    config: Dict[str, Any],
    selector_module: str | None,
    max_workers_override: int | None,
    cli_args: argparse.Namespace | None = None,
    cookies_override: str | None = None,
    profile_backup_name: str | None = None,
    login_method_override: str | None = None,
    go_to_date_override: str | None = None,
    result_callback: Callable[[int, Dict[str, Any]], None] | None = None,
) -> List[Dict[str, Any]]:
    crawl_cfg = config["crawl"]
    login_cfg = config["login"]

    env = load_env_file(".env")

    cookies_raw = (
        cookies_override
        or (getattr(cli_args, "cookies", None) if cli_args else None)
        or env.get("COOKIES", "")
    )

    user_agent = pick_value(
        getattr(cli_args, "user_agent", None) if cli_args else None,
        env,
        "USER_AGENT",
        "",
    )

    user_agents_file = pick_value(
        getattr(cli_args, "user_agents_file", None) if cli_args else None,
        env,
        "USER_AGENTS_FILE",
        "user_agents.txt",
    ).strip() or "user_agents.txt"

    user_agents = load_user_agents(user_agents_file, user_agent)

    chrome_binary = pick_value(
        getattr(cli_args, "chrome_binary", None) if cli_args else None,
        env,
        "CHROME_BINARY",
        "",
    ).strip() or None

    chrome_binary_win_path = pick_value(
        getattr(cli_args, "chrome_binary_win_path", None) if cli_args else None,
        env,
        "CHROME_BINARY_WIN_PATH",
        "",
    ).strip() or None

    chrome_binary_candidates_raw = pick_value(
        getattr(cli_args, "chrome_binary_candidates", None) if cli_args else None,
        env,
        "CHROME_BINARY_CANDIDATES",
        "",
    ).strip()

    chrome_binary_candidates = (
        [item.strip() for item in chrome_binary_candidates_raw.split(",") if item.strip()]
        if chrome_binary_candidates_raw
        else None
    )

    fb_home_url = pick_value(
        getattr(cli_args, "fb_home_url", None) if cli_args else None,
        env,
        "FB_HOME_URL",
        "",
    ).strip() or None

    fb_locale_url = pick_value(
        getattr(cli_args, "fb_locale_url", None) if cli_args else None,
        env,
        "FB_LOCALE_URL",
        "",
    ).strip() or None

    proxies_file = pick_value(
        getattr(cli_args, "proxies_file", None) if cli_args else None,
        env,
        "PROXIES_FILE",
        "proxies.txt",
    ).strip() or "proxies.txt"

    proxy_override = getattr(cli_args, "proxy", None) if cli_args else None
    proxy = select_working_proxy(proxy_override or env.get("PROXY"), proxies_file)

    login_method = (
        login_method_override
        or (getattr(cli_args, "login_method", None) if cli_args else None)
        or env.get("LOGIN_METHOD")
        or login_cfg.get("method")
        or "cookies"
    ).strip().lower()

    headless = pick_bool(
        getattr(cli_args, "headless", None) if cli_args else None,
        env,
        "HEADLESS",
        login_cfg.get("headless", False),
        False,
    )

    wait_after_load = pick_int(
        getattr(cli_args, "wait_after_load", None) if cli_args else None,
        env,
        "WAIT_AFTER_LOAD",
        crawl_cfg.get("wait_after_load", 3),
        3,
    )

    wait_between_pages = pick_int(
        getattr(cli_args, "wait_between_pages", None) if cli_args else None,
        env,
        "WAIT_BETWEEN_PAGES",
        crawl_cfg.get("wait_between_pages", 0),
        0,
    )

    element_timeout = pick_int(
        getattr(cli_args, "element_timeout", None) if cli_args else None,
        env,
        "ELEMENT_TIMEOUT",
        crawl_cfg.get("element_timeout", 15),
        15,
    )

    login_stagger_seconds = pick_int(
        getattr(cli_args, "login_stagger_seconds", None) if cli_args else None,
        env,
        "LOGIN_STAGGER_SECONDS",
        crawl_cfg.get("login_stagger_seconds", 2),
        2,
    )
    run_generic_selector_after_info = pick_bool(
        getattr(cli_args, "run_generic_selector_after_info", None) if cli_args else None,
        env,
        "RUN_GENERIC_SELECTOR_AFTER_INFO",
        crawl_cfg.get("run_generic_selector_after_info", False),
        False,
    )
    raw_profile_dirs_override: List[str] = []
    if cli_args:
        if getattr(cli_args, "test_uid", None):
            test_profile_dirs_csv = getattr(cli_args, "test_profile_dirs_csv", None)
            if test_profile_dirs_csv:
                raw_profile_dirs_override.append(str(test_profile_dirs_csv))
            test_profile_dir_values = getattr(cli_args, "test_profile_dirs", None)
            if test_profile_dir_values:
                raw_profile_dirs_override.extend(
                    str(value) for value in test_profile_dir_values
                )
        if not raw_profile_dirs_override:
            profile_dirs_csv = getattr(cli_args, "profile_dirs_csv", None)
            if profile_dirs_csv:
                raw_profile_dirs_override.append(str(profile_dirs_csv))
            profile_dir_values = getattr(cli_args, "profile_dirs", None)
            if profile_dir_values:
                raw_profile_dirs_override.extend(str(value) for value in profile_dir_values)
        if not raw_profile_dirs_override and getattr(cli_args, "test_uid", None):
            test_profile_dirs = (
                env.get("TEST_UID_PROFILE_DIRS")
                or env.get("TEST_PROFILE_DIRS")
                or ""
            ).strip()
            test_profile_dir = (
                env.get("TEST_UID_PROFILE_DIR")
                or env.get("TEST_PROFILE_DIR")
                or ""
            ).strip()
            if test_profile_dirs:
                raw_profile_dirs_override.append(test_profile_dirs)
            elif test_profile_dir:
                raw_profile_dirs_override.append(test_profile_dir)
    profile_dirs = (
        parse_profile_dirs(",".join(raw_profile_dirs_override))
        if raw_profile_dirs_override
        else resolve_profile_dirs(env, crawl_cfg, login_cfg)
    )
    for profile_dir in profile_dirs:
        os.makedirs(profile_dir, exist_ok=True)
    if cli_args and getattr(cli_args, "test_uid", None):
        logger.info(
            "[TEST MODE] effective_login_method=%s profile_dirs=%s",
            login_method,
            profile_dirs,
        )

    scroll_until_stable_cfg = (
        crawl_cfg.get("scroll_until_stable")
        if isinstance(crawl_cfg.get("scroll_until_stable"), dict)
        else None
    )

    override_date = parse_go_to_date(go_to_date_override)
    crawl_targets: List[Dict[str, Any]] = []
    for item in items:
        uid = item.get("uid")
        if not isinstance(uid, str) or not uid.strip():
            raise ValueError("No valid uid values found in dequeue items.")
        target_date = override_date or parse_go_to_date(extract_go_to_date_value(item))
        crawl_targets.append(
            {
                "uid": uid.strip(),
                "selector_module": item.get("selector_module"),
                "date": target_date.isoformat() if target_date else None,
                "login_method": item.get("_login_method_override"),
                "cookies": item.get("_cookies_override"),
                "profile_backup_name": item.get("_profile_backup_name"),
                "auto_anonymous_first": bool(item.get("_auto_anonymous_first")),
            }
        )

    elements_cfg, default_wait_cfg, selector_debug_cfg = build_selector_config(
        config,
        crawl_cfg,
        env,
        selector_module,
    )
    selector_root = None
    if isinstance(config.get("selectors"), dict):
        selector_root = config["selectors"]
    elif isinstance(crawl_cfg.get("selectors"), dict):
        selector_root = crawl_cfg["selectors"]
    selector_modules = _normalize_selector_modules(selector_root)
    elements_cfg_profile: List[Dict[str, Any]] | None = None
    default_wait_cfg_profile: Dict[str, Any] | None = None
    selector_debug_cfg_profile: Dict[str, Any] | None = None
    elements_cfg_page: List[Dict[str, Any]] | None = None
    default_wait_cfg_page: Dict[str, Any] | None = None
    selector_debug_cfg_page: Dict[str, Any] | None = None
    if selector_module is None and "profile" in selector_modules and "page" in selector_modules:
        elements_cfg_profile, default_wait_cfg_profile, selector_debug_cfg_profile = build_selector_config(
            config,
            crawl_cfg,
            env,
            "profile",
        )
        elements_cfg_page, default_wait_cfg_page, selector_debug_cfg_page = build_selector_config(
            config,
            crawl_cfg,
            env,
            "page",
        )

    configured_max_workers = (
        max_workers_override
        if max_workers_override is not None
        else env.get("MAX_WORKERS") or crawl_cfg.get("max_workers") or min(5, len(crawl_targets))
    )
    target_login_methods = {
        (str(target.get("login_method") or login_method).strip().lower() or login_method)
        for target in crawl_targets
    }
    if "profile" in target_login_methods:
        worker_login_method = "profile"
    elif len(target_login_methods) > 1:
        worker_login_method = "anonymous"
    else:
        worker_login_method = next(iter(target_login_methods), login_method)

    max_workers = resolve_max_workers(
        configured_max_workers,
        len(crawl_targets),
        worker_login_method,
        len(profile_dirs),
    )
    logger.info(
        "[crawl] Scheduling %s mixed task(s) across %s worker(s); login_methods=%s",
        len(crawl_targets),
        max_workers,
        ",".join(sorted(target_login_methods)),
    )
    batches = split_urls_for_workers(crawl_targets, max_workers)

    port_min = (
        getattr(cli_args, "port_range_min", None)
        if cli_args and getattr(cli_args, "port_range_min", None) is not None
        else int(env.get("PORT_RANGE_MIN") or login_cfg.get("port_min") or 8000)
    )

    port_max = (
        getattr(cli_args, "port_range_max", None)
        if cli_args and getattr(cli_args, "port_range_max", None) is not None
        else int(env.get("PORT_RANGE_MAX") or login_cfg.get("port_max") or 9999)
    )

    port_pool_size = (
        getattr(cli_args, "port_pool_size", None)
        if cli_args and getattr(cli_args, "port_pool_size", None) is not None
        else int(env.get("PORT_POOL_SIZE") or login_cfg.get("port_pool_size") or max_workers)
    )
    port_pool_size = max(port_pool_size, max_workers)
    port_queue = build_port_queue(port_min, port_max, port_pool_size)

    indexed_results: Dict[int, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                crawl_urls_batch,
                worker_id,
                batch,
                selector_module=selector_module,
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
                elements_cfg_profile=elements_cfg_profile,
                elements_cfg_page=elements_cfg_page,
                wait_after_load=wait_after_load,
                wait_between_pages=wait_between_pages,
                element_timeout=element_timeout,
                login_stagger_seconds=login_stagger_seconds,
                default_wait_cfg=default_wait_cfg,
                default_wait_cfg_profile=default_wait_cfg_profile,
                default_wait_cfg_page=default_wait_cfg_page,
                selector_debug_cfg=selector_debug_cfg,
                selector_debug_cfg_profile=selector_debug_cfg_profile,
                selector_debug_cfg_page=selector_debug_cfg_page,
                scroll_until_stable_cfg=scroll_until_stable_cfg,
                run_generic_selector_after_info=run_generic_selector_after_info,
                profile_backup_name=profile_backup_name,
                allow_auto_anonymous_first=True,
                result_callback=result_callback,
            )
            for worker_id, batch in enumerate(batches, start=1)
        ]

        for future in as_completed(futures):
            for index, page_data in future.result():
                indexed_results[index] = page_data

    return [indexed_results[index] for index in range(len(crawl_targets))]


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
        "--out",
        help="Optional output JSON file to write crawl results.",
    )
    parser.add_argument(
        "--test-uid",
        dest="test_uid",
        help="Provide a static UID to crawl directly without waiting for dequeue API.",
    )
    parser.add_argument(
        "--test-login-method",
        dest="test_login_method",
        help="Login method used only with --test-uid; overrides TEST_UID_LOGIN_METHOD.",
    )
    parser.add_argument(
        "--test-profile-dir",
        dest="test_profile_dirs",
        action="append",
        help=(
            "Chrome profile directory used only with --test-uid. "
            "Can be passed multiple times."
        ),
    )
    parser.add_argument(
        "--test-profile-dirs",
        dest="test_profile_dirs_csv",
        help="Comma-separated Chrome profile directories used only with --test-uid.",
    )
    parser.add_argument(
        "--anonymous",
        action="store_true",
        help="Run crawler in anonymous (incognito) mode.",
    )
    parser.add_argument(
        "--date",
        dest="go_to_date",
        help=(
            "Optional timeline end date for go_to_date, formatted YYYY-MM-DD. "
            "Overrides date fields from dequeue items."
        ),
    )
    parser.add_argument("--cookies", help="Override COOKIES from .env")
    parser.add_argument("--user-agent", dest="user_agent", help="Override USER_AGENT from .env")
    parser.add_argument("--user-agents-file", dest="user_agents_file", help="Override USER_AGENTS_FILE from .env")

    parser.add_argument("--chrome-binary", dest="chrome_binary", help="Override CHROME_BINARY from .env")
    parser.add_argument("--chrome-binary-win-path", dest="chrome_binary_win_path", help="Override CHROME_BINARY_WIN_PATH from .env")
    parser.add_argument(
        "--chrome-binary-candidates",
        dest="chrome_binary_candidates",
        help="Comma-separated list to override CHROME_BINARY_CANDIDATES from .env",
    )

    parser.add_argument("--fb-home-url", dest="fb_home_url", help="Override FB_HOME_URL from .env")
    parser.add_argument("--fb-locale-url", dest="fb_locale_url", help="Override FB_LOCALE_URL from .env")

    parser.add_argument("--proxy", help="Override PROXY from .env")
    parser.add_argument("--proxies-file", dest="proxies_file", help="Override PROXIES_FILE from .env")

    parser.add_argument("--login-method", dest="login_method", help="Override LOGIN_METHOD from .env/config")
    parser.add_argument(
        "--profile-dir",
        dest="profile_dirs",
        action="append",
        help=(
            "Use a specific Chrome profile directory. Can be passed multiple times. "
            "Useful with --test-uid."
        ),
    )
    parser.add_argument(
        "--profile-dirs",
        dest="profile_dirs_csv",
        help="Comma-separated Chrome profile directories, overriding PROFILE_DIRS.",
    )
    parser.add_argument("--headless", dest="headless", help="Override HEADLESS from .env (true/false)")

    parser.add_argument("--wait-after-load", dest="wait_after_load", type=int, help="Override crawl.wait_after_load")
    parser.add_argument("--wait-between-pages", dest="wait_between_pages", type=int, help="Override crawl.wait_between_pages")
    parser.add_argument("--element-timeout", dest="element_timeout", type=int, help="Override crawl.element_timeout")
    parser.add_argument("--login-stagger-seconds", dest="login_stagger_seconds", type=int, help="Override crawl.login_stagger_seconds")
    parser.add_argument(
        "--run-generic-selector-after-info",
        dest="run_generic_selector_after_info",
        help="Override RUN_GENERIC_SELECTOR_AFTER_INFO/crawl.run_generic_selector_after_info (true/false).",
    )

    parser.add_argument("--port-range-min", dest="port_range_min", type=int, help="Override PORT_RANGE_MIN")
    parser.add_argument("--port-range-max", dest="port_range_max", type=int, help="Override PORT_RANGE_MAX")
    parser.add_argument("--port-pool-size", dest="port_pool_size", type=int, help="Override PORT_POOL_SIZE")

    parser.add_argument("--account-cookies-file", dest="account_cookies_file", help="Override ACCOUNT_COOKIES_FILE")
    parser.add_argument("--uid-preflight-enabled", dest="uid_preflight_enabled", help="Override UID_PREFLIGHT_ENABLED")
    parser.add_argument("--uid-preflight-timeout", dest="uid_preflight_timeout", type=float, help="Override UID_PREFLIGHT_TIMEOUT")
    args = parser.parse_args()

    if args.go_to_date:
        try:
            parsed_go_to_date = parse_go_to_date(args.go_to_date)
        except ValueError as exc:
            logger.error(str(exc))
            return 2
        args.go_to_date = parsed_go_to_date.isoformat() if parsed_go_to_date else None

    env = load_env_file(".env")
    if not args.api_key:
        args.api_key = env.get("API_KEY")
    events_url = build_service_url(
        env,
        path="/events",
        explicit_key="EVENTS_URL",
        fallback=DEFAULT_EVENTS_URL,
    )

    if args.test_uid:
        logger.info("[TEST MODE] Skipping API queue, using static test UID: %s", args.test_uid)
        items = [{
            "task_id": "test_id_999",
            "uid": args.test_uid,
            "social_type": "facebook",
            "crawl_types": ["page"],
            "date": args.go_to_date,
        }]
    else:
        if not args.api_key:
            logger.error("Missing API key. Provide --api-key or set API_KEY env var.")
            return 2

        try:
            result = run_request(args.api_key)
        except ValueError as exc:
            logger.error("Dequeue request is not configured: %s", exc)
            return 2

        if not result.ok:
            logger.error(
                "Dequeue request failed: status=%s error=%s",
                result.status_code,
                result.error or result.text,
            )
            return 1

        payload = parse_dequeue_payload(result.json())
        # print("payload>>>>>>>>>", payload)
        items = extract_items(payload)
        # print("Items from payload: ", items)

    if not items:
        logger.info("Queue is empty or contains no valid tasks. Exiting safely.")
        return 0

    account_cookies_file = args.account_cookies_file or env.get("ACCOUNT_COOKIES_FILE")
    account_cookies = load_account_cookies(account_cookies_file)

    precheck_enabled = str_to_bool(
        args.uid_preflight_enabled,
        str_to_bool(env.get("UID_PREFLIGHT_ENABLED", "1")),
    )

    precheck_timeout = (
        args.uid_preflight_timeout
        if args.uid_preflight_timeout is not None
        else float(env.get("UID_PREFLIGHT_TIMEOUT", "6"))
    )

    precheck_user_agent = args.user_agent or env.get("USER_AGENT", "")

    config = load_config(DEFAULT_CONFIG_PATH)
    selector_root = None
    if isinstance(config.get("selectors"), dict):
        selector_root = config["selectors"]
    selector_modules = _normalize_selector_modules(selector_root)
    inferred_module = infer_selector_module(items, selector_modules, None)
    node_id = env.get("NODE_ID", "default-node")

    response_items: List[Dict[str, Any]] = []
    indexed_results: Dict[int, Dict[str, Any]] = {}
    valid_items: List[Dict[str, Any]] = []
    posted_indices: set[int] = set()
    result_event_lock = threading.Lock()
    account_first_cookies: Dict[str, str] = {}
    login_cfg = config.get("login", {})
    global_login_method = (
        env.get("LOGIN_METHOD")
        or login_cfg.get("method")
        or "cookies"
    ).strip().lower()
    test_uid_login_method = (
        (
            env.get("TEST_UID_LOGIN_METHOD")
            or env.get("TEST_LOGIN_METHOD")
            or ""
        ).strip().lower()
        if args.test_uid
        else None
    )
    cli_test_login_method = (
        args.test_login_method.strip().lower()
        if args.test_uid and args.test_login_method
        else None
    )
    explicit_login_method = (
        args.login_method.strip().lower()
        if args.login_method
        else (
            "anonymous"
            if args.anonymous
            else cli_test_login_method or test_uid_login_method or None
        )
    )

    for index, item in enumerate(items):
        item["_index"] = index
        account_uid = extract_account_uid(item)
        account_cookie = extract_account_cookie(item, account_cookies)
        force_profile_login = bool(account_uid and not account_cookie)
        if force_profile_login:
            logger.warning(
                "[account] No cookies found for account uid=%s; forcing LOGIN_METHOD=profile.",
                account_uid,
            )
        if account_uid and account_cookie:
            first_cookie = account_first_cookies.setdefault(account_uid, account_cookie)
            if first_cookie != account_cookie:
                logger.warning(
                    "[account] Multiple cookies for account uid=%s; using the task-specific cookie.",
                    account_uid,
                )

        inferred = infer_module_for_item(item, selector_modules, None) or inferred_module
        item["selector_module"] = inferred
        declared_type = _declared_target_type(item, inferred)
        auto_anonymous_first = (
            declared_type == "page"
            and not has_cursor_rid(item)
            and explicit_login_method is None
            and should_run_anonymous_first_for_target(item, inferred)
        )
        login_method_for_item = (
            "profile"
            if has_cursor_rid(item)
            else "anonymous"
            if auto_anonymous_first
            else "profile"
            if force_profile_login
            else explicit_login_method
        )
        if auto_anonymous_first:
            logger.info(
                "[auto-anonymous] task_id=%s fanpage has no previous crawl; using anonymous first for %s",
                item.get("task_id"),
                item.get("uid"),
            )
        if login_method_for_item == "profile":
            cursor = item.get("cursor") if isinstance(item.get("cursor"), dict) else {}
            if has_cursor_rid(item):
                logger.info(
                    "[cursor] task_id=%s has cursor.rid=%s created_time=%s; forcing LOGIN_METHOD=profile",
                    item.get("task_id"),
                    cursor.get("rid"),
                    cursor.get("created_time"),
                )
            elif force_profile_login:
                logger.info(
                    "[account] task_id=%s account uid=%s has no cookie; forcing LOGIN_METHOD=profile",
                    item.get("task_id"),
                    account_uid,
                )

        item["_login_method_override"] = login_method_for_item
        item["_cookies_override"] = account_cookie or None
        item["_profile_backup_name"] = account_uid or None
        item["_auto_anonymous_first"] = auto_anonymous_first

        uid = item.get("uid")
        effective_login_method = login_method_for_item or global_login_method
        is_anon = effective_login_method == "anonymous"
        if is_anon and declared_type == "profile":
            logger.warning(
                "[type_clone] Anonymous mode cannot crawl profile. Recalling task: %s",
                uid,
            )
            item["needs_type_clone"] = True
            item["detected_crawl_type"] = "profile"
            response_item = _response_item_from_result(
                item,
                {"error": "skipped_type_mismatch_profile"},
            )
            indexed_results[item["_index"]] = response_item
            _post_backend_event_for_item(
                api_key=args.api_key,
                events_url=events_url,
                node_id=node_id,
                item=response_item,
            )
            posted_indices.add(item["_index"])
            continue

        if precheck_enabled and isinstance(uid, str):
            status, reason, checked_url = precheck_facebook_uid(
                uid,
                timeout=precheck_timeout,
                user_agent=precheck_user_agent,
            )
            if status == "invalid":
                logger.warning(
                    "[precheck] uid invalid: %s (%s)",
                    uid,
                    reason,
                )
                response_item = _response_item_from_result(
                    item,
                    {
                        "url": checked_url or uid,
                        "error": f"invalid_uid: {reason}",
                    },
                )
                indexed_results[item["_index"]] = response_item
                _post_backend_event_for_item(
                    api_key=args.api_key,
                    events_url=events_url,
                    node_id=node_id,
                    item=response_item,
                )
                posted_indices.add(item["_index"])
                continue

        valid_items.append(item)

    if valid_items:
        def publish_crawl_result(local_index: int, result: Dict[str, Any]) -> None:
            item = valid_items[local_index]
            original_index = item["_index"]
            response_item = _response_item_from_result(item, result)
            with result_event_lock:
                indexed_results[original_index] = response_item
                try:
                    _post_backend_event_for_item(
                        api_key=args.api_key,
                        events_url=events_url,
                        node_id=node_id,
                        item=response_item,
                    )
                except Exception as exc:
                    logger.error(
                        "[event] Failed to post early result for task_id=%s: %s",
                        response_item.get("task_id"),
                        exc,
                    )
                    return
                posted_indices.add(original_index)
            logger.info(
                "[event] Posted early result for task_id=%s index=%s",
                response_item.get("task_id"),
                original_index,
            )

        results = _crawl_from_uids(
            valid_items,
            config=config,
            selector_module=None,
            max_workers_override=args.max_workers,
            cli_args=args,
            cookies_override=None,
            profile_backup_name=None,
            login_method_override=None,
            go_to_date_override=args.go_to_date,
            result_callback=publish_crawl_result,
        )
        for item, result in zip(valid_items, results):
            indexed_results[item["_index"]] = _response_item_from_result(item, result)

    for index in range(len(items)):
        result = indexed_results.get(index)
        if result:
            response_items.append(result)

    output = {
        "count": len(response_items),
        "items": response_items,
    }

    output_json = json.dumps(output, ensure_ascii=False, indent=2)
    # print(output_json)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as file:
            file.write(output_json)

    for index in range(len(items)):
        if index in posted_indices:
            continue
        item = indexed_results.get(index)
        if not item:
            continue
        _post_backend_event_for_item(
            api_key=args.api_key,
            events_url=events_url,
            node_id=node_id,
            item=item,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
