import json
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from logs.loging_config import logger
from src.utils.selectors import resolve_locator, validate_selector_payload
from .stable_scroll import scroll_until_stable

import json
import os
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from logs.loging_config import logger
from src.fbprofile.browser.driver import create_chrome
from src.utils.env import str_to_bool
from src.utils.drivers import login_facebook_with_cookies

try:
    from src.utils.groups.get_graphql_response import (
        DEFAULT_USER_AGENT,
        GraphQLTokens,
        extract_media_page,
        fetch_group_info_response,
        fetch_next_group_info_response
    )
except ImportError:
    from groups.get_graphql_response import (  # type: ignore
        DEFAULT_USER_AGENT,
        GraphQLTokens,
        extract_media_page,
        fetch_group_info_response,
        fetch_next_group_info_response
    )

try:
    from src.utils.groups.group_media_browser import (
        extract_group_general_info,
        merge_group_general_info,
        resolve_graphql_tokens,
    )

    from src.utils.groups.group_media_common import default_output_path, extract_group_id_from_url
    from src.utils.groups.group_media_extract import (
        build_group_info_document,
        build_media_post_urls,
        crawl_group_media_posts,
        extract_media_ids,
    )
    from src.utils.groups.group_media_io import load_cookie_header
except ImportError:
    from groups.group_media_browser import (  # type: ignore
        extract_group_general_info,
        merge_group_general_info,
        resolve_graphql_tokens,
    )
    from groups.group_media_common import default_output_path, extract_group_id_from_url  # type: ignore
    from groups.group_media_extract import (  # type: ignore
        build_group_info_document,
        build_media_post_urls,
        crawl_group_media_posts,
        extract_media_ids,
    )
from groups.group_media_io import load_cookie_header  # type: ignore


