from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple
import datetime
from pathlib import Path

from src.fbprofile.storage.paths import compute_paths
from src.fbprofile.browser.hooks import install_early_hook
from src.fbprofile.browser.get_profile_info import scrape_full_profile_info
from src.fbprofile.browser.get_page_info import scrape_full_page_info
from src.fbprofile.browser.navigation import go_to_date
from src.fbprofile.browser.scroll import crawl_scroll_loop
from src.fbprofile.storage.checkpoint import save_checkpoint

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from src.utils import (
    build_port_queue,
    create_logged_in_driver,
    extract_element,
    guard_fragile_locators,
    load_config,
    load_env_file,
    normalize_elements_config,
    read_pages,
    resolve_max_workers,
    resolve_profile_dirs,
    resolve_selector_payload,
    select_working_proxy,
    setup_logging,
    split_pages_for_workers,
    str_to_bool,
    terminate_chrome_process,
    validate_selector_payload,
    wait_for_page_ready,
    wait_for_seconds,
)


DEFAULT_CONFIG_PATH = os.path.join("configs", "base.json")
DEFAULT_PAGES_FILE = "pages.txt"
logger = logging.getLogger(__name__)

'''
    Nhiệm vụ: vào 1 URL và trích dữ liệu.

    Luồng đơn giản:

    driver.get(url) mở trang.
    wait_for_page_ready và wait_for_seconds để trang ổn định.
    Duyệt từng cấu hình selector trong elements_cfg.
    Gọi extract_element(...) để lấy dữ liệu.
    Lỗi thì ghi None và log lỗi.
    Trả về dict dữ liệu, có key "url".
    '''
def crawl_page(
    driver,
    url: str,
    elements_cfg: List[Dict[str, Any]],
    wait_after_load: int,
    element_timeout: int,
    default_wait_cfg: Dict[str, Any] | None,
    selector_debug_cfg: Dict[str, Any] | None,
) -> Dict[str, Any]:
    logger.info("[crawl] Visiting %s", url)
    driver.get(url)
    wait_for_page_ready(driver, 20)
    wait_for_seconds(driver, wait_after_load)

    
    data: Dict[str, Any] = {"url": url}
    for element_cfg in elements_cfg:
        name = element_cfg.get("name") or element_cfg.get("selector")
        try:
            value = extract_element(
                driver,
                element_cfg,
                element_timeout,
                default_wait_cfg,
                selector_debug_cfg,
            )
        except Exception as exc:
            logger.warning(
                "[crawl] Failed to capture '%s' on %s: %s",
                name,
                url,
                exc,
            )
            value = None
        data[name] = value
    return data


