import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import parse_qs, urlparse

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By

from logs.loging_config import logger
from src.utils.selectors import resolve_locator, validate_selector_payload
from ..storage.ndjson import append_ndjson
from ..utils import _norm_link


PROFILE_SELECTOR_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "configs" / "modules" / "profile.json"
)

POST_CONTAINER_SELECTORS = (
    "div[data-pagelet*='FeedUnit']",
    "div[role='article']",
    "div[aria-posinset]",
)


@lru_cache(maxsize=1)
def _load_profile_selector_config() -> dict:
    with open(PROFILE_SELECTOR_CONFIG_PATH, "r", encoding="utf-8") as config_file:
        return validate_selector_payload(json.load(config_file))


def _build_locator_chain(selector_name: str) -> List[Dict[str, Any]]:
    selector_cfg = _load_profile_selector_config().get("elements", {}).get(selector_name)
    if not isinstance(selector_cfg, dict):
        return []

    locators: List[Dict[str, Any]] = []
    primary = selector_cfg.get("primary")
    if isinstance(primary, dict):
        locators.append(dict(primary))

    fallbacks = selector_cfg.get("fallbacks")
    if isinstance(fallbacks, list):
        locators.extend(dict(fallback) for fallback in fallbacks if isinstance(fallback, dict))

    return locators


def _safe_text(root, selectors: List[str], selector_name: str | None = None) -> str:
    if selector_name:
        for locator in _build_locator_chain(selector_name):
            try:
                by, value = resolve_locator(locator)
                element = root.find_element(by, value)
                text = (element.text or "").strip()
                if text:
                    return text
            except Exception:
                continue

    for selector in selectors:
        try:
            element = root.find_element(By.CSS_SELECTOR, selector)
            text = (element.text or "").strip()
            if text:
                return text
        except Exception:
            continue
    return ""


def _safe_attr(root, selectors: List[str], attr: str, selector_name: str | None = None) -> str:
    if selector_name:
        for locator in _build_locator_chain(selector_name):
            try:
                by, value = resolve_locator(locator)
                element = root.find_element(by, value)
                value = element.get_attribute(attr)
                value = value.strip() if isinstance(value, str) else value
                if value:
                    return value
            except Exception:
                continue

    for selector in selectors:
        try:
            element = root.find_element(By.CSS_SELECTOR, selector)
            value = element.get_attribute(attr)
            value = value.strip() if isinstance(value, str) else value
            if value:
                return value
        except Exception:
            continue
    return ""


def _safe_find_elements(root, selectors: List[str], selector_name: str | None = None):
    if selector_name:
        for locator in _build_locator_chain(selector_name):
            try:
                by, value = resolve_locator(locator)
                elements = root.find_elements(by, value)
                if elements:
                    return elements
            except Exception:
                continue

    for selector in selectors:
        try:
            elements = root.find_elements(By.CSS_SELECTOR, selector)
            if elements:
                return elements
        except Exception:
            continue
    return []


def _safe_find_first(root, selectors: List[str], selector_name: str | None = None):
    if selector_name:
        for locator in _build_locator_chain(selector_name):
            try:
                by, value = resolve_locator(locator)
                return root.find_element(by, value)
            except Exception:
                continue

    for selector in selectors:
        try:
            return root.find_element(By.CSS_SELECTOR, selector)
        except Exception:
            continue
    return None


def _extract_url_digits(url: str) -> Optional[str]:
    if not url or not isinstance(url, str):
        return None
    try:
        path = urlparse(url).path or ""
    except Exception:
        return None

    match = re.search(r"/(?:posts|permalink|reel)/(\d+)", path.lower())
    if match:
        return match.group(1)

    return None


def _clean_fb_link(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query or "")
        story_fbid = query.get("story_fbid", [None])[0]
        if story_fbid:
            return f"https://facebook.com/{parsed.path.strip('/')}/posts/{story_fbid}"
        return f"{parsed.scheme or 'https'}://{parsed.netloc}{parsed.path}".rstrip("/")
    except Exception:
        return url


def _extract_post_link(post_element) -> str:
    candidates = _safe_find_elements(
        post_element,
        [
            "a[href*='/posts/']",
            "a[href*='/permalink/']",
            "a[href*='story_fbid=']",
            "a[href*='/reel/']",
            "a[role='link'][href*='facebook.com']",
        ],
        selector_name="profile.posts.link",
    )
    for element in candidates:
        try:
            href = element.get_attribute("href") or ""
            href = _clean_fb_link(href)
            if _norm_link(href) or _extract_url_digits(href):
                return href
        except Exception:
            continue
    return ""

