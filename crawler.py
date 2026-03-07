from __future__ import annotations

import json
import os
import queue
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple

from utils import (
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
    select_working_proxy,
    split_pages_for_workers,
    str_to_bool,
    terminate_chrome_process,
    validate_selector_payload,
    wait_for_page_ready,
    wait_for_seconds,
)


DEFAULT_CONFIG_PATH = "config.json"
DEFAULT_PAGES_FILE = "pages.txt"


def crawl_page(
    driver,
    url: str,
    elements_cfg: List[Dict[str, Any]],
    wait_after_load: int,
    element_timeout: int,
    default_wait_cfg: Dict[str, Any] | None,
    selector_debug_cfg: Dict[str, Any] | None,
) -> Dict[str, Any]:
    print(f"[crawl] Visiting {url}")
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
            print(f"[crawl] Failed to capture '{name}' on {url}: {exc}")
            value = None
        data[name] = value
    return data


def crawl_pages_batch(
    worker_id: int,
    indexed_pages: List[Tuple[int, str]],
    *,
    login_method: str,
    cookies_raw: str,
    user_agent: str,
    headless: bool,
    profile_dir: str,
    proxy: str | None,
    chrome_binary: str | None,
    port_queue: queue.Queue[int],
    elements_cfg: List[Dict[str, Any]],
    wait_after_load: int,
    wait_between_pages: int,
    element_timeout: int,
    login_stagger_seconds: int,
    default_wait_cfg: Dict[str, Any] | None,
    selector_debug_cfg: Dict[str, Any] | None,
) -> List[Tuple[int, Dict[str, Any]]]:
    profile_label = profile_dir or "cookies-session"
    print(
        f"[worker {worker_id}] Starting with {len(indexed_pages)} page(s) "
        f"using {profile_label}"
    )
    if login_stagger_seconds > 0 and worker_id > 1:
        delay = login_stagger_seconds * (worker_id - 1)
        print(f"[worker {worker_id}] Waiting {delay}s before login")
        time.sleep(delay)

    debug_port = port_queue.get()
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
            )
        except Exception as exc:
            print(f"[worker {worker_id}] Login failed: {exc}")
            return [
                (index, {"url": url, "error": f"login_failed: {exc}"})
                for index, url in indexed_pages
            ]

        results: List[Tuple[int, Dict[str, Any]]] = []
        for position, (index, url) in enumerate(indexed_pages):
            try:
                page_data = crawl_page(
                    driver,
                    url,
                    elements_cfg,
                    wait_after_load,
                    element_timeout,
                    default_wait_cfg,
                    selector_debug_cfg,
                )
            except Exception as exc:
                print(f"[worker {worker_id}] Failed on {url}: {exc}")
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
        print(f"[worker {worker_id}] Finished (port {debug_port})")


def main() -> None:
    config = load_config(DEFAULT_CONFIG_PATH)
    crawl_cfg = config["crawl"]
    login_cfg = config["login"]

    env = load_env_file(".env")
    cookies_raw = env.get("COOKIES", "")
    user_agent = env.get("USER_AGENT", "")
    chrome_binary = env.get("CHROME_BINARY", "").strip() or None
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

    # arguments to build driver in Selenium
    headless = str_to_bool(env.get("HEADLESS"), login_cfg.get("headless", False))
    pages_file = crawl_cfg.get("pages_file") or DEFAULT_PAGES_FILE
    wait_after_load = int(crawl_cfg.get("wait_after_load", 3))
    wait_between_pages = int(crawl_cfg.get("wait_between_pages", 0))
    element_timeout = int(crawl_cfg.get("element_timeout", 15))
    login_stagger_seconds = int(crawl_cfg.get("login_stagger_seconds", 2))
    output_file = crawl_cfg.get("output_file", "crawl_results.json")
    default_wait_cfg: Dict[str, Any] | None = None
    selector_payload = None
    selector_debug_cfg: Dict[str, Any] | None = None
    
    if isinstance(config.get("selectors"), dict):
        selector_payload = validate_selector_payload(config["selectors"])
    elif isinstance(crawl_cfg.get("selectors"), dict):
        selector_payload = validate_selector_payload(crawl_cfg["selectors"])

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
            print(
                "[selectors] Using selector config "
                f"site={site} module={module} page={page} env={env_name} "
                f"version={version}"
            )
            if selector_debug_cfg.get("log_config", True):
                print(
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
            "in config.json or under crawl.elements (legacy)."
        )

    pages = read_pages(pages_file)
    max_workers = resolve_max_workers(
        env.get("MAX_WORKERS") or crawl_cfg.get("max_workers") or min(5, len(pages)),
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
    print("port_pool_size: ", port_pool_size)
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
                user_agent=user_agent,
                headless=headless,
                profile_dir=profile_dirs[(worker_id - 1) % len(profile_dirs)],
                proxy=proxy or None,
                chrome_binary=chrome_binary,
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

    results = [indexed_results[index] for index in range(len(pages))]

    with open(output_file, "w", encoding="utf-8") as file:
        json.dump(results, file, ensure_ascii=False, indent=2)
    print(
        f"[crawl] Saved {len(results)} records to {output_file} "
        f"using {max_workers} worker(s)"
    )


if __name__ == "__main__":
    main()