def scrape_full_group_info(
    driver,
    target_url: str,
    output_path: Path,
    *,
    scroll_until_stable_cfg: Dict[str, Any] | None = None,
    cookie_header: str | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    fb_dtsg: str | None = None,
    max_pages: int = 1,
    photo_limit: int | None = None,
    wait_seconds: float = 2.0,
    skip_posts: bool = False,
) -> dict:
    """
    Orchestrator: lấy thông tin GROUP (selectors) và lưu JSON ra output_path.
    Chỉ phục vụ nhánh group trong crawler.py.
    """
    logger.info("--- BẮT ĐẦU QUÉT INFO GROUP (FULL): %s ---", target_url)

    group_id = None
    try:
        group_id = extract_group_id_from_url(target_url)
    except Exception:
        pass

    full_data: dict[str, Any] = {
        "url": target_url,
        "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "basic_info": {
            "group_id": group_id,
            "reference_token": f"g.{group_id}" if group_id else None,
            "entity_type": "facebook_group",
            "name": None,
            "members": None,
            "cover_photo": None,
            "privacy": None,
        },
        "introduction": {"info": []},
        "members": [],
        "posts": [],
    }

    try:
        group_general_info = extract_group_general_info(driver, group_url=target_url)
        merge_group_general_info(full_data, group_general_info)
        logger.info("[GROUP] ✅ Xong Basic Info")
    except Exception as exc:
        logger.warning("[GROUP] Failed to extract general info: %s", exc)

    def _cookie_header_from_driver() -> str | None:
        try:
            cookie_items = driver.get_cookies()
        except Exception as exc:
            logger.warning("[GROUP][POSTS] Cannot read cookies from driver: %s", exc)
            return None

        if not isinstance(cookie_items, list) or not cookie_items:
            return None

        parts: list[str] = []
        for item in cookie_items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            if not name:
                continue
            parts.append(f"{name}={value}")
        header = "; ".join(parts).strip()
        return header or None

    def _resolve_user_agent_from_driver() -> str | None:
        try:
            ua = driver.execute_script("return navigator.userAgent")  # type: ignore[attr-defined]
        except Exception:
            return None
        return ua.strip() if isinstance(ua, str) and ua.strip() else None

    try:
        if not skip_posts:
            resolved_cookie_header = (cookie_header or "").strip() or _cookie_header_from_driver()
            if not resolved_cookie_header:
                try:
                    resolved_cookie_header = load_cookie_header(cookie_header=None, cookie_file=None)
                except Exception as exc:
                    logger.warning("[GROUP][POSTS] Missing cookie header; skip posts: %s", exc)
                    resolved_cookie_header = ""

            resolved_user_agent = (user_agent or "").strip() or _resolve_user_agent_from_driver() or DEFAULT_USER_AGENT

            if resolved_cookie_header:
                resolved_group_id = group_id or extract_group_id_from_url(target_url)
                if not resolved_group_id:
                    raise ValueError(f"Cannot resolve group id from URL: {target_url}")

                tokens = resolve_graphql_tokens(
                    driver,
                    cookie_header=resolved_cookie_header,
                    group_url=target_url,
                    fb_dtsg=fb_dtsg,
                )

                pages = fetch_group_media_pages(
                    group_id=resolved_group_id,
                    cookie_header=resolved_cookie_header,
                    user_agent=resolved_user_agent,
                    fb_dtsg=fb_dtsg,
                    tokens=tokens,
                    max_pages=max_pages,
                )
                logger.info("[GROUP][POSTS] Found %s pages", len(pages))
                if not pages:
                    raise ValueError(f"No media page returned for group {resolved_group_id}")

                result = crawl_group_media_posts(
                    driver,
                    group_url=target_url,
                    first_page=pages[0],
                    next_pages=pages[1:],
                    photo_limit=photo_limit,
                    wait_seconds=wait_seconds,
                )
                posts = result.get("posts") if isinstance(result, dict) else None
                print("posts: ", posts)
                if isinstance(posts, list):
                    full_data["posts"] = posts
                    logger.info("[GROUP] ✅ Xong Posts (%s posts)", len(posts))

                    # Phase 2: mở từng post link để crawl comment_id + nội dung chi tiết.
                    try:
                        phase2_enabled = str_to_bool(os.getenv("COMMENTS_PHASE2", "1"), True)
                    except Exception:
                        phase2_enabled = True

                    if phase2_enabled and full_data["posts"]:
                        try:
                            from src.fbprofile.browser.comments_phase2 import enrich_posts_with_comments, write_ndjson

                            max_posts_raw = os.getenv("COMMENTS_PHASE2_MAX_POSTS", "").strip()
                            max_posts = int(max_posts_raw) if max_posts_raw else None
                            enriched_posts = enrich_posts_with_comments(
                                driver,
                                full_data["posts"],
                                max_posts=max_posts,
                                sleep_between_posts=float(os.getenv("COMMENTS_PHASE2_SLEEP", "0.5") or 0.5),
                                log_prefix="[GROUP][CMT2]",
                            )
                            full_data["posts"] = enriched_posts
                            write_ndjson(
                                enriched_posts,
                                Path(output_path).parent / "posts_all_with_comments.ndjson",
                            )
                        except Exception as exc:
                            logger.warning("[GROUP] Phase2 crawl comments failed: %s", exc)
                
            else:
                logger.warning("[GROUP][POSTS] No cookie header available; skip posts.")
    except Exception as exc:
        logger.warning("[GROUP] Failed to fetch posts via media pipeline: %s", exc)

    try:
        full_data["members"] = get_group_members(
            driver,
            target_url,
            scroll_until_stable_cfg=scroll_until_stable_cfg,
        )
        logger.info("[GROUP] ✅ Xong Members (%s người)", len(full_data["members"]))
    except Exception as exc:
        logger.warning("[GROUP] Failed to fetch members: %s", exc)

    try:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as output_file:
            json.dump(full_data, output_file, ensure_ascii=False, indent=4)
        logger.info("[GROUP] 💾 Đã lưu FULL info vào: %s", output_path)
    except Exception as save_err:
        logger.error("[GROUP] Không thể lưu file: %s", save_err)

    return full_data



# GROUP_SELECTOR_CONFIG_PATH = (
#     Path(__file__).resolve().parents[1] / "configs" / "modules" / "group.json"
# )
GROUP_SELECTOR_CONFIG_PATH = "/home/baoanh/Desktop/fb_crawler/selenium_crawl_fb/configs/modules/group.json"

@lru_cache(maxsize=1)
def _load_group_selector_config() -> dict:
    """Load group selectors from local JSON config."""
    with open(GROUP_SELECTOR_CONFIG_PATH, "r", encoding="utf-8") as config_file:
        return validate_selector_payload(json.load(config_file))


def _get_selector_entry(selector_name: str) -> dict | None:
    """Return one selector entry from config."""
    return _load_group_selector_config().get("elements", {}).get(selector_name)


def _build_locator_chain(selector_name: str, **format_kwargs) -> list[dict]:
    """Build primary + fallback locators for a selector key."""
    selector_cfg = _get_selector_entry(selector_name)
    if not isinstance(selector_cfg, dict):
        logger.warning(f"[GROUP] Chưa cấu hình selector: {selector_name}")
        return []

    locators = []
    primary = selector_cfg.get("primary")
    if isinstance(primary, dict):
        locators.append(dict(primary))

    fallbacks = selector_cfg.get("fallbacks")
    if isinstance(fallbacks, list):
        locators.extend(dict(fallback) for fallback in fallbacks if isinstance(fallback, dict))

    if format_kwargs:
        for locator in locators:
            value = locator.get("value") or locator.get("selector")
            if isinstance(value, str):
                locator["value"] = value.format(**format_kwargs)

    return locators