def get_post_timestamp_and_id(post_element) -> dict:
    """
    Extract timestamp link và post ID từ DOM Facebook.
    
    Selector hoạt động: thẻ <a> có href chứa '/posts/pfbid'
    """
    result = {
        "timestamp_text": "",
        "timestamp_url": "",
        "post_id": "",        # pfbid dạng encode
        "post_id_raw": "",    # username/posts/pfbid...
    }

    try:
        # Selector chính xác từ DOM: <a> có href chứa /posts/
        timestamp_link = _safe_find_first(
            post_element,
            ["a[href*='/posts/pfbid']"],
            selector_name="profile.posts.timestamp",
        )
        if timestamp_link is None:
            raise ValueError("timestamp link not found")

        href = (timestamp_link.get_attribute("href") or "").strip()
        clean_url = href.split("?", 1)[0]
        result["timestamp_url"] = clean_url

        # Post ID = phần sau /posts/
        # vd: .../charlotte.rosabella/posts/pfbid02dZjXQ7sc...
        if "/posts/" in clean_url:
            result["post_id"] = clean_url.split("/posts/", 1)[1]
            result["post_id_raw"] = clean_url

        # Text hiển thị (vd: "1 giờ", "Hôm qua lúc 10:00")
        result["timestamp_text"] = (timestamp_link.text or "").strip()
    except Exception:
        pass

    return result


def _extract_timestamp_post_id_and_link(post_element) -> tuple[str, str]:
    timestamp_data = get_post_timestamp_and_id(post_element)
    return (
        (timestamp_data.get("post_id") or "").strip(),
        (timestamp_data.get("timestamp_url") or "").strip(),
    )


def _extract_photo_post_id_and_link(post_element) -> tuple[str, str]:
    clean_href = ""
    photo_link = _safe_find_first(
        post_element,
        [
            "a[href*='fbid=']",
            "a[href*='/photo/']",
        ],
        selector_name="profile.posts.photo_link",
    )
    if photo_link is not None:
        try:
            photo_href = (photo_link.get_attribute("href") or "").strip()
        except Exception:
            photo_href = ""

        if photo_href:
            try:
                parsed = urlparse(photo_href)
                photo_qs = parse_qs(parsed.query or "")
            except Exception:
                parsed = None
                photo_qs = {}

            if parsed is not None:
                clean_href = f"{parsed.scheme or 'https'}://{parsed.netloc}{parsed.path}".rstrip("/")
            else:
                clean_href = photo_href.split("?", 1)[0]

            photo_fbid = photo_qs.get("fbid", [None])[0]
            if isinstance(photo_fbid, str) and photo_fbid.strip():
                return photo_fbid.strip(), clean_href

            photo_digits = _extract_url_digits(clean_href)
            if isinstance(photo_digits, str) and photo_digits.strip():
                return photo_digits.strip(), clean_href

    return "", clean_href


def _extract_author(post_element) -> Dict[str, str]:
    author_name = _safe_text(
        post_element,
        [
            "h2 a[role='link'] span[dir='auto']",
            "h2 a b span",
            "strong span[dir='auto']",
            "a[role='link'] h3 span[dir='auto']",
        ],
        selector_name="profile.posts.author_name",
    )
    author_url = _safe_attr(
        post_element,
        [
            "h2 a[href*='facebook.com']",
            "a[role='link'][href*='facebook.com'][aria-label]",
            "strong a[href*='facebook.com']",
        ],
        "href",
        selector_name="profile.posts.author_url",
    )
    author_url = _clean_fb_link(author_url)

    avatar = ""
    avatar_el = _safe_find_first(
        post_element,
        [
            "a[aria-label] image",
            "svg[role='img'] image",
            "image",
            "img[referrerpolicy]",
        ],
        selector_name="profile.posts.author_avatar",
    )
    if avatar_el is not None:
        for attr in ("xlink:href", "href", "src"):
            try:
                avatar = avatar_el.get_attribute(attr) or ""
            except Exception:
                avatar = ""
            if avatar:
                break

    return {"name": author_name, "url": author_url, "avatar": avatar}


def _extract_content(post_element) -> str:
    content = _safe_text(
        post_element,
        [
            "[data-ad-comet-preview='message'] [dir='auto']",
            "div[data-ad-preview='message'] [dir='auto']",
            "div[dir='auto'][style*='text-align']",
        ],
        selector_name="profile.posts.content",
    )
    return re.sub(r"\n{3,}", "\n\n", content).strip()


def _extract_images(post_element) -> List[str]:
    images: List[str] = []
    for img in _safe_find_elements(
        post_element,
        [
            "a[href*='photo'] img",
            "img[data-imgperflogname='feedCoverPhoto']",
            "img[data-visualcompletion='media-vc-image']",
            "img[alt='']",
        ],
        selector_name="profile.posts.image",
    ):
        try:
            src = img.get_attribute("src") or ""
        except Exception:
            continue
        if not src or "fbcdn" not in src:
            continue
        if src not in images:
            images.append(src)
    return images


def _extract_videos(post_element) -> List[str]:
    videos: List[str] = []
    for video in _safe_find_elements(post_element, ["video"], selector_name="profile.posts.video"):
        try:
            src = video.get_attribute("src") or video.get_attribute("poster") or ""
        except Exception:
            continue
        if src and src not in videos:
            videos.append(src)
    return videos


