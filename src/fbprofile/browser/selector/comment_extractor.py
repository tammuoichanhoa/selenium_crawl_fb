"""
comment_extractor.py
--------------------
Thu thập comment từ một post đã mở (parse_comment) và
trích xuất chi tiết từng article comment (_extract_comment_detail_from_article).
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By

from logs.loging_config import logger
from .element_helpers import read_element_text
from .expand_prune import (
    EXPAND_VIEWPORT_MARGIN_PX,
    EXPAND_MAX_CLICKS_PER_ROUND,
    KEEP_ANCHORS,
    PRUNE_BUFFER,
    PRUNE_MAX_ARTICLES_PER_ROUND,
    expand_comments_near_viewport,
    prune_processed_comment_articles,
    _closest_comment_article,
)
from .scroll_context import find_comment_context_root, scroll_comment_context
from .url_text_utils import (
    extract_comment_ids_from_href,
    extract_post_id_from_link,
    normalize_comment_text,
    parse_engagement_count,
)
from .expand_prune import select_all_comments_filter


COMMENT_DEBUG_OUTPUT_PATH = Path(__file__).resolve().parents[1] / "logs" / "comment_selector_debug.txt"


# ---------------------------------------------------------------------------
# Debug helpers
# ---------------------------------------------------------------------------

def append_comment_debug_output(source_url: str, collected_texts: List[str]) -> None:
    try:
        COMMENT_DEBUG_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(COMMENT_DEBUG_OUTPUT_PATH, "a", encoding="utf-8") as f:
            f.write(
                f"[{datetime.now().isoformat()}] source_url={source_url}\n"
                f">>>>>>>>>>>>>>>>> {json.dumps(collected_texts, ensure_ascii=False)}\n"
            )
    except Exception as exc:
        logger.debug("[SEL] failed writing comment debug output: %s", exc)


def save_article_el(article_el, filepath: str = "article_debug.html") -> None:
    try:
        html = article_el.get_attribute("outerHTML") or ""
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"""<!DOCTYPE html>
<html lang="vi">
<head><meta charset="UTF-8"><title>Article Debug</title></head>
<body>
{html}
</body>
</html>""")
        print(f"[DEBUG] Saved article_el → {filepath}")
    except Exception as e:
        print(f"[DEBUG] Failed to save article_el: {e}")


# ---------------------------------------------------------------------------
# Extract detail from a single comment article
# ---------------------------------------------------------------------------

def extract_comment_detail_from_article(article_el) -> dict:
    result: Dict[str, Any] = {
        "nickname": None,
        "author_url": None,
        "comment": None,
        "time": None,
        "permalink": None,
        "comment_id": None,
        "reply_comment_id": None,
        "react_count": 0,
    }

    try:
        save_article_el(article_el)
    except Exception:
        pass

    # --- permalink + ids + time ---
    try:
        link_el = article_el.find_element(By.CSS_SELECTOR, "a[href*='comment_id=']")
        href = (link_el.get_attribute("href") or "").strip()
        result.update(extract_comment_ids_from_href(href))
        result["permalink"] = href or None
        result["time"] = (link_el.text or "").strip() or None
    except Exception:
        pass

    # --- author ---
    try:
        author_link = article_el.find_element(
            By.XPATH,
            ".//a[contains(@href,'/user/') or contains(@href,'profile.php') or contains(@href,'/people/')]",
        )
        href = (author_link.get_attribute("href") or "").strip()
        if href and href != "#":
            result["author_url"] = href
    except Exception:
        for pattern in [
            r"tên\s+(.+?)\s+vào\s+",
            r"(?:dưới\s+tên|under\s+the\s+name)\s+(.+?)\s+(?:vào|at)\s+",
        ]:
            try:
                aria = (article_el.get_attribute("aria-label") or "").strip()
                m = re.search(pattern, aria, flags=re.IGNORECASE)
                if m:
                    result["nickname"] = m.group(1).strip()
                    break
            except Exception:
                pass

    # --- comment content ---
    try:
        candidates = article_el.find_elements(By.CSS_SELECTOR, "div[dir='auto'], span[dir='auto']")
        seen_texts: Set[str] = set()
        texts: List[str] = []
        nickname = (result.get("nickname") or "").strip()
        time_text = (result.get("time") or "").strip()

        for node in candidates:
            raw = (node.text or "").strip()
            normalized = normalize_comment_text(raw)
            if not normalized or normalized == nickname or normalized == time_text:
                continue
            if normalized in seen_texts:
                continue
            seen_texts.add(normalized)
            texts.append(normalized)

        if texts:
            ordered = sorted(texts, key=len, reverse=True)
            filtered: List[str] = []
            for t in ordered:
                if any(t != kept and t in kept for kept in filtered):
                    continue
                filtered.append(t)
            filtered_set = set(filtered)
            result["comment"] = [t for t in texts if t in filtered_set] or None
        else:
            result["comment"] = None
    except Exception:
        pass

    # --- reactions ---
    try:
        possible_counts: List[int] = []
        for sel in (
            "[aria-label*='cảm xúc']", "[aria-label*='Bày tỏ cảm xúc']",
            "[aria-label*='reaction']", "[aria-label*='React']",
        ):
            try:
                els = article_el.find_elements(By.CSS_SELECTOR, sel)
            except Exception:
                els = []
            for e in els:
                for val in (e.get_attribute("aria-label"), e.text):
                    if val:
                        possible_counts.append(parse_engagement_count(val.strip()))

        if not possible_counts:
            try:
                els = article_el.find_elements(By.CSS_SELECTOR, "[role='button'], a")
            except Exception:
                els = []
            for e in els:
                for val in (e.get_attribute("aria-label"), e.text):
                    if val:
                        possible_counts.append(parse_engagement_count(val.strip()))

        result["react_count"] = max([c for c in possible_counts if isinstance(c, int)], default=0)
    except Exception:
        result["react_count"] = 0

    return result


# ---------------------------------------------------------------------------
# parse_comment — phase 2 main loop
# ---------------------------------------------------------------------------

import time as _time


def parse_comment(driver, post_link: str, source_url: str) -> List[dict]:
    """
    Phase 2: mở `post_link`, bung/scroll và thu thập comment chi tiết.
    Trả về list[dict] (mỗi dict có nickname, comment, time, react_count, ...).
    """
    if not post_link:
        return []

    driver.get(post_link)
    logger.info(f"Redirect to {post_link}...")
    post_id = extract_post_id_from_link(post_link)

    seen: Set[str] = set()
    collected: List[dict] = []
    stable_rounds = 0
    clicked_fingerprints: Set[str] = set()

    for _ in range(80):
        try:
            root = find_comment_context_root(driver)
        except Exception:
            root = None

        try:
            if root is not None:
                select_all_comments_filter(driver, root=root, timeout=2.0)
        except Exception:
            pass

        try:
            if root is not None:
                expand_comments_near_viewport(
                    driver, root, clicked_fingerprints,
                    margin_px=EXPAND_VIEWPORT_MARGIN_PX,
                    max_clicks=EXPAND_MAX_CLICKS_PER_ROUND,
                    pause_after_click=0.35,
                )
        except Exception:
            pass

        new_this_round = 0
        try:
            anchors = (root or driver).find_elements(By.CSS_SELECTOR, "a[href*='comment_id=']")
        except Exception:
            anchors = []

        for a in anchors:
            try:
                href = (a.get_attribute("href") or "").strip()
                if post_id and post_id not in href:
                    continue
                ids = extract_comment_ids_from_href(href)
                key = str(ids.get("reply_comment_id") or ids.get("comment_id") or "").strip()
                if not key or key in seen:
                    continue

                article = _closest_comment_article(driver, a)
                if article is None:
                    continue

                detail = extract_comment_detail_from_article(article)
                if not detail.get("comment_id") and ids.get("comment_id"):
                    detail["comment_id"] = ids["comment_id"]
                if not detail.get("reply_comment_id") and ids.get("reply_comment_id"):
                    detail["reply_comment_id"] = ids["reply_comment_id"]
                if not detail.get("permalink") and href:
                    detail["permalink"] = href

                seen.add(key)
                collected.append(detail)
                new_this_round += 1
            except StaleElementReferenceException:
                continue
            except Exception:
                continue

        if new_this_round == 0:
            stable_rounds += 1
        else:
            stable_rounds = 0

        if stable_rounds >= 4 and collected:
            break
        if stable_rounds >= 12:
            break

        try:
            if root is not None and seen:
                prune_processed_comment_articles(
                    driver, root, seen,
                    keep_anchors=KEEP_ANCHORS,
                    prune_buffer=PRUNE_BUFFER,
                    max_articles=PRUNE_MAX_ARTICLES_PER_ROUND,
                    protect_margin_px=EXPAND_VIEWPORT_MARGIN_PX,
                )
        except Exception:
            pass

        try:
            scroll_comment_context(driver, root)
        except Exception:
            break
        _time.sleep(0.75)

    return collected
