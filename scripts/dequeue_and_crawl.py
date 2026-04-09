#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import queue
import time
import datetime
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.crawler.engine import _normalize_selector_modules
from scripts.dequeue_task import run_curl
from src.utils.ports import build_port_queue
from src.core.selectors import guard_fragile_locators, normalize_elements_config, validate_selector_payload
from src.core.config_parser import load_config
from src.utils.env import load_env_file, str_to_bool
from src.utils.pages import resolve_max_workers, split_pages_for_workers
from src.utils.profiles import resolve_profile_dirs
from src.core.selector_remote import resolve_selector_payload
from src.utils.proxies import load_proxies
from src.utils.logging_setup import setup_logging
from src.core.driver_factory import create_logged_in_driver, terminate_chrome_process
from src.utils.proxies import load_proxies, get_working_proxy_from_list

from src.fbprofile.storage.paths import compute_paths
from src.fbprofile.browser.hooks import install_early_hook
from src.fbprofile.browser.get_profile_info import scrape_full_profile_info
from src.fbprofile.browser.navigation import go_to_date
from src.fbprofile.browser.scroll import crawl_scroll_loop
from src.fbprofile.storage.checkpoint import save_checkpoint

DEFAULT_CONFIG_PATH = "configs/config.json"
logger = logging.getLogger(__name__)
DEFAULT_EVENTS_URL = "https://latex-card-walk-donor.trycloudflare.com/events"


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
        logger.error("[event] Failed to post task_id=%s: %s", task_id, response.stderr.strip())
    elif response.stdout.strip():
        logger.info("[event] Response for task_id=%s: %s", task_id, response.stdout.strip())


def _extract_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return []
    return [item for item in items if isinstance(item, dict)]


def _collect_uids(items: List[Dict[str, Any]]) -> List[str]:
    uids: List[str] = []
    for item in items:
        uid = item.get("uid")
        if isinstance(uid, str) and uid.strip():
            uids.append(uid.strip())
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
            crawl_types.extend([str(value).lower() for value in types if value is not None])

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
    import random
    user_agents: List[str] = []
    if os.path.exists(user_agents_file):
        with open(user_agents_file, "r", encoding="utf-8") as file:
            user_agents = [
                line.strip()
                for line in file
                if line.strip() and not line.lstrip().startswith("#")
            ]
        if not user_agents:
            logger.warning("[user-agent] %s is empty; using USER_AGENT from .env", user_agents_file)
    else:
        logger.warning("[user-agent] %s not found; using USER_AGENT from .env", user_agents_file)
    if user_agents:
        return user_agents
    return [fallback] if fallback else []


def crawl_profiles_batch(
    worker_id: int,
    indexed_pages: List[Tuple[int, str]],
    login_method: str,
    cookies_raw: str,
    user_agents: List[str],
    user_agent_fallback: str,
    user_agent_rotation: bool,
    headless: bool,
    max_workers: int,
    profile_dir: str,
    proxy_candidates: List[str],
    proxy_rotation: bool,
    chrome_binary: str | None,
    chrome_binary_win_path: str | None,
    chrome_binary_candidates: List[str] | None,
    fb_home_url: str | None,
    fb_locale_url: str | None,
    port_queue: queue.Queue[int],
    data_root: str,
) -> List[Tuple[int, Dict[str, Any]]]:
    import random
    profile_label = profile_dir or "cookies-session"
    logger.info(
        f"[worker {worker_id}] Starting fbprofile interception for {len(indexed_pages)} task(s) "
        f"using {profile_label}"
    )

    debug_port = port_queue.get()
    
    if user_agent_rotation and user_agents:
        user_agent = random.choice(user_agents)
    elif user_agents: user_agent = user_agents[0]
    else: user_agent = user_agent_fallback
        
    proxy = get_working_proxy_from_list(proxy_candidates, rotate=proxy_rotation) if proxy_candidates else None
    
    window_size = None
    window_pos = None
    driver = None
    try:
        try:
            driver = create_logged_in_driver(
                login_method=login_method,
                cookies_raw=cookies_raw,
                user_agent=user_agent,
                headless=headless,
                profile_dir=profile_dir,
                proxy=proxy,
                chrome_binary=chrome_binary,
                debug_port=debug_port,
                home_url=fb_home_url or "https://www.facebook.com/",
                locale_url=fb_locale_url or "https://www.facebook.com/?locale=en_EN",
                chrome_binary_win_path=chrome_binary_win_path,
                chrome_binary_candidates=chrome_binary_candidates,
                window_size=window_size,
                window_position=window_pos,
            )
        except Exception as exc:
            logger.error("[worker %s] Login failed: %s", worker_id, exc)
            return [(index, {"uid": uid, "error": f"login_failed: {exc}"}) for index, uid in indexed_pages]

        results = []
        keep_last = 350
        MAX_STALL_RETRIES = 3

        for position, (index, uid_or_url) in enumerate(indexed_pages):
            group_url = uid_or_url
            if not group_url.startswith("http"):
                group_url = f"https://www.facebook.com/{uid_or_url}"

            try:
                page_name = str(uid_or_url).split('/')[-1].split('?')[0]
                if not page_name: page_name = str(uid_or_url)
                
                database_path, out_ndjson, raw_dumps_dir, checkpoint = compute_paths(
                    Path(data_root).resolve(), page_name, ""
                )
                profile_info_path = database_path / "profile_info.json"

                install_early_hook(driver, keep_last=keep_last)
                scrape_full_profile_info(driver, group_url, profile_info_path)

                driver.get(group_url)
                time.sleep(1.5)
                
                target_date = datetime.date.today()
                
                if "group" not in group_url:
                    try:
                        go_to_date(driver, target_date)
                    except Exception as e:
                        logger.warning("[worker %s] Không thể click 'Bộ lọc' ngày tháng (bỏ qua & tiếp tục scroll): %s", worker_id, str(e).split('\n')[0])

                seen_ids = set()
                stall_retry_count = 0
                current_target_date = target_date
                
                ts_state = {"latest": None, "earliest": None}

                while True:
                    stopped_due_to_stall = crawl_scroll_loop(
                        driver,
                        group_url=group_url,
                        out_path=out_ndjson,
                        seen_ids=seen_ids,
                        keep_last=keep_last,
                        max_scrolls=10000,
                        ts_state=ts_state,
                    )

                    if not stopped_due_to_stall:
                        break

                    stall_retry_count += 1
                    if stall_retry_count >= MAX_STALL_RETRIES:
                        break

                    if ts_state["earliest"] is None:
                        break

                    new_date = datetime.datetime.fromtimestamp(ts_state["earliest"]).date()
                    current_target_date = new_date

                if ts_state["latest"] is not None:
                    save_checkpoint(checkpoint, ts_state["latest"])

                profile_data = {}
                if profile_info_path.exists():
                    try:
                        with open(profile_info_path, "r", encoding="utf-8") as f:
                            profile_data = json.load(f)
                    except: pass
                
                page_data = {
                    "uid": uid_or_url,
                    "profile_info": profile_data,
                    "posts_collected": len(seen_ids),
                    "output_ndjson": str(out_ndjson)
                }

            except Exception as exc:
                logger.warning("[worker %s] Failed on %s: %s\n%s", worker_id, uid_or_url, exc, traceback.format_exc())
                page_data = {"uid": uid_or_url, "error": str(exc)}

            results.append((index, page_data))

        return results
    finally:
        if driver is not None:
            driver.quit()
            terminate_chrome_process(driver)
        port_queue.put(debug_port)
        logger.info("[worker %s] Finished (port %s)", worker_id, debug_port)


