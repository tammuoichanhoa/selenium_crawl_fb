#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.crawler import crawl_pages_batch, _normalize_selector_modules
from scripts.dequeue_task import run_curl
from src.utils import (
    build_port_queue,
    DEFAULT_CONFIG_PATH,
    load_config,
    load_env_file,
    resolve_max_workers,
    resolve_profile_dirs,
    select_working_proxy,
    setup_logging,
    split_pages_for_workers,
    str_to_bool,
)
from src.utils.task_flow import (
    build_selector_config,
    collect_uids,
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
)


logger = logging.getLogger(__name__)
DEFAULT_EVENTS_URL = "https://gasoline-asn-protecting-pictures.trycloudflare.com/events"
# DEFAULT_ACCOUNT_COOKIES_FILE = "V1CM69c1f0b094cbc.txt"


def _crawl_from_uids(
    uids: List[str],
    *,
    config: Dict[str, Any],
    selector_module: str | None,
    max_workers_override: int | None,
    cookies_override: str | None = None,
    profile_backup_name: str | None = None,
) -> List[Dict[str, Any]]:
    crawl_cfg = config["crawl"]
    login_cfg = config["login"]

    env = load_env_file(".env")
    cookies_raw = cookies_override or env.get("COOKIES", "")
    user_agent = env.get("USER_AGENT", "")
    user_agents_file = env.get("USER_AGENTS_FILE", "user_agents.txt").strip() or "user_agents.txt"
    user_agents = load_user_agents(user_agents_file, user_agent)
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
                profile_backup_name=profile_backup_name if worker_id == 1 else None,
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

    payload = parse_dequeue_payload(result.stdout or "")
    items = extract_items(payload)
    env = load_env_file(".env")
    #@anhtb temp cookies for test
    account_cookies_file = env.get("ACCOUNT_COOKIES_FILE")
    account_cookies = load_account_cookies(account_cookies_file)
    precheck_enabled = str_to_bool(env.get("UID_PREFLIGHT_ENABLED", "1"))
    precheck_timeout = float(env.get("UID_PREFLIGHT_TIMEOUT", "6"))
    precheck_user_agent = env.get("USER_AGENT", "")

    config = load_config(DEFAULT_CONFIG_PATH)
    selector_root = None
    if isinstance(config.get("selectors"), dict):
        selector_root = config["selectors"]
    selector_modules = _normalize_selector_modules(selector_root)
    inferred_module = infer_selector_module(items, selector_modules, args.selector_module)

    response_items: List[Dict[str, Any]] = []
    grouped_items: Dict[str, Dict[str, Any]] = {}
    for index, item in enumerate(items):
        item["_index"] = index
        account_uid = extract_account_uid(item)
        account_cookie = extract_account_cookie(item, account_cookies)
        print("Account Info: ", account_uid, account_cookie)
        if account_uid and not account_cookie:
            logger.warning(
                "[account] No cookies found for account uid=%s; falling back to .env COOKIES.",
                account_uid,
            )
        group_key = account_uid or "__default__"
        group = grouped_items.setdefault(
            group_key,
            {"account_uid": account_uid, "cookies": account_cookie, "items": []},
        )
        if account_cookie and not group.get("cookies"):
            group["cookies"] = account_cookie
        elif (
            account_cookie
            and group.get("cookies")
            and account_cookie != group.get("cookies")
        ):
            logger.warning(
                "[account] Multiple cookies for account uid=%s; keeping the first one.",
                account_uid,
            )
        group["items"].append(item)

    indexed_results: Dict[int, Dict[str, Any]] = {}
    for group in grouped_items.values():
        group_items = group["items"]
        module_buckets: Dict[str | None, List[Dict[str, Any]]] = {}
        for item in group_items:
            inferred = infer_module_for_item(item, selector_modules, args.selector_module)
            item["selector_module"] = inferred
            module_buckets.setdefault(inferred, []).append(item)

        for module, module_items in module_buckets.items():
            valid_items: List[Dict[str, Any]] = []
            for item in module_items:
                uid = item.get("uid")
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
                        indexed_results[item["_index"]] = {
                            "task_id": item.get("task_id"),
                            "uid": uid,
                            "social_type": item.get("social_type"),
                            "crawl_types": item.get("crawl_types"),
                            "selector_module": item.get("selector_module"),
                            "result": {
                                "url": checked_url or uid,
                                "error": f"invalid_uid: {reason}",
                            },
                        }
                        continue
                valid_items.append(item)

            if not valid_items:
                continue

            uids = collect_uids(valid_items)
            results = _crawl_from_uids(
                uids,
                config=config,
                selector_module=module or inferred_module,
                max_workers_override=args.max_workers,
                cookies_override=group.get("cookies"),
                profile_backup_name=group.get("account_uid"),
            )
            for item, page_result in zip(valid_items, results):
                indexed_results[item["_index"]] = {
                    "task_id": item.get("task_id"),
                    "uid": item.get("uid"),
                    "social_type": item.get("social_type"),
                    "crawl_types": item.get("crawl_types"),
                    "selector_module": item.get("selector_module"),
                    "result": page_result,
                }

    for index in range(len(items)):
        result = indexed_results.get(index)
        if result:
            response_items.append(result)

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
            post_event(args.api_key, args.events_url, str(task_id), result_payload)
        else:
            logger.warning("[event] Skipped invalid event payload for item: %s", item)

    return 0


if __name__ == "__main__":
    sys.exit(main())
