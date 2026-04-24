"""
post_extractor.py
-----------------
Pipeline chính: thu thập post elements từ DOM, extract metadata,
ghi NDJSON, và hàm entry-point cho phase 1 (extract_selector_post, process_visible_selector_posts).
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By

from logs.loging_config import logger
from ...storage.ndjson import append_ndjson
from .element_helpers import keep_outermost_elements
from .expand_prune import expand_post_content_until_done
from .post_helpers import (
    build_post_identity,
    extract_author,
    extract_content,
    extract_engagement_counts,
    extract_source_id,
    extract_visibility,
    get_post_timestamp_and_id,
)
from .url_text_utils import extract_url_digits


# ---------------------------------------------------------------------------
# Debug dump
# ---------------------------------------------------------------------------

def _dump_post_debug(driver, post_element, context, idx: int) -> None:
    try:
        data = {
            "index": idx,
            "tag": post_element.tag_name,
            "text": post_element.text[:500],
            "html": post_element.get_attribute("outerHTML"),
            "context": context,
            "url": driver.current_url,
            "timestamp": datetime.utcnow().isoformat(),
        }
        with open(f"debug_post_{idx}.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.info(f"Dump failed: {e}")


# ---------------------------------------------------------------------------
# Collect visible post containers
# ---------------------------------------------------------------------------

def collect_visible_selector_posts(driver, source_url: str):
    seen, unique_ids = [], set()

    # Case 1: dialog trong feed
    elements = driver.find_elements(
        By.CSS_SELECTOR,
        "div[role='dialog'][aria-label='Trình xem ảnh'] div[role='complementary']",
    )
    # Case 2: navigate thẳng tới photo URL
    if not elements:
        elements = driver.find_elements(By.CSS_SELECTOR, "div[role='complementary']")
    # Case 3: fallback
    if not elements:
        elements = driver.find_elements(By.CSS_SELECTOR, "[data-name='media-viewer-nav-container']")

    elements = keep_outermost_elements(driver, elements)

    for element in elements:
        try:
            marker = element.id
        except Exception:
            marker = None
        if marker and marker in unique_ids:
            continue
        if marker:
            unique_ids.add(marker)
        seen.append(element)

    return seen


# ---------------------------------------------------------------------------
# Extract single post context
# ---------------------------------------------------------------------------

def _extract_selector_post_context(driver, post_element, group_url: str) -> Dict[str, Any]:
    author = extract_author(post_element, group_url)
    visibility = extract_visibility(post_element, group_url)
    engagement_counts, reaction_breakdown = extract_engagement_counts(post_element, group_url)
    content = extract_content(driver, post_element, group_url)
    timestamp_info = get_post_timestamp_and_id(driver, post_element, group_url)

    item = {
        "id": timestamp_info["post_id"],
        "rid": timestamp_info["post_id_raw"],
        "type": "story",
        "link": timestamp_info["timestamp_url"],
        "author_id": author.get("id", ""),
        "author": author.get("name", ""),
        "author_link": author.get("url", ""),
        "avatar": author.get("avatar", ""),
        "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp_text": timestamp_info["timestamp_text"],
        "content": content,
        "comments": [],
        "comments_crawled": False,
        "comment": engagement_counts["comment"],
        "share": engagement_counts["share"],
        "reactions": reaction_breakdown,
        "hashtag": re.findall(r"#\w+", content or ""),
        "source_id": extract_source_id(group_url),
        "visibility": visibility,
    }

    identity = build_post_identity(item)
    if not identity:
        return {"item": None}

    if not item["id"]:
        item["id"] = identity
    if not item["rid"]:
        item["rid"] = identity

    return {"item": item}


def extract_selector_post(driver, post_element, group_url: str) -> Optional[Dict[str, Any]]:
    context = _extract_selector_post_context(driver, post_element, group_url)
    return context["item"]


# ---------------------------------------------------------------------------
# Process visible posts (batch write)
# ---------------------------------------------------------------------------

def process_visible_selector_posts(
    driver,
    group_url: str,
    seen_ids: Set[str],
    out_path: Path,
    log_prefix: str = "",
    ts_state: dict = None,
) -> int:
    fresh: List[Dict[str, Any]] = []
    written_this_round: Set[str] = set()

    for idx, post_element in enumerate(collect_visible_selector_posts(driver, group_url)):
        try:
            item = extract_selector_post(driver, post_element, group_url)
        except StaleElementReferenceException:
            logger.debug("[SEL%s] stale post element at index=%d", log_prefix, idx)
            continue
        except Exception as exc:
            logger.debug("[SEL%s] extract failed at index=%d: %s", log_prefix, idx, exc)
            continue

        if not item:
            continue

        identity = build_post_identity(item)
        if not identity or identity in seen_ids or identity in written_this_round:
            continue

        fresh.append(item)
        written_this_round.add(identity)

    if not fresh:
        return 0

    append_ndjson(fresh, out_path)
    seen_ids.update(written_this_round)
    logger.info("[SEL%s] wrote %d fresh posts", log_prefix, len(fresh))
    return len(fresh)


# ---------------------------------------------------------------------------
# Extract best post (preferred_photo_id)
# ---------------------------------------------------------------------------

def extract_best_selector_post(
    driver,
    group_url: str,
    preferred_photo_id: str | None = None,
    *,
    require_preferred_match: bool = False,
) -> Optional[Dict[str, Any]]:
    fallback_item: Optional[Dict[str, Any]] = None

    posts = collect_visible_selector_posts(driver, group_url)
    for post in posts:
        expand_post_content_until_done(driver, post, group_url)

    logger.info(f"[DEBUG] elements count: {len(posts)}")

    for idx, post_element in enumerate(posts):
        try:
            context = _extract_selector_post_context(driver, post_element, group_url)
        except StaleElementReferenceException:
            continue
        except Exception as exc:
            logger.debug("[SEL] _extract_selector_post_context failed: %s", exc)
            continue

        logger.info(f"[DEBUG] idx={idx}")
        logger.info(f"[DEBUG] author={context.get('item') and context['item'].get('author')}")
        logger.info(f"[DEBUG] photo_fbid={context.get('photo_fbid')}")
        logger.info(f"[DEBUG] link={context.get('link')}")
        logger.info(f"[DEBUG] item is None: {context.get('item') is None}")

        _dump_post_debug(driver, post_element, context, idx)

        item = context.get("item")
        logger.info("item: %s", item)
        if not item:
            continue

        photo_fbid = (context.get("photo_fbid") or "").strip()
        if preferred_photo_id and photo_fbid == preferred_photo_id:
            return item

        if fallback_item is None:
            fallback_item = item

    if preferred_photo_id and require_preferred_match:
        return None

    return fallback_item