def _crawl_from_uids(
    uids: List[str],
    *,
    config: Dict[str, Any],
    selector_module: str | None,
    max_workers_override: int | None,
) -> List[Dict[str, Any]]:
    crawl_cfg = config.get("crawl", {})
    login_cfg = config.get("login", {})

    env = load_env_file(".env")
    cookies_raw = env.get("COOKIES", "")
    user_agent = env.get("USER_AGENT", "")
    user_agents_file = env.get("USER_AGENTS_FILE", "data/user_agents.txt").strip() or "data/user_agents.txt"
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
    proxies_file = env.get("PROXIES_FILE", "data/proxies.txt").strip() or "data/proxies.txt"
    proxy_candidates = load_proxies(env.get("PROXY"), proxies_file)
    proxy_rotation = str_to_bool(env.get("PROXY_ROTATION"), crawl_cfg.get("proxy_rotation", False))
    user_agent_rotation = str_to_bool(env.get("USER_AGENT_ROTATION"), crawl_cfg.get("user_agent_rotation", True))

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

    configured_max_workers = (
        max_workers_override
        if max_workers_override is not None
        else int(env.get("MAX_WORKERS") or crawl_cfg.get("max_workers") or min(5, len(uids)))
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
                crawl_profiles_batch,
                worker_id,
                batch,
                login_method=login_method,
                cookies_raw=cookies_raw,
                user_agents=user_agents,
                user_agent_fallback=user_agent,
                user_agent_rotation=user_agent_rotation,
                headless=headless,
                max_workers=max_workers,
                profile_dir=profile_dirs[(worker_id - 1) % len(profile_dirs)],
                proxy_candidates=proxy_candidates,
                proxy_rotation=proxy_rotation,
                chrome_binary=chrome_binary,
                chrome_binary_win_path=chrome_binary_win_path,
                chrome_binary_candidates=chrome_binary_candidates,
                fb_home_url=fb_home_url,
                fb_locale_url=fb_locale_url,
                port_queue=port_queue,
                data_root=str(Path(PROJECT_ROOT) / "database")
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
        description="Dequeue tasks and crawl using uid links from the response, using fbprofile mechanisms.",
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
        "--test-uid",
        dest="test_uid",
        help="Cung cấp UID để chạy chế độ test mà không cần gọi API xếp hàng đợi.",
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
    args.test_uid = "cambongda"
    if args.test_uid:
        logger.info("[TEST MODE] Bỏ qua gọi API Queue, dùng trực tiếp UID: %s", args.test_uid)
        items = [{"task_id": "test_id_999", "uid": args.test_uid, "social_type": "facebook", "crawl_types": ["profile"]}]
        uids = [args.test_uid]
        print(">>>>>>>>>>>>>> Processing UIDs (Test Mode):", uids)
    else:
        if not args.api_key:
            logger.error("Missing API key. Provide --api-key or set API_KEY env var.")
            return 2

        result = run_curl(args.api_key)
        if result.returncode != 0:
            logger.error("Dequeue request failed: %s", result.stderr.strip())
            return result.returncode

        payload = _parse_dequeue_payload(result.stdout or "")
        items = _extract_items(payload)
        if not items:
            logger.info("Hàng đợi (Queue) hiện đang trống. Không có tài khoản nào cần cào.")
            return 0

        uids = _collect_uids(items)
        if not uids:
            logger.info("Không tìm thấy thuộc tính 'uid' hợp lệ trong các item. Kết thúc an toàn.")
            return 0

        print(">>>>>>>>>>>>>> Processing UIDs:", uids)

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