def _extract_timestamp_text(post_element) -> str:
    return _safe_text(
        post_element,
        [
            "a[href*='/posts/'] span",
            "a[href*='/permalink/'] span",
            "a[href*='story_fbid='] span",
            "abbr",
            "span[id*='timestamp']",
        ],
        selector_name="profile.posts.timestamp_text",
    )


def _extract_visibility(post_element) -> str:
    return _safe_text(
        post_element,
        [
            "span.xzpqnlu.x179tack",
            "div[aria-label*='Công khai'] span",
            "div[aria-label*='Public'] span",
        ],
        selector_name="profile.posts.visibility",
    )


def _extract_link_preview(post_element) -> Dict[str, str]:
    result = {"domain": "", "title": "", "url": ""}
    result["domain"] = _safe_text(
        post_element,
        [
            "[data-ad-rendering-role='meta'] span",
            "a[rel='nofollow noreferrer'] span[dir='auto']",
        ],
        selector_name="profile.posts.link_preview.domain",
    )
    result["title"] = _safe_text(
        post_element,
        [
            "[data-ad-rendering-role='title'] span",
            "a[rel='nofollow noreferrer'] h3 span",
            "a[rel='nofollow noreferrer'] span[dir='auto']",
        ],
        selector_name="profile.posts.link_preview.title",
    )
    result["url"] = _safe_attr(
        post_element,
        [
            "a[rel='nofollow noreferrer']",
            "a[target='_blank'][href^='http']",
        ],
        "href",
        selector_name="profile.posts.link_preview.url",
    )
    return result


def _extract_source_id(group_url: str) -> Optional[str]:
    try:
        match = re.search(r"/groups/([^/?#]+)", group_url or "")
        if match:
            return match.group(1)
    except Exception:
        return None
    return None


def _build_post_identity(item: Dict[str, Any]) -> Optional[str]:
    for candidate in (
        item.get("rid"),
        item.get("id"),
        _extract_url_digits(item.get("link") or ""),
        _norm_link(item.get("link") or ""),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    author = (item.get("author") or "").strip()
    content = (item.get("content") or "").strip()
    if author or content:
        return f"{author}|{content[:80]}"
    return None


def extract_selector_post(post_element, group_url: str) -> Optional[Dict[str, Any]]:
    author = _extract_author(post_element)
    content = _extract_content(post_element)
    timestamp_post_id, timestamp_link = _extract_timestamp_post_id_and_link(post_element)
    photo_fid, photo_link = _extract_photo_post_id_and_link(post_element)
    link = timestamp_link or _extract_post_link(post_element) or photo_link
    link_preview = _extract_link_preview(post_element)
    image_urls = _extract_images(post_element)
    video_urls = _extract_videos(post_element)
    timestamp_text = _extract_timestamp_text(post_element)
    visibility = _extract_visibility(post_element)

    rid = timestamp_post_id or photo_fid or _extract_url_digits(link) or ""
    fb_id = rid
    item = {
        "id": fb_id,
        "rid": rid,
        "type": "story",
        "link": link,
        "author_id": None,
        "author": author["name"],
        "author_link": author["url"],
        "avatar": author["avatar"],
        "created_time": None,
        "timestamp_text": timestamp_text,
        "content": content,
        "image_url": image_urls,
        "like": 0,
        "comment": 0,
        "haha": 0,
        "wow": 0,
        "sad": 0,
        "love": 0,
        "angry": 0,
        "care": 0,
        "share": 0,
        "hashtag": re.findall(r"#\w+", content or ""),
        "video": video_urls,
        "source_id": _extract_source_id(group_url),
        "is_share": bool(link_preview["url"]),
        "link_share": link_preview["url"] or None,
        "type_share": "link" if link_preview["url"] else None,
        "origin_id": None,
        "out_links": [link_preview["url"]] if link_preview["url"] else [],
        "out_domains": [link_preview["domain"]] if link_preview["domain"] else [],
        "visibility": visibility,
        "link_title": link_preview["title"],
        "link_url": link_preview["url"],
        "link_domain": link_preview["domain"],
    }

    identity = _build_post_identity(item)
    if not identity:
        return None

    if not item["id"]:
        item["id"] = identity
    if not item["rid"]:
        item["rid"] = identity

    return item


def collect_visible_selector_posts(driver):
    seen = []
    unique_ids = set()
    elements = _safe_find_elements(driver, list(POST_CONTAINER_SELECTORS), selector_name="profile.posts.container")
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

    if seen:
        return seen

    for selector in POST_CONTAINER_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            continue
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

    for idx, post_element in enumerate(collect_visible_selector_posts(driver)):
        try:
            item = extract_selector_post(post_element, group_url)
        except StaleElementReferenceException:
            logger.debug("[SEL%s] stale post element at index=%d", log_prefix, idx)
            continue
        except Exception as exc:
            logger.debug("[SEL%s] extract failed at index=%d: %s", log_prefix, idx, exc)
            continue

        if not item:
            continue

        identity = _build_post_identity(item)
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
