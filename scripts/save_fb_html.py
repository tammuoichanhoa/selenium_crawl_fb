from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List
from urllib.parse import urlparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.utils.ports import build_port_queue
from src.core.driver_factory import create_logged_in_driver, terminate_chrome_process
from src.utils.env import load_env_file
from src.utils.pages import read_pages
from src.utils.logging_setup import setup_logging
from src.utils.waits import wait_for_page_ready, wait_for_seconds


DEFAULT_PAGES_FILE = "data/profiles.txt"
DEFAULT_OUTPUT_DIR = os.path.join("debug_artifacts", "html_pages")
DEFAULT_WAIT_AFTER_LOAD = 3
DEFAULT_WAIT_BETWEEN_PAGES = 2
logger = logging.getLogger(__name__)


def slugify_url(url: str, max_len: int = 120) -> str:
    parsed = urlparse(url)
    candidate = f"{parsed.netloc}{parsed.path}"
    if parsed.query:
        candidate = f"{candidate}_{parsed.query}"
    candidate = candidate.strip().lower()
    candidate = re.sub(r"[^a-z0-9]+", "_", candidate)
    candidate = re.sub(r"_+", "_", candidate).strip("_")
    if not candidate:
        return "page"
    return candidate[:max_len]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Save DOM/HTML of Facebook pages.",
    )
    parser.add_argument(
        "--url",
        help="Single Facebook URL to save",
    )
    parser.add_argument(
        "--pages",
        default=None,
        help="Path to a file with URLs (one per line)",
    )
    parser.add_argument(
        "--out-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory for HTML files",
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=DEFAULT_WAIT_AFTER_LOAD,
        help="Seconds to wait after page load",
    )
    parser.add_argument(
        "--between",
        type=float,
        default=DEFAULT_WAIT_BETWEEN_PAGES,
        help="Seconds to wait between pages",
    )
    parser.add_argument(
        "--headless",
        default=None,
        help="true/false to override headless (optional)",
    )
    parser.add_argument(
        "--profile-dir",
        default=None,
        help="Chrome profile dir (optional)",
    )
    return parser.parse_args()


def _str_to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return default


def main() -> None:
    setup_logging()
    args = parse_args()

    env = load_env_file(".env")
    cookies_raw = env.get("COOKIES", "")
    user_agent = env.get("USER_AGENT", "")

    login_method = env.get("LOGIN_METHOD", "cookies").strip().lower()
    headless = _str_to_bool(args.headless, _str_to_bool(env.get("HEADLESS"), False))

    profile_dir = (
        args.profile_dir
        or env.get("PROFILE_DIR")
        or "./chrome_profile"
    )
    os.makedirs(profile_dir, exist_ok=True)

    pages: List[str] = []
    if args.url:
        pages = [args.url]
    else:
        pages_file = args.pages or env.get("PAGES_FILE") or DEFAULT_PAGES_FILE
        pages = read_pages(pages_file)

    if not pages:
        raise RuntimeError("No URLs provided. Use --url or --pages.")

    port_min = int(env.get("PORT_RANGE_MIN") or 8000)
    port_max = int(env.get("PORT_RANGE_MAX") or 9999)
    port_queue = build_port_queue(port_min, port_max, 1)
    debug_port = port_queue.get()

    output_dir = args.out_dir
    os.makedirs(output_dir, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    index_rows: List[Dict[str, Any]] = []
    driver = None
    try:
        driver = create_logged_in_driver(
            login_method=login_method,
            cookies_raw=cookies_raw,
            user_agent=user_agent,
            headless=headless,
            profile_dir=profile_dir,
            proxy=None,
            chrome_binary=None,
            debug_port=debug_port,
            home_url="https://www.facebook.com/",
            locale_url="https://www.facebook.com/?locale=vi_VN",
            chrome_binary_win_path=None,
            chrome_binary_candidates=None,
        )

        for idx, url in enumerate(pages, start=1):
            logger.info("[save_html] Visiting %s", url)
            try:
                driver.get(url)
                wait_for_page_ready(driver, 20)
                wait_for_seconds(driver, args.wait)
                html = driver.page_source or ""

                slug = slugify_url(url)
                filename = f"{idx:03d}_{slug}_{run_id}.html"
                out_path = os.path.join(output_dir, filename)
                with open(out_path, "w", encoding="utf-8") as file:
                    file.write(html)

                index_rows.append(
                    {
                        "index": idx,
                        "url": url,
                        "file": out_path,
                        "saved_at": datetime.now().isoformat(timespec="seconds"),
                        "status": "ok",
                    }
                )
                logger.info("[save_html] Saved %s", out_path)
            except Exception as exc:
                index_rows.append(
                    {
                        "index": idx,
                        "url": url,
                        "file": None,
                        "saved_at": datetime.now().isoformat(timespec="seconds"),
                        "status": "error",
                        "error": str(exc),
                    }
                )
                logger.warning("[save_html] Failed %s: %s", url, exc)

            is_last_page = idx == len(pages)
            if not is_last_page:
                wait_for_seconds(driver, args.between)
    finally:
        if driver is not None:
            driver.quit()
            terminate_chrome_process(driver)
        port_queue.put(debug_port)

    index_path = os.path.join(output_dir, f"index_{run_id}.json")
    with open(index_path, "w", encoding="utf-8") as file:
        json.dump(index_rows, file, ensure_ascii=False, indent=2)
    logger.info("[save_html] Wrote index %s", index_path)


if __name__ == "__main__":
    main()
