from __future__ import annotations

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
from src.fbprofile.browser.get_group_info import get_group_members
from src.fbprofile.browser.driver import create_chrome
from src.utils.drivers import login_facebook_with_cookies

try:
    from .get_graphql_response import (
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
    from .group_media_browser import (
        extract_group_general_info,
        merge_group_general_info,
        resolve_graphql_tokens,
    )
    from .group_media_common import default_output_path, extract_group_id_from_url
    from .group_media_extract import (
        build_group_info_document,
        build_media_post_urls,
        crawl_group_media_posts,
        extract_media_ids,
    )
    from .group_media_io import load_cookie_header
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
        posts = result.get("posts") if isinstance(result, dict) else None
        if isinstance(posts, list):
            logger.info("[GROUP] ✅ Xong Posts (%s posts)", len(posts))
            # logger.info("posts[0]: ", posts)
            # Phase 2: mở từng post link để crawl comment_id + nội dung chi tiết.
            phase2_enabled = True
            if phase2_enabled and posts:
                try:
                    from src.fbprofile.browser.comments_phase2 import enrich_posts_with_comments, write_ndjson
                    enriched_posts = enrich_posts_with_comments(
                        driver,
                        posts,
                        max_posts=1000,
                        sleep_between_posts=float(os.getenv("COMMENTS_PHASE2_SLEEP", "0.5") or 0.5),
                        log_prefix="[GROUP][CMT2]",
                    )
                    posts = enriched_posts
                    write_ndjson(
                        enriched_posts,
                            "outputs/posts_all_with_comments.ndjson",
                        )
                except Exception as exc:
                    logger.warning("[GROUP] Phase2 crawl comments failed: %s", exc)
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
