from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple


from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from login import (
    build_driver,
    get_facebook_login_debug_state,
    login_facebook_with_cookies,
    verify_facebook_login_state,
)
from utils import load_env_file, str_to_bool, load_config, select_working_proxy


DEFAULT_CONFIG_PATH = "config.yml"
DEFAULT_PAGES_FILE = "pages.txt"


def parse_profile_dirs(raw_value: Any) -> List[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        parts = raw_value.replace("\n", ",").split(",")
    elif isinstance(raw_value, list):
        parts = raw_value
    else:
        return []

    profile_dirs: List[str] = []
    seen: set[str] = set()
    for part in parts:
        candidate = str(part).strip()
        if not candidate:
            continue
        normalized = os.path.abspath(os.path.expanduser(candidate))
        if normalized in seen:
            continue
        seen.add(normalized)
        profile_dirs.append(normalized)
    return profile_dirs


def resolve_profile_dirs(
    env: Dict[str, str],
    crawl_cfg: Dict[str, Any],
    login_cfg: Dict[str, Any],
) -> List[str]:
    profile_dirs = parse_profile_dirs(env.get("PROFILE_DIRS"))
    if profile_dirs:
        return profile_dirs

    profile_dirs = parse_profile_dirs(crawl_cfg.get("profile_dirs"))
    if profile_dirs:
        return profile_dirs

    fallback_profile_dir = (
        env.get("PROFILE_DIR")
        or login_cfg.get("profile_dir")
        or os.path.join(os.getcwd(), "chrome_profile")
    )
    return parse_profile_dirs([fallback_profile_dir])


def read_pages(path: str) -> List[str]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Pages list not found: {path}")
    with open(path, "r", encoding="utf-8") as file:
        pages = [
            line.strip()
            for line in file
            if line.strip() and not line.strip().startswith("#")
        ]
    if not pages:
        raise ValueError("pages.txt is empty. Please add at least one URL.")
    return pages


def resolve_max_workers(
    configured_value: Any,
    total_pages: int,
    login_method: str,
    available_profiles: int,
) -> int:
    try:
        max_workers = int(configured_value or 1)
    except (TypeError, ValueError):
        max_workers = 1

    max_workers = max(1, min(max_workers, total_pages))

    if login_method == "profile":
        if available_profiles <= 1:
            if max_workers > 1:
                print(
                    "[crawl] Only one profile is configured, "
                    "so multi-threading is disabled."
                )
            return 1
        return min(max_workers, available_profiles)

    if login_method == "cookies" and max_workers > 1:
        print(
            "[crawl] LOGIN_METHOD=cookies now runs with one worker by default. "
            "Use multiple Facebook profiles for safe parallel crawling."
        )
        return 1
    return max_workers


def resolve_by(by_value: str) -> str:
    mapping = {
        "css": By.CSS_SELECTOR,
        "xpath": By.XPATH,
        "id": By.ID,
        "name": By.NAME,
        "tag": By.TAG_NAME,
        "class": By.CLASS_NAME,
        "link_text": By.LINK_TEXT,
        "partial_link_text": By.PARTIAL_LINK_TEXT,
    }
    key = by_value.strip().lower()
    if key not in mapping:
        supported = ", ".join(mapping.keys())
        raise ValueError(f"Unsupported locator strategy '{by_value}'. Use: {supported}")
    return mapping[key]


def extract_element(
    driver,
    element_cfg: Dict[str, Any],
    timeout: int,
) -> str | None:
    by_value = element_cfg.get("by", "css")
    selector = element_cfg.get("selector")
    attr = element_cfg.get("attribute", "text")
    required = element_cfg.get("required", False)

    if not selector:
        raise ValueError("Each element definition must include a 'selector'.")

    by = resolve_by(by_value)
    try:
        element = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((by, selector))
        )
    except TimeoutException:
        if required:
            raise TimeoutException(
                f"Timed out waiting for element '{element_cfg.get('name', selector)}'"
            )
        return None

    if attr == "text":
        return element.text.strip()
    return element.get_attribute(attr)