def _resolve_wait(selector_name: str, timeout: int | None = None) -> tuple[str, float]:
    """Resolve wait mode + timeout from selector config."""
    selector_cfg = _get_selector_entry(selector_name) or {}
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


def _find_elements_by_selector(driver, selector_name: str, timeout: int | None = None, **format_kwargs):
    """Find Selenium elements using selector config and fallback chain."""
    locators = _build_locator_chain(selector_name, **format_kwargs)
    if not locators:
        logger.warning("[GROUP][SELECTOR] %s has no configured locators", selector_name)
        return []

    wait_state, wait_seconds = _resolve_wait(selector_name, timeout=timeout)
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
                logger.info(
                    "[GROUP][SELECTOR] %s matched %s elements with by=%s value=%s",
                    selector_name,
                    len(elements),
                    by,
                    value,
                )
                return elements
            logger.warning(
                "[GROUP][SELECTOR] %s matched wait but returned 0 elements with by=%s value=%s",
                selector_name,
                by,
                value,
            )
        except TimeoutException:
            logger.warning(
                "[GROUP][SELECTOR] %s timeout after %.1fs",
                selector_name,
                wait_seconds,
            )
        except Exception:
            logger.exception("[GROUP][SELECTOR] %s failed while locating", selector_name)
            continue

    logger.warning("[GROUP][SELECTOR] %s did not match any locator", selector_name)
    return []


def _find_child_element(root, selector_name: str, **format_kwargs):
    """Find a child element from an existing Selenium node using config."""
    for locator in _build_locator_chain(selector_name, **format_kwargs):
        try:
            by, value = resolve_locator(locator)
            return root.find_element(by, value)
        except Exception:
            continue
    logger.warning("[GROUP][SELECTOR] child selector %s not found inside current card", selector_name)
    return None


def _find_elements_by_selector_no_wait(driver, selector_name: str, **format_kwargs):
    """Find Selenium elements without waiting; useful for progress counting while scrolling."""
    for locator in _build_locator_chain(selector_name, **format_kwargs):
        try:
            by, value = resolve_locator(locator)
            elements = driver.find_elements(by, value)
            if elements:
                return elements
        except Exception:
            continue
    return []


def _count_unique_selector_values(
    driver,
    selector_name: str,
    *,
    attr: str = "href",
    **format_kwargs,
) -> int:
    values = set()
    for element in _find_elements_by_selector_no_wait(driver, selector_name, **format_kwargs):
        try:
            value = element.get_attribute(attr) if attr else element.text
        except Exception:
            continue
        value = value.strip() if isinstance(value, str) else value
        if value:
            values.add(value)
    return len(values)

