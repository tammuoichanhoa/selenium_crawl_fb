"""
url_text_utils.py
-----------------
Các hàm tiện ích xử lý URL và chuẩn hoá văn bản:
  - parse / clean Facebook URL
  - extract post_id / comment_id từ href
  - parse số lượng engagement (k/m/b)
  - normalize comment/button text
"""

import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def extract_url_digits(url: str) -> Optional[str]:
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


def clean_fb_link(url: str) -> str:
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


def extract_comment_ids_from_href(href: str) -> dict:
    try:
        parsed = urlparse(href or "")
        params = parse_qs(parsed.query or "")
    except Exception:
        return {"comment_id": None, "reply_comment_id": None}

    return {
        "comment_id": (params.get("comment_id") or [None])[0],
        "reply_comment_id": (params.get("reply_comment_id") or [None])[0],
    }


def extract_post_id_from_link(link: str) -> Optional[str]:
    try:
        parsed = urlparse(link or "")
    except Exception:
        return None

    path = parsed.path or ""
    for pattern in (r"/posts/(\d+)", r"/permalink/(\d+)"):
        m = re.search(pattern, path)
        if m:
            return m.group(1)

    try:
        params = parse_qs(parsed.query or "")
    except Exception:
        params = {}

    for key in ("story_fbid", "fbid", "post_id"):
        value = (params.get(key) or [None])[0]
        if value:
            return str(value)

    return None


def get_author_id(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        parsed = urlparse(url)
        m = re.search(r"/user/(\d+)", parsed.path or "")
        if m:
            return m.group(1)
        qs = parse_qs(parsed.query)
        return qs.get("id", [None])[0]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Engagement count
# ---------------------------------------------------------------------------

def parse_engagement_count(raw_value: Any) -> int:
    if raw_value is None:
        return 0
    text = str(raw_value).strip()
    if not text:
        return 0
    normalized = text.lower().replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)\s*([kmb])?", normalized)
    if not match:
        digits = re.sub(r"\D", "", normalized)
        return int(digits) if digits else 0
    number = float(match.group(1))
    suffix = match.group(2)
    multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1)
    return int(number * multiplier)


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

def normalize_text(text: Optional[str]) -> str:
    """Dùng chung cho button label matching."""
    return " ".join((text or "").split()).strip().lower()


def normalize_comment_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    if lowered in {
        "thích", "trả lời", "chia sẻ", "phù hợp nhất",
        "mới nhất", "most relevant", "newest", "reply", "like", "share",
    }:
        return ""
    if re.fullmatch(r"\d+", cleaned):
        return ""
    return cleaned