def crawl_page(
    driver,
    url: str,
    elements_cfg: List[Dict[str, Any]],
    wait_after_load: int,
    element_timeout: int,
) -> Dict[str, Any]:
    print(f"[crawl] Visiting {url}")
    driver.get(url)
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    time.sleep(max(wait_after_load, 0))

    data: Dict[str, Any] = {"url": url}
    for element_cfg in elements_cfg:
        name = element_cfg.get("name") or element_cfg.get("selector")
        try:
            value = extract_element(driver, element_cfg, element_timeout)
        except Exception as exc:
            print(f"[crawl] Failed to capture '{name}' on {url}: {exc}")
            value = None
        data[name] = value
    return data


def split_pages_for_workers(
    pages: List[str],
    max_workers: int,
) -> List[List[Tuple[int, str]]]:
    batches: List[List[Tuple[int, str]]] = [[] for _ in range(max_workers)]
    for index, url in enumerate(pages):
        batches[index % max_workers].append((index, url))
    return [batch for batch in batches if batch]


def create_logged_in_driver(
    login_method: str,
    cookies_raw: str,
    user_agent: str,
    headless: bool,
    profile_dir: str | None,
    proxy: str | None,
):
    driver_profile_dir = profile_dir if login_method == "profile" else None
    driver = build_driver(
        user_agent=user_agent,
        headless=headless,
        profile_dir=driver_profile_dir,
        proxy=proxy,
    )

    try:
        if login_method == "cookies":
            ok = login_facebook_with_cookies(driver, cookies_raw)
        elif login_method == "profile":
            ok = verify_facebook_login_state(driver)
        else:
            raise ValueError("LOGIN_METHOD must be either 'cookies' or 'profile'.")

        if not ok:
            debug_state = get_facebook_login_debug_state(driver)
            raise RuntimeError(
                "Unable to verify Facebook login. "
                f"Facebook redirected the session: {debug_state}"
            )
        return driver
    except Exception:
        driver.quit()
        raise


def crawl_pages_batch(
    worker_id: int,
    indexed_pages: List[Tuple[int, str]],
    *,
    login_method: str,
    cookies_raw: str,
    user_agent: str,
    headless: bool,
    profile_dir: str | None,
    proxy: str | None,
    elements_cfg: List[Dict[str, Any]],
    wait_after_load: int,
    wait_between_pages: int,
    element_timeout: int,
    login_stagger_seconds: int,
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

    try:
        driver = create_logged_in_driver(
            login_method=login_method,
            cookies_raw=cookies_raw,
            user_agent=user_agent,
            headless=headless,
            profile_dir=profile_dir,
            proxy=proxy,
        )
    except Exception as exc:
        print(f"[worker {worker_id}] Login failed: {exc}")
        return [
            (index, {"url": url, "error": f"login_failed: {exc}"})
            for index, url in indexed_pages
        ]

    results: List[Tuple[int, Dict[str, Any]]] = []
    try:
        for position, (index, url) in enumerate(indexed_pages):
            try:
                page_data = crawl_page(
                    driver,
                    url,
                    elements_cfg,
                    wait_after_load,
                    element_timeout,
                )
            except Exception as exc:
                print(f"[worker {worker_id}] Failed on {url}: {exc}")
                page_data = {"url": url, "error": str(exc)}

            results.append((index, page_data))

            is_last_page = position == len(indexed_pages) - 1
            if not is_last_page:
                time.sleep(max(wait_between_pages, 0))
        return results
    finally:
        driver.quit()
        print(f"[worker {worker_id}] Finished")


def main() -> None:
    config = load_config(DEFAULT_CONFIG_PATH)
    crawl_cfg = config["crawl"]
    login_cfg = config["login"]

    env = load_env_file(".env")
    cookies_raw = env.get("COOKIES", "")
    user_agent = env.get("USER_AGENT", "")
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
    elements_cfg = crawl_cfg.get("elements", [])

    if not elements_cfg:
        raise ValueError(
            "No elements configured. Please add entries under crawl.elements in config.yml"
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
                elements_cfg=elements_cfg,
                wait_after_load=wait_after_load,
                wait_between_pages=wait_between_pages,
                element_timeout=element_timeout,
                login_stagger_seconds=login_stagger_seconds,
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
