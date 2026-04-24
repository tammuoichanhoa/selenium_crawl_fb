from selenium.webdriver.common.by import By
import re
from urllib.parse import parse_qs, urlparse
from typing import Any, Dict, List, Optional, Set

def _extract_comment_ids_from_href(href: str) -> dict:
    try:
        parsed = urlparse(href or "")
        params = parse_qs(parsed.query or "")
    except Exception:
        return {"comment_id": None, "reply_comment_id": None}

    return {
        "comment_id": (params.get("comment_id") or [None])[0],
        "reply_comment_id": (params.get("reply_comment_id") or [None])[0],
    }


def _parse_engagement_count(raw_value: Any) -> int:
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

def save_article_el(article_el, filepath: str = "article_debug.html"):
    """Lưu outerHTML của article_el ra file HTML."""
    try:
        html = article_el.get_attribute("outerHTML") or ""
        with open(filepath, "w", encoding="utf-8") as f:
            # Wrap trong html/body để browser render đúng
            f.write(f"""<!DOCTYPE html>
                            <html lang="vi">
                            <head>
                                <meta charset="UTF-8">
                                <title>Article Debug</title>
                            </head>
                            <body>
                            {html}
                            </body>
                        </html>""")
        print(f"[DEBUG] Saved article_el → {filepath}")
    except Exception as e:
        print(f"[DEBUG] Failed to save article_el: {e}")

def _extract_comment_detail_from_article(article_el) -> dict:
    result = {
        "nickname": None,
        "author_url": None,
        "comment": None,
        "time": None,
        "permalink": None,
        "comment_id": None,
        "reply_comment_id": None,
        "react_count": 0,
    }

    save_article_el(article_el)

    # =========================
    # permalink + ids + time
    # =========================
    try:
        link_el = article_el.find_element(By.CSS_SELECTOR, "a[href*='comment_id=']")
        href = (link_el.get_attribute("href") or "").strip()

        ids = _extract_comment_ids_from_href(href)
        result.update(ids)

        result["permalink"] = href or None
        result["time"] = (link_el.text or "").strip() or None
    except Exception:
        pass

    # =========================
    # author (FIX CHUẨN)
    # =========================
    try:
        # lấy đúng link chứa text (tránh avatar)
        author_link = article_el.find_element(
            By.XPATH,
            ".//a[contains(@href,'/user/') and .//span[@dir='auto']]"
        )

        result["author_url"] = (author_link.get_attribute("href") or "").strip() or None

        name_el = author_link.find_element(By.XPATH, "//span[@dir='auto']")
        name = (name_el.text or "").strip()

        if name:
            result["nickname"] = name

    except Exception:
        # fallback từ aria-label
        try:
            aria = (article_el.get_attribute("aria-label") or "").strip()
            m = re.search(r"tên\s+(.+?)\s+vào\s+", aria, flags=re.IGNORECASE)
            if m:
                result["nickname"] = m.group(1).strip()
        except Exception:
            pass

    # =========================
    # content (FIX selector + lọc noise)
    # =========================
    try:
        # chỉ lấy node sâu nhất (tránh duplicate text)
        parts = article_el.find_elements(
            By.CSS_SELECTOR,
            "div[dir='auto']"
        )

        texts = []
        for p in parts:
            t = (p.text or "").strip()
            if t:
                texts.append(t)

        result["comment"] = texts

    except Exception:
        pass

    # =========================
    # reactions
    # =========================
    try:
        react_btn = article_el.find_element(
            By.CSS_SELECTOR,
            "[role='button'][aria-label*='cảm xúc']"
        )

        label = (react_btn.get_attribute("aria-label") or "").strip()

        if label:
            result["react_count"] = _parse_engagement_count(label)
        else:
            result["react_count"] = _parse_engagement_count(react_btn.text or "")

    except Exception:
        result["react_count"] = 0

    return result

from selenium import webdriver
from selenium.webdriver.common.by import By

# Khởi tạo driver
driver = webdriver.Chrome()

# Load file local
driver.get("file:///home/baoanh/Desktop/fb_crawler/selenium_crawl_fb/article_debug.html")
# Lấy lại article_el
# article_el = driver.find_element(By.CSS_SELECTOR, "article")  # hoặc selector phù hợp
article_el = driver.find_element(By.TAG_NAME, "body")
# Test hàm
result = _extract_comment_detail_from_article(article_el)
print(result)