# ==========================================
# MEMBERS (Bạn bè)
# ==========================================
def get_group_members(
    driver,
    target_url,
    timeout: int = 5,
    scroll_until_stable_cfg: Dict[str, Any] | None = None,
) -> list:
    """Lấy danh sách Thành viên (có cuộn trang)."""
    members_list = []
    seen_group_urls = set()

    try:
        target_members = f"{target_url}/members"

        logger.info(f"[GROUP] Đang truy cập danh sách thành viên: {target_members}")
        driver.get(target_members)
        time.sleep(3)
        logger.info("[GROUP][MEMBERS] current_url=%s", driver.current_url)

        logger.info("[GROUP] Đang cuộn danh sách thành viên đến khi ổn định...")
        scroll_until_stable(
            driver,
            get_progress_count=lambda: _count_unique_selector_values(
                driver,
                "group.members.link",
                attr="href",
            ),
            log_prefix="[GROUP][MEMBERS]",
            config=scroll_until_stable_cfg,
            defaults={
                "max_scrolls": 5,
                "stable_rounds": 3,
                "scroll_pause_seconds": 2.0,
                "settle_pause_seconds": 0.5,
            },
        )

        logger.info("[GROUP] Đang trích xuất dữ liệu thành viên...")
        info_divs = _find_elements_by_selector(driver, "group.members.card", timeout=timeout)
        logger.info("[GROUP][MEMBERS] cards_found=%s", len(info_divs))
        if not info_divs:
            logger.warning(
                "[GROUP][MEMBERS] No member cards found. Check selectors group.members.card / group.members.link."
            )

        for index, info in enumerate(info_divs, start=1):
            try:
                member_data = {"name": None, "group_url": None, "avatar_url": None, "subtitle": ""}
                try:
                    link_element = _find_child_element(info, "group.members.link")
                    if link_element is None:
                        logger.warning("[GROUP][MEMBERS] card #%s skipped: missing link element", index)
                        continue
                    member_data["name"] = (link_element.text or "").strip()
                    member_data["group_url"] = link_element.get_attribute("href")
                except Exception:
                    logger.exception("[GROUP][MEMBERS] card #%s failed while reading link", index)
                    continue

                if not member_data["name"]:
                    try:
                        name_el = _find_child_element(info, "group.members.name")
                        if name_el is not None:
                            member_data["name"] = (name_el.text or "").strip()
                            logger.info(
                                "[GROUP][MEMBERS] card #%s recovered name via group.members.name: %r",
                                index,
                                member_data["name"],
                            )
                    except Exception:
                        logger.exception("[GROUP][MEMBERS] card #%s failed while reading name fallback", index)

                if not member_data["group_url"] or member_data["group_url"] in seen_group_urls:
                    reason = "missing href" if not member_data["group_url"] else "duplicate href"
                    logger.warning(
                        "[GROUP][MEMBERS] card #%s skipped: %s name=%r href=%r",
                        index,
                        reason,
                        member_data["name"],
                        member_data["group_url"],
                    )
                    continue
                seen_group_urls.add(member_data["group_url"])

                try:
                    sub_el = _find_child_element(info, "group.members.subtitle")
                    if sub_el is not None:
                        member_data["subtitle"] = sub_el.text.strip()
                except Exception:
                    logger.exception("[GROUP][MEMBERS] card #%s failed while reading subtitle", index)

                try:
                    avt_el = _find_child_element(info, "group.members.avatar")
                    if avt_el is not None:
                        member_data["avatar_url"] = (
                            avt_el.get_attribute("src")
                            or avt_el.get_attribute("xlink:href")
                            or avt_el.get_attribute("href")
                        )
                except Exception:
                    logger.exception("[GROUP][MEMBERS] card #%s failed while reading avatar", index)

                if member_data["name"]:
                    members_list.append(member_data)
                    logger.info(
                        "[GROUP][MEMBERS] accepted card #%s name=%r href=%r subtitle=%r avatar=%r",
                        index,
                        member_data["name"],
                        member_data["group_url"],
                        member_data["subtitle"],
                        bool(member_data["avatar_url"]),
                    )
                else:
                    logger.warning(
                        "[GROUP][MEMBERS] card #%s skipped: empty member name href=%r",
                        index,
                        member_data["group_url"],
                    )
            except Exception:
                logger.exception("[GROUP][MEMBERS] unexpected failure while parsing card #%s", index)
                continue

    except Exception as exc:
        logger.error(f"[GROUP] Lỗi lấy thành viên: {str(exc)}")

    logger.info("[GROUP][MEMBERS] extracted=%s unique_members", len(members_list))
    return members_list