def _normalize_selector_modules(root: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(root, dict):
        return {}

    if isinstance(root.get("modules"), dict):
        modules = root["modules"]
        return {
            str(name): validate_selector_payload(payload)
            for name, payload in modules.items()
            if isinstance(payload, dict)
        }

    if "elements" in root:
        return {"default": validate_selector_payload(root)}

    if root and all(isinstance(value, dict) for value in root.values()):
        return {
            str(name): validate_selector_payload(payload)
            for name, payload in root.items()
        }

    return {}

'''
Nhiệm vụ: một worker xử lý một nhóm URL.

Luồng dễ hiểu:

In log worker, nếu có “giãn đăng nhập” (login_stagger_seconds) thì đợi.
Lấy debug_port từ port_queue.
Tạo trình duyệt + đăng nhập bằng create_logged_in_driver(...).
Duyệt từng URL trong batch:
Gọi crawl_page(...) để lấy dữ liệu.
Nếu lỗi thì ghi error.
Giữa các trang, đợi wait_between_pages.
Đóng driver, trả lại port về queue.
'''
def crawl_pages_batch(
    worker_id: int,
    indexed_pages: List[Tuple[int, str]],
    *,
    login_method: str,
    cookies_raw: str,
    user_agents: List[str],
    user_agent_fallback: str,
    headless: bool,
    profile_dir: str,
    proxy: str | None,
    chrome_binary: str | None,
    chrome_binary_win_path: str | None,
    chrome_binary_candidates: List[str] | None,
    fb_home_url: str | None,
    fb_locale_url: str | None,
    port_queue: queue.Queue[int],
    elements_cfg: List[Dict[str, Any]],
    wait_after_load: int,
    wait_between_pages: int,
    element_timeout: int,
    login_stagger_seconds: int,
    default_wait_cfg: Dict[str, Any] | None,
    selector_debug_cfg: Dict[str, Any] | None,
    profile_backup_name: str | None = None,
    selector_module: str | None = None,
) -> List[Tuple[int, Dict[str, Any]]]:
    profile_label = profile_dir or "cookies-session"
    logger.info(
        f"[worker {worker_id}] Starting with {len(indexed_pages)} page(s) "
        f"using {profile_label}"
    )
    if login_stagger_seconds > 0 and worker_id > 1:
        delay = login_stagger_seconds * (worker_id - 1)
        logger.info("[worker %s] Waiting %ss before login", worker_id, delay)
        time.sleep(delay)

    debug_port = port_queue.get()
    user_agent = (
        random.choice(user_agents)
        if user_agents
        else user_agent_fallback
    )
    if user_agent:
        logger.info(
            "[worker %s] Using user-agent (port %s): %s",
            worker_id,
            debug_port,
            user_agent,
        )
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
                locale_url=fb_locale_url or "https://www.facebook.com/?locale=vi_VN",
                chrome_binary_win_path=chrome_binary_win_path,
                chrome_binary_candidates=chrome_binary_candidates,
                profile_backup_name=profile_backup_name,
            )
        except Exception as exc:
            logger.error("[worker %s] Login failed: %s", worker_id, exc)
            return [
                (index, {"url": url, "error": f"login_failed: {exc}"})
                for index, url in indexed_pages
            ]

        results: List[Tuple[int, Dict[str, Any]]] = []
        for position, (index, uid_or_url) in enumerate(indexed_pages):
            url = uid_or_url
            if not str(url).startswith("http"):
                url = f"https://www.facebook.com/{url}"

            try:
                page_name = str(url).split('/')[-1].split('?')[0]
                if not page_name: page_name = str(url)
                
                data_root = str(Path(PROJECT_ROOT) / "database")
                database_path, out_ndjson, raw_dumps_dir, checkpoint = compute_paths(
                    Path(data_root).resolve(), page_name, ""
                )
                profile_info_path = database_path / "profile_info.json"

                install_early_hook(driver, keep_last=350)
                if selector_module == "profile":
                    scrape_full_profile_info(driver, url, profile_info_path)
                else:
                    scrape_full_page_info(driver, url, profile_info_path)

                page_data = crawl_page(
                    driver,
                    url,
                    elements_cfg,
                    wait_after_load,
                    element_timeout,
                    default_wait_cfg,
                    selector_debug_cfg,
                )

                target_date = datetime.date.today()
                if "group" not in url:
                    try:
                        go_to_date(driver, target_date)
                    except Exception as e:
                        logger.warning("[worker %s] Lỗi go_to_date: %s", worker_id, e)

                seen_ids = set()
                ts_state = {"latest": None, "earliest": None}

                crawl_scroll_loop(
                    driver,
                    group_url=url,
                    out_path=out_ndjson,
                    seen_ids=seen_ids,
                    keep_last=350,
                    max_scrolls=10000,
                    ts_state=ts_state,
                )

                if ts_state["latest"] is not None:
                    save_checkpoint(checkpoint, ts_state["latest"])

                profile_data = {}
                if profile_info_path.exists():
                    try:
                        import json
                        with open(profile_info_path, "r", encoding="utf-8") as f:
                            profile_data = json.load(f)
                    except: pass
                
                posts_data = []
                if out_ndjson.exists():
                    try:
                        import json
                        with open(out_ndjson, "r", encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if line:
                                    posts_data.append(json.loads(line))
                    except Exception as e:
                        logger.warning("[worker %s] Lỗi đọc file posts nsjson: %s", worker_id, e)

                page_data["profile_info"] = profile_data
                page_data["posts"] = posts_data
                page_data["posts_collected"] = len(seen_ids)
                page_data["output_ndjson"] = str(out_ndjson)

            except Exception as exc:
                import traceback
                logger.warning("[worker %s] Failed on %s: %s\n%s", worker_id, url, exc, traceback.format_exc())
                page_data = {"url": url, "error": str(exc)}

            results.append((index, page_data))

            is_last_page = position == len(indexed_pages) - 1
            if not is_last_page:
                wait_for_seconds(driver, wait_between_pages)
        return results
    finally:
        if driver is not None:
            driver.quit()
            terminate_chrome_process(driver)
        port_queue.put(debug_port)
        logger.info("[worker %s] Finished (port %s)", worker_id, debug_port)


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Facebook crawler")
    parser.add_argument(
        "--max-workers",
        type=int,
        dest="max_workers",
        help="Override max worker threads (takes precedence over .env/config).",
    )
    parser.add_argument(
        "--selector-module",
        dest="selector_module",
        help="Selector module to use (overrides env/config).",
    )
    args = parser.parse_args()
    '''
    Load cấu hình crawler
    '''
    config = load_config(DEFAULT_CONFIG_PATH)
    crawl_cfg = config["crawl"]
    login_cfg = config["login"]

    env = load_env_file(".env")
    cookies_raw = env.get("COOKIES", "")
    user_agent = env.get("USER_AGENT", "")
    user_agents_file = env.get("USER_AGENTS_FILE", "user_agents.txt").strip() or "user_agents.txt"
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

    '''
    Arguments to build driver in Selenium -> Đưa ra thành 1 hàm load config
    '''
    headless = str_to_bool(env.get("HEADLESS"), login_cfg.get("headless", False))
    pages_file = crawl_cfg.get("pages_file") or DEFAULT_PAGES_FILE
    wait_after_load = int(crawl_cfg.get("wait_after_load", 3))
    wait_between_pages = int(crawl_cfg.get("wait_between_pages", 0))
    element_timeout = int(crawl_cfg.get("element_timeout", 15))
    login_stagger_seconds = int(crawl_cfg.get("login_stagger_seconds", 2))
    output_file = crawl_cfg.get("output_file", "crawl_results.json")
    default_wait_cfg: Dict[str, Any] | None = None
    selector_payload = None
    selector_source = "none"
    selector_debug_cfg: Dict[str, Any] | None = None

    # Resolve selector payload with remote download + cache fallback.
    selector_root = None
    if isinstance(config.get("selectors"), dict):
        selector_root = config["selectors"]
    elif isinstance(crawl_cfg.get("selectors"), dict):
        selector_root = crawl_cfg["selectors"]

    selector_modules = _normalize_selector_modules(selector_root)
    selector_module = (
        args.selector_module
        or env.get("SELECTOR_MODULE")
        or crawl_cfg.get("selector_module")
        or crawl_cfg.get("module")
    )
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

    resolved_payload, selector_source = resolve_selector_payload(local_selector, env)
    if resolved_payload is not None:
        try:
            selector_payload = validate_selector_payload(resolved_payload)
        except ValueError as exc:
            # If remote/cache payload is invalid, fall back to local config if possible.
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
                f"site={site} module={module} page={page} env={env_name} "
                f"version={version} source={selector_source}"
            )
            if selector_debug_cfg.get("log_config", True):
                logger.info(
                    "[selectors] Config:\n"
                    + json.dumps(selector_payload, ensure_ascii=False, indent=2)
                )

    locator_guard_mode = None
    if isinstance(locator_guard_cfg, dict):
        locator_guard_mode = (
            locator_guard_cfg.get("mode")
            or locator_guard_cfg.get("level")
            or locator_guard_cfg.get("severity")
        )
    elif isinstance(locator_guard_cfg, str):
        locator_guard_mode = locator_guard_cfg

    locator_guard_mode = (
        env.get("LOCATOR_GUARD")
        or locator_guard_mode
        or "warn"
    )
    guard_fragile_locators(elements_cfg, locator_guard_mode)
    port_min = int(env.get("PORT_RANGE_MIN") or login_cfg.get("port_min") or 8000)
    port_max = int(env.get("PORT_RANGE_MAX") or login_cfg.get("port_max") or 9999)
    
    if not elements_cfg:
        raise ValueError(
            "No elements configured. Please add elements under selectors.elements "
            "in configs/modules/*.json or under crawl.elements (legacy)."
        )

    pages = read_pages(pages_file)
    configured_max_workers = (
        args.max_workers
        if args.max_workers is not None
        else env.get("MAX_WORKERS") or crawl_cfg.get("max_workers") or min(5, len(pages))
    )
    max_workers = resolve_max_workers(
        configured_max_workers,
        len(pages),
        login_method,
        len(profile_dirs),
    )
    indexed_results: Dict[int, Dict[str, Any]] = {}
    page_batches = split_pages_for_workers(pages, max_workers)
    port_pool_size = int(
        env.get("PORT_POOL_SIZE")
        or login_cfg.get("port_pool_size")
        or max_workers
    )
    logger.info("port_pool_size: %s", port_pool_size)
    port_pool_size = max(port_pool_size, max_workers)
    port_queue = build_port_queue(port_min, port_max, port_pool_size)

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
                selector_module=selector_module,
            )
            for worker_id, batch in enumerate(page_batches, start=1)
        ]

        for future in as_completed(futures):
            for index, page_data in future.result():
                indexed_results[index] = page_data

    results = [indexed_results[index] for index in range(len(pages))]

    with open(output_file, "w", encoding="utf-8") as file:
        json.dump(results, file, ensure_ascii=False, indent=2)
    logger.info(
        "[crawl] Saved %s records to %s using %s worker(s)",
        len(results),
        output_file,
        max_workers,
    )


if __name__ == "__main__":
    main()
