from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from logs.loging_config import logger
from . import selector_posts


def read_ndjson(path: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not path.exists():
        return items

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                items.append(obj)
    return items


def write_ndjson(items: Iterable[Dict[str, Any]], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def enrich_posts_with_comments(
    driver,
    posts: List[Dict[str, Any]],
    *,
    max_posts: Optional[int] = None,
    sleep_between_posts: float = 0.5,
    log_prefix: str = "[CMT2]",
) -> List[Dict[str, Any]]:
    """
    Phase 2: duyệt các post trong danh sách, mở từng `post['link']` để crawl comment chi tiết
    (comment_id/reply_comment_id, nickname, nội dung, giờ, react...),
    sau đó gắn lại vào `post['comments']`.
    """
    if not posts:
        return posts

    limit = max_posts if isinstance(max_posts, int) and max_posts > 0 else len(posts)
    processed = 0

    for idx, post in enumerate(posts):
        if processed >= limit:
            break

        if not isinstance(post, dict):
            continue

        link = (post.get("link") or "").strip()
        if not link:
            continue

        if post.get("comments_crawled") is True and isinstance(post.get("comments"), list) and post["comments"]:
            continue

        try:
            logger.info("%s #%d crawl comments for %s", log_prefix, idx, link)
            # Dùng `link` làm source_url để namespace selector đúng (/groups/ vs profile/page)
            comments = selector_posts.parse_comment(driver, link, link)
            post["comments"] = comments
            post["comments_crawled"] = True
            post["comments_extracted"] = len(comments) if isinstance(comments, list) else 0
        except Exception as exc:
            logger.warning("%s #%d failed crawling comments for %s: %s", log_prefix, idx, link, exc)
            post["comments_crawled"] = False
            post["comments_error"] = str(exc)

        processed += 1
        if sleep_between_posts > 0:
            time.sleep(sleep_between_posts)

    return posts

