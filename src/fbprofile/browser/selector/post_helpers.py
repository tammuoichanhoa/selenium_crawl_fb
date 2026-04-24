"""
post_helpers.py
---------------
Các hàm trợ giúp cấp post: tìm content root, extract timestamp/author/content/images/videos,
build post identity. Tách riêng để expand_prune.py có thể import mà không bị circular.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from logs.loging_config import logger
from ...utils import _norm_link
from .element_helpers import (
    collect_texts, safe_attr, safe_find_elements, safe_find_first, safe_text,
)
from .selector_utils import post_selector
from .url_text_utils import (
    clean_fb_link, extract_url_digits,
    get_author_id, normalize_comment_text, parse_engagement_count,
)


# ---------------------------------------------------------------------------
# Content root detection
# ---------------------------------------------------------------------------

def find_post_content_roots(post_element, source_url: str):
    selectors = [
        "[data-ad-comet-preview='message']",
        "div[data-ad-preview='message']",
        "ol[class*='html-ol']",
        "div[dir='auto'][style*='text-align']",
    ]
    roots = safe_find_elements(post_element, selectors)
    return roots if roots else [post_element]


# ---------------------------------------------------------------------------
# Timestamp & post_id
# ---------------------------------------------------------------------------

def get_post_timestamp_and_id(driver, post_element, source_url: str) -> dict:
    result = {
        "timestamp_text": "",
        "timestamp_url": "",
        "post_id": "",
        "post_id_raw": "",
    }
    wait = WebDriverWait(driver, 0.5)
    try:
        actions = ActionChains(driver)
        links = post_element.find_elements(By.CSS_SELECTOR, "[role='link']")

        for el in links:
            try:
                actions.move_to_element(el).perform()
                wait.until(lambda d: el.get_attribute("href") is not None)

                href = el.get_attribute("href")
                if not href:
                    continue

                if "facebook.com/groups" in href and "/posts/" in href:
                    result["timestamp_url"] = href

                    timestamp_text = (
                        el.get_attribute("aria-label")
                        or el.get_attribute("title")
                        or ""
                    )
                    if not timestamp_text:
                        try:
                            abbr = el.find_element(By.TAG_NAME, "abbr")
                            timestamp_text = abbr.get_attribute("title") or abbr.text
                        except Exception:
                            pass
                    if not timestamp_text:
                        try:
                            tooltip = driver.find_element(By.CSS_SELECTOR, "[role='tooltip']")
                            timestamp_text = tooltip.text
                        except Exception:
                            pass

                    result["timestamp_text"] = timestamp_text.strip()

                    parts = href.split("/")
                    if "posts" in parts:
                        idx = parts.index("posts")
                        if idx + 1 < len(parts):
                            post_id = parts[idx + 1].split("?")[0]
                            result["post_id"] = post_id
                            result["post_id_raw"] = post_id
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"Error extracting timestamp: {e}")

    return result


# ---------------------------------------------------------------------------
# Author
# ---------------------------------------------------------------------------

def extract_author(post_element, source_url: str) -> Dict[str, str]:
    author_name = safe_text(
        post_element,
        [
            "h2 a[role='link'] span[dir='auto']",
            "h2 a b span",
            "strong span[dir='auto']",
            "a[role='link'] h3 span[dir='auto']",
        ],
        selector_name=post_selector("author_name", source_url),
    )
    author_url = safe_attr(
        post_element,
        [
            "h2 a[href*='/user/']",
            "a[aria-label][href*='facebook.com/profile.php']",
        ],
        "href",
        selector_name=post_selector("author_url", source_url),
    )
    author_id = get_author_id(author_url)

    avatar = ""
    avatar_el = safe_find_first(
        post_element,
        [
            ".xjp7ctv > [type='nested/pressable'] > .x1i10hfl",
            "object > a:nth-child(1)",
            "a[aria-label] image",
        ],
        selector_name=post_selector("author_avatar", source_url),
    )
    if avatar_el is not None:
        for attr in ("xlink:href", "href", "src"):
            try:
                avatar = avatar_el.get_attribute(attr) or ""
            except Exception:
                avatar = ""
            if avatar:
                break

    return {"name": author_name, "url": author_url, "avatar": avatar, "id": author_id}


# ---------------------------------------------------------------------------
# Engagement counts
# ---------------------------------------------------------------------------

def extract_comment_count(post_element, source_url: str) -> int:
    raw = safe_text(
        post_element,
        ["span.xkrqix3.x1sur9pj", "div[aria-label*='bình luận' i]", "div[aria-label*='comment' i]"],
        selector_name=post_selector("comment", source_url),
    )
    return parse_engagement_count(raw)


def extract_share_count(post_element, source_url: str) -> int:
    raw = safe_text(
        post_element,
        ["span.xkrqix3.x1sur9pj", "div[aria-label*='chia sẻ' i]", "div[aria-label*='share' i]"],
        selector_name=post_selector("share", source_url),
    )
    return parse_engagement_count(raw)


def extract_engagement_counts(post_element, source_url: str) -> tuple[Dict[str, int], Dict[str, int]]:
    engagement = {
        "comment": extract_comment_count(post_element, source_url),
        "share": extract_share_count(post_element, source_url),
    }
    reactions = {k: 0 for k in ("like", "love", "haha", "wow", "sad", "angry", "care")}
    return engagement, reactions


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------

def extract_content(driver, post_element, source_url: str) -> str:
    from .expand_prune import expand_post_content_until_done  # avoid circular at module level
    try:
        expand_post_content_until_done(driver, post_element, source_url)
    except Exception:
        pass

    texts = collect_texts(
        post_element,
        ["ol[class*='html-ol'] li div span"],
        selector_name=post_selector("content", source_url),
    )
    content = "\n".join(texts).strip()
    if content:
        return re.sub(r"\n{3,}", "\n\n", content).strip()

    try:
        els = WebDriverWait(post_element, 3).until(
            lambda root: root.find_elements(By.CSS_SELECTOR, ".xyinxu5 > .x193iq5w")
        )
        content = "\n".join(el.text for el in els if el.text.strip())
        return re.sub(r"\n{3,}", "\n\n", content).strip()
    except TimeoutException:
        return ""


# ---------------------------------------------------------------------------
# Images / Videos / Visibility
# ---------------------------------------------------------------------------

def extract_images(post_element, source_url: str) -> List[str]:
    images: List[str] = []
    for img in safe_find_elements(
        post_element,
        [
            "[data-visualcompletion='media-vc-image']",
            "img[data-visualcompletion='media-vc-image']",
            "img[data-imgperflogname='feedCoverPhoto']",
            "img[referrerpolicy]",
        ],
        selector_name=post_selector("image", source_url),
    ):
        try:
            src = img.get_attribute("src") or img.get_attribute("currentSrc") or img.get_attribute("data-src") or ""
        except Exception:
            continue
        if src and "fbcdn" in src and src not in images:
            images.append(src)
    return images


def extract_videos(post_element, source_url: str) -> List[str]:
    videos: List[str] = []
    for video in safe_find_elements(post_element, ["video"], selector_name=post_selector("video", source_url)):
        try:
            src = video.get_attribute("src") or video.get_attribute("poster") or ""
        except Exception:
            continue
        if src and src not in videos:
            videos.append(src)
    return videos


def extract_visibility(post_element, source_url: str) -> str:
    return safe_text(
        post_element,
        [
            "span.xzpqnlu.x179tack",
            "div[aria-label*='Công khai'] span",
            "div[aria-label*='Public'] span",
            ".xs7f9wi",
        ],
        selector_name=post_selector("visibility", source_url),
    )


def extract_post_link(post_element, source_url: str) -> str:
    candidates = safe_find_elements(
        post_element,
        [
            "a[href*='/posts/']", "a[href*='/permalink/']",
            "a[href*='story_fbid=']", "a[href*='/reel/']",
            "a[role='link'][href*='facebook.com']",
        ],
        selector_name=post_selector("link", source_url),
    )
    for el in candidates:
        try:
            href = clean_fb_link(el.get_attribute("href") or "")
            if _norm_link(href) or extract_url_digits(href):
                return href
        except Exception:
            continue
    return ""


# ---------------------------------------------------------------------------
# Source / identity helpers
# ---------------------------------------------------------------------------

def extract_source_id(group_url: str) -> Optional[str]:
    try:
        m = re.search(r"/groups/([^/?#]+)", group_url or "")
        if m:
            return m.group(1)
    except Exception:
        return None
    return None


def build_post_identity(item: Dict[str, Any]) -> Optional[str]:
    for candidate in (
        item.get("rid"),
        item.get("id"),
        extract_url_digits(item.get("link") or ""),
        _norm_link(item.get("link") or ""),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    author = (item.get("author") or "").strip()
    content = (item.get("content") or "").strip()
    if author or content:
        return f"{author}|{content[:80]}"
    return None