def fetch_group_media_pages(
    *,
    group_id: str,
    cookie_header: str,
    user_agent: str = DEFAULT_USER_AGENT,
    fb_dtsg: str | None = None,
    tokens: GraphQLTokens | None = None,
    max_pages: int = 1,
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []

    response = fetch_group_info_response(
        group_id,
        cookie_header=cookie_header,
        user_agent=user_agent,
        fb_dtsg=fb_dtsg,
        tokens=tokens,
        scale=4,
    )
    pages.append(response)

    media_page = extract_media_page(response)
    cursor = media_page.get("cursor")
    has_next = bool(media_page.get("has_next"))
    page_count = 1

    unlimited_pages = max_pages == -1
    while has_next and cursor and (unlimited_pages or page_count < max_pages):
        response = fetch_next_group_info_response(
            group_id,
            cursor,
            cookie_header=cookie_header,
            user_agent=user_agent,
            fb_dtsg=fb_dtsg,
            tokens=tokens,
            scale=1,
        )
        pages.append(response)

        media_page = extract_media_page(response)
        cursor = media_page.get("cursor")
        has_next = bool(media_page.get("has_next"))
        page_count += 1
        time.sleep(2)

    return pages



def save_group_media_pipeline_output(output_path: str | Path, payload: dict[str, Any]) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def crawl_group_media_pipeline(
    driver,
    *,
    group_url: str,
    cookie_header: str,
    user_agent: str = DEFAULT_USER_AGENT,
    fb_dtsg: str | None = None,
    max_pages: int = 3,
    photo_limit: int | None = None,
    wait_seconds: float = 2.0,
    skip_posts: bool = False,
) -> dict[str, Any]:
    
    group_id = extract_group_id_from_url(group_url)
    if not group_id:
        raise ValueError(f"Cannot resolve group id from URL: {group_url}")

    if skip_posts:
        result = {
            "url": group_url,
            "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "basic_info": {
                "group_id": group_id,
                "reference_token": f"g.{group_id}",
                "entity_type": "facebook_group",
                "name": None,
                "members": None,
                "avatar_url": None,
                "cover_photo": None,
            },
            "viewer_capabilities": {
                "can_post": None,
                "can_create_album": None,
            },
            # "introduction": {
            #     "info": [],
            #     "activities": [],
            #     "rules": [],
            # },
            "members": [],
            "posts": [],
        }
    else:
        tokens = resolve_graphql_tokens(
            driver,
            cookie_header=cookie_header,
            group_url=group_url,
            fb_dtsg=fb_dtsg,
        )

        pages = fetch_group_media_pages(
            group_id=group_id,
            cookie_header=cookie_header,
            user_agent=user_agent,
            fb_dtsg=fb_dtsg,
            tokens=tokens,
            max_pages=max_pages,
        )
        print(f"Found {len(pages)} pages")
        if not pages:
            raise ValueError(f"No media page returned for group {group_id}")

        result = crawl_group_media_posts(
            driver,
            group_url=group_url,
            first_page=pages[0],
            next_pages=pages[1:],
            photo_limit=photo_limit,
            wait_seconds=wait_seconds,
        )

    try:
        group_general_info = extract_group_general_info(driver, group_url=group_url)
        merge_group_general_info(result, group_general_info)
    except Exception as exc:
        logger.warning("[GROUP_MEDIA] Failed to enrich group general info via selectors: %s", exc)

    # try:
    #     result["members"] = get_group_members(driver, group_url)
    # except Exception as exc:
    #     logger.warning("[GROUP_MEDIA] Failed to fetch group members via browser flow: %s", exc)

    return result

# =============================================================
#                       MAIN PROGRAM 
# =============================================================


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Crawl bài post của Facebook group từ media GraphQL + Selenium selectors.",
    )
    parser.add_argument("--group-url", default="https://www.facebook.com/groups/weibovietnamtruyendaiky")
    parser.add_argument("--cookie", default="")
    parser.add_argument("--cookie-file", default="/home/baoanh/Desktop/crawler/selenium_crawl_fb/groups/cookies.txt")
    parser.add_argument("--fb-dtsg", default='')
    parser.add_argument("--user-agent", default=os.getenv("USER_AGENT", "").strip() or DEFAULT_USER_AGENT)
    parser.add_argument("--max-pages", type=int, default=1, help="Số trang media GraphQL cần crawl; dùng -1 để crawl đến hết.")
    parser.add_argument("--photo-limit", type=int, default=1)
    parser.add_argument("--wait-seconds", type=float, default=float(os.getenv("GROUP_MEDIA_WAIT_SECONDS", "2")))
    parser.add_argument("--skip-posts",  default=False, action="store_true")
    parser.add_argument("--output", default="")
    parser.add_argument("--headless", action="store_true")
    
    return parser

def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()

    group_url = (args.group_url or "").strip()
    if not group_url:
        parser.error("Thiếu --group-url hoặc biến môi trường GROUP_URL.")

    cookie_header = load_cookie_header(cookie_header=args.cookie, cookie_file=args.cookie_file)
    
    output_path = Path(args.output).expanduser() if args.output else default_output_path(group_url)
    photo_limit = args.photo_limit if args.photo_limit > 0 else None

    driver = create_chrome(headless=bool(args.headless))
    try:
        if not login_facebook_with_cookies(driver, cookie_header):
            raise RuntimeError("Không đăng nhập được Facebook bằng cookie đã cung cấp.")

        payload = crawl_group_media_pipeline(
            driver,
            group_url=group_url,
            cookie_header=cookie_header,
            user_agent=args.user_agent,
            fb_dtsg=args.fb_dtsg,
            max_pages=(-1 if int(args.max_pages) == -1 else max(1, int(args.max_pages))),
            photo_limit=photo_limit,
            wait_seconds=max(0.0, float(args.wait_seconds)),
            skip_posts=bool(args.skip_posts),
        )
        saved_path = save_group_media_pipeline_output(output_path, payload)
        logger.info(f"Save collected data in {saved_path}")
        return 0
    finally:
        try:
            driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    raise SystemExit(main())
