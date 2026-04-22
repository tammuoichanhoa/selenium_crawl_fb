import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import parse_qs, urlparse
from time import sleep, time
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from logs.loging_config import logger
from src.utils.selectors import resolve_locator, validate_selector_payload
from ..storage.ndjson import append_ndjson
from ..utils import _norm_link


MODULE_SELECTOR_CONFIG_PATHS = {
    "profile": Path(__file__).resolve().parents[1] / "configs" / "modules" / "profile.json",
    # "group": Path(__file__).resolve().parents[1] / "configs" / "modules" / "group.json",
    "group": "/home/baoanh/Desktop/fb_crawler/selenium_crawl_fb/configs/modules/group.json"

}

POST_CONTAINER_SELECTORS = (
    "[data-name='media-viewer-nav-container']",
    "div[role='dialog'] div[role='complementary']",       # broad fallback inside photo viewer
    # "div[role='article']",  ← bỏ cái này, match nhầm comment
)
COMMENT_DEBUG_OUTPUT_PATH = Path(__file__).resolve().parents[1] / "logs" / "comment_selector_debug.txt"

# ===========================================================================
#                   LIBS  
# ===========================================================================
@lru_cache(maxsize=None)
def _load_selector_config(module_name: str) -> dict:
    config_path = MODULE_SELECTOR_CONFIG_PATHS.get(module_name) or MODULE_SELECTOR_CONFIG_PATHS["profile"]
    with open(config_path, "r", encoding="utf-8") as config_file:
        return validate_selector_payload(json.load(config_file))


def _build_locator_chain(selector_name: str) -> List[Dict[str, Any]]:
    module_name = selector_name.split(".", 1)[0] if "." in selector_name else "profile"
    selector_cfg = _load_selector_config(module_name).get("elements", {}).get(selector_name)
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


def _resolve_post_selector_namespace(source_url: str) -> str:
    if isinstance(source_url, str) and "/groups/" in source_url:
        return "group"
    return "profile"


def _post_selector(selector_suffix: str, source_url: str) -> str:
    return f"{_resolve_post_selector_namespace(source_url)}.posts.{selector_suffix}"


def _read_element_text(element) -> tuple[str, str]:
    """Đọc text của 1 `WebElement`, kèm nguồn (`text`/`textContent`/`innerText`)."""
    try:
        text = (element.text or "").strip()
    except Exception:
        text = ""
    if text:
        return re.sub(r"\s+", " ", text).strip(), "text"

    for attr in ("textContent", "innerText"):
        try:
            raw_value = element.get_attribute(attr)
        except Exception:
            raw_value = ""
        normalized = re.sub(r"\s+", " ", (raw_value or "")).strip()
        if normalized:
            return normalized, attr

    return "", ""


def _locator_debug_repr(locator: Dict[str, Any]) -> str:
    locator_type = locator.get("type") or locator.get("by") or locator.get("strategy") or "css"
    value = locator.get("value") or locator.get("selector") or ""
    return f"{locator_type}={value}"


def _safe_text(root, selectors: List[str], selector_name: str | None = None) -> str:
    if selector_name:
        for locator in _build_locator_chain(selector_name):
            try:
                by, value = resolve_locator(locator)
                element = root.find_element(by, value)
                text, text_source = _read_element_text(element)

                if text:
                    if text_source != "text":
                        logger.debug(
                            "[SEL] selector=%s locator=%s used_fallback=%s value=%r",
                            selector_name,
                            _locator_debug_repr(locator),
                            text_source,
                            text,
                        )
                    return text
                logger.debug(
                    "[SEL] selector=%s locator=%s found element but text empty",
                    selector_name,
                    _locator_debug_repr(locator),
                )
            except NoSuchElementException:
                logger.debug(
                    "[SEL] selector=%s locator=%s not found",
                    selector_name,
                    _locator_debug_repr(locator),
                )
                continue
            except Exception as exc:
                logger.debug(
                    "[SEL] selector=%s locator=%s failed: %s",
                    selector_name,
                    _locator_debug_repr(locator),
                    exc,
                )
                continue

    for selector in selectors:
        try:
            element = root.find_element(By.CSS_SELECTOR, selector)
            text, text_source = _read_element_text(element)
            if text:
                if text_source != "text":
                    logger.debug(
                        "[SEL] selector=%s fallback_css=%s used_fallback=%s value=%r",
                        selector_name or "<inline>",
                        selector,
                        text_source,
                        text,
                    )
                return text
            logger.debug(
                "[SEL] selector=%s fallback_css=%s found element but text empty",
                selector_name or "<inline>",
                selector,
            )
        except NoSuchElementException:
            logger.debug(
                "[SEL] selector=%s fallback_css=%s not found",
                selector_name or "<inline>",
                selector,
            )
            continue
        except Exception as exc:
            logger.debug(
                "[SEL] selector=%s fallback_css=%s failed: %s",
                selector_name or "<inline>",
                selector,
                exc,
            )
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
    """
    Tìm nhiều phần tử một cách “an toàn” (không throw ra ngoài).

    Ưu tiên:
    1) Nếu có `selector_name`: thử lần lượt các locator sinh từ `_build_locator_chain(selector_name)`
       (mỗi locator được `resolve_locator()` -> (by, value)). Nếu tìm thấy >=1 phần tử thì trả ngay.
    2) Nếu không thấy theo locator chain: thử lần lượt các CSS selector trong `selectors`.
       Nếu tìm thấy >=1 phần tử thì trả ngay.

    Kết quả:
    - Trả về `List[WebElement]` (có thể rỗng).
    - Nuốt mọi exception trong quá trình find để luồng crawl không bị dừng.
    """
    if selector_name:
        for locator in _build_locator_chain(selector_name):
            try:
                by, value = resolve_locator(locator)
                elements = root.find_elements(by, value)
                # logger.info("Len>>>>>> ", len(elements))
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


def _keep_outermost_elements(driver, elements):
    """
    Giữ lại các node ngoài cùng trong tập match.

    Selector broad ở photo viewer có thể trả về nhiều wrapper/complementary lồng nhau.
    Khi đó chỉ nên giữ ancestor ngoài cùng để tránh coi pane comment con như post container độc lập.
    """
    filtered = []

    for element in elements:
        try:
            is_nested = any(
                bool(
                    driver.execute_script(
                        "return arguments[0] !== arguments[1] && arguments[0].contains(arguments[1]);",
                        existing,
                        element,
                    )
                )
                for existing in filtered
            )
            if is_nested:
                continue

            filtered = [
                existing
                for existing in filtered
                if not bool(
                    driver.execute_script(
                        "return arguments[0] !== arguments[1] && arguments[0].contains(arguments[1]);",
                        element,
                        existing,
                    )
                )
            ]
        except Exception:
            pass

        filtered.append(element)

    return filtered


def _safe_find_first(root, selectors: List[str], selector_name: str | None = None):
    """
    Tìm phần tử đầu tiên một cách “an toàn” (không throw ra ngoài).

    Ưu tiên:
    1) Nếu có `selector_name`: thử lần lượt locator từ `_build_locator_chain(selector_name)`.
       Gặp locator nào tìm thấy thì trả ngay `WebElement`.
    2) Nếu không thấy theo locator chain: thử lần lượt các CSS selector trong `selectors`.

    Kết quả:
    - Trả về `WebElement` nếu tìm được, ngược lại `None`.
    - Nuốt mọi exception trong quá trình find để luồng crawl không bị dừng.
    """
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


def _collect_texts(root, selectors: List[str], selector_name: str | None = None) -> List[str]:
    """Collect các text (không rỗng, không trùng) từ danh sách elements tìm được."""
    texts: List[str] = []
    seen: Set[str] = set()

    elements = _safe_find_elements(root, selectors, selector_name=selector_name)
    for element in elements:
        text, _ = _read_element_text(element)
        if not text or text in seen:
            continue
        seen.add(text)
        texts.append(text)
    return texts


def _append_comment_debug_output(source_url: str, collected_texts: List[str]) -> None:
    try:
        COMMENT_DEBUG_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(COMMENT_DEBUG_OUTPUT_PATH, "a", encoding="utf-8") as debug_file:
            debug_file.write(
                f"[{datetime.now().isoformat()}] source_url={source_url}\n"
                f">>>>>>>>>>>>>>>>> {json.dumps(collected_texts, ensure_ascii=False)}\n"
            )
    except Exception as exc:
        logger.debug("[SEL] failed writing comment debug output: %s", exc)


def _scroll_post_for_comments(driver, post_element, source_url: str) -> None:
    wait = WebDriverWait(driver, 2)
    comment_selector = _post_selector("comments", source_url)

    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'start', inline: 'nearest'});",
            post_element,
        )
        wait.until(
            lambda _driver: bool(
                _safe_find_elements(post_element, [], selector_name=comment_selector)
            )
            or abs(
                int(
                    _driver.execute_script(
                        "return Math.round(arguments[0].getBoundingClientRect().top || 0);",
                        post_element,
                    )
                    or 0
                )
            )
            < 400
        )
    except Exception:
        return

    last_height = -1
    for _ in range(5):
        try:
            current_height = int(
                driver.execute_script(
                    """
                    const rect = arguments[0].getBoundingClientRect();
                    return Math.max(rect.height || 0, arguments[0].scrollHeight || 0);
                    """,
                    post_element,
                )
                or 0
            )
        except Exception:
            current_height = 0

        if current_height <= 0 or current_height == last_height:
            break
        last_height = current_height

        try:
            driver.execute_script(
                """
                const rect = arguments[0].getBoundingClientRect();
                const targetY = window.scrollY + rect.top + Math.min(rect.height * 0.75, 1200);
                window.scrollTo({top: targetY, behavior: 'instant'});
                """,
                post_element,
            )
        except Exception:
            break

        try:
            wait.until(
                lambda _driver, previous_height=last_height: bool(
                    _safe_find_elements(post_element, [], selector_name=comment_selector)
                )
                or int(
                    _driver.execute_script(
                        """
                        const rect = arguments[0].getBoundingClientRect();
                        return Math.max(rect.height || 0, arguments[0].scrollHeight || 0);
                        """,
                        post_element,
                    )
                    or 0
                )
                != previous_height
            )
        except TimeoutException:
            pass

        if _safe_find_elements(post_element, [], selector_name=comment_selector):
            break



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

# ===========================================================================
#                   EXTRACT INFO BLOCKS 
# ===========================================================================

def _extract_comments(driver, post_element, source_url: str) -> List[str]:
    comments: List[str] = []
    seen: Set[str] = set()

    _scroll_post_for_comments(driver, post_element, source_url)

    comment_selector = _post_selector("comments", source_url)
    collected_texts = _collect_texts(post_element, [], selector_name=comment_selector)
    _append_comment_debug_output(source_url, collected_texts)
    for raw_text in collected_texts:
        normalized = _normalize_comment_text(raw_text)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        comments.append(normalized)

    return comments


def _normalize_comment_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return ""

    lowered = cleaned.lower()
    if lowered in {
        "thích",
        "trả lời",
        "chia sẻ",
        "phù hợp nhất",
        "mới nhất",
        "most relevant",
        "newest",
        "reply",
        "like",
        "share",
    }:
        return ""

    if re.fullmatch(r"\d+", cleaned):
        return ""

    return cleaned

def _closest_comment_article(driver, el):
    try:
        return driver.execute_script("return arguments[0].closest('[role=\"article\"]');", el)
    except Exception:
        return None


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

def _extract_post_id_from_link(link: str) -> str | None:
    """
    Cố gắng lấy post_id từ URL của FB để filter comment_id anchors, tránh lẫn element
    từ page phía sau (feed vẫn tồn tại trong DOM khi mở post dạng overlay/dialog).
    """
    try:
        parsed = urlparse(link or "")
    except Exception:
        return None

    path = parsed.path or ""
    m = re.search(r"/posts/(\d+)", path)
    if m:
        return m.group(1)
    m = re.search(r"/permalink/(\d+)", path)
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


def _find_comment_context_root(driver):
    """
    Facebook thường mở post/comment trong overlay (role=dialog), còn feed phía sau vẫn
    nằm trong DOM. Nếu find_elements từ driver/body sẽ dễ lẫn anchors/comment_id
    từ background. Hàm này cố gắng trả về container "đúng" để scope query.
    """
    try:
        dialogs = driver.find_elements(By.CSS_SELECTOR, "div[role='dialog']")
    except Exception:
        dialogs = []

    # ưu tiên dialog "trên cùng" và có chứa comment_id anchors
    for dialog in reversed(dialogs):
        try:
            if not dialog.is_displayed():
                continue
            if dialog.find_elements(By.CSS_SELECTOR, "a[href*='comment_id=']"):
                return dialog
        except StaleElementReferenceException:
            continue
        except Exception:
            continue

    for selector in ("div[role='main']", "body"):
        try:
            return driver.find_element(By.CSS_SELECTOR, selector)
        except Exception:
            continue
    return None


def _scroll_comment_context(driver, root) -> None:
    """
    Scroll đúng context để load thêm comment:
    - Ưu tiên scroll vào "khung post" (scrollable container) bên trong dialog/main
      vì Facebook thường dùng nested scroll container khi mở post theo overlay.
    - Fallback: scroll root nếu scroll được; cuối cùng mới scroll window.
    """
    target = _find_comment_scroll_target(driver, root)

    try:
        if target is not None:
            driver.execute_script(
                """
                const el = arguments[0];
                const delta = Math.floor((el && el.clientHeight ? el.clientHeight : window.innerHeight) * 0.9);
                if (el) el.scrollTop = el.scrollTop + delta;
                """,
                target,
            )
            return
    except Exception:
        pass

    try:
        if root is not None:
            driver.execute_script(
                """
                const el = arguments[0];
                const delta = Math.floor((el && el.clientHeight ? el.clientHeight : window.innerHeight) * 0.9);
                el.scrollTop = el.scrollTop + delta;
                """,
                root,
            )
            return
    except Exception:
        pass

    try:
        driver.execute_script("window.scrollBy(0, Math.floor(window.innerHeight * 0.9));")
    except Exception:
        pass

def _find_comment_scroll_target(driver, root):
    """
    Best-effort tìm scrollable container để Facebook load thêm comment.
    Tách riêng để reuse cho windowed expand + prune.
    """
    if root is None:
        return None

    try:
        return driver.execute_script(
            """
            const root = arguments[0];
            if (!root) return null;

            const isVisible = (el) => {
              if (!el) return false;
              const rect = el.getBoundingClientRect();
              return rect.width > 0 && rect.height > 0;
            };

            const overflowOk = (el) => {
              const st = window.getComputedStyle(el);
              if (!st) return false;
              const oy = (st.overflowY || '').toLowerCase();
              return oy === 'auto' || oy === 'scroll';
            };

            const isScrollable = (el) => {
              try {
                return (el.scrollHeight - el.clientHeight) > 80;
              } catch (e) {
                return false;
              }
            };

            const candidates = [];
            for (const sel of ['[role=\"dialog\"]', '[role=\"main\"]', 'div', 'section', 'ul']) {
              try {
                root.querySelectorAll(sel).forEach(el => candidates.push(el));
              } catch (e) {}
            }

            let best = null;
            let bestScore = -1;

            for (const el of candidates) {
              if (!isVisible(el)) continue;
              if (!overflowOk(el)) continue;
              if (!isScrollable(el)) continue;

              let commentAnchors = 0;
              try {
                commentAnchors = el.querySelectorAll("a[href*='comment_id=']").length;
              } catch (e) {
                commentAnchors = 0;
              }

              const scrollRoom = Math.max(0, (el.scrollHeight - el.clientHeight));
              const score = (commentAnchors * 100000) + Math.min(scrollRoom, 200000);
              if (score > bestScore) {
                best = el;
                bestScore = score;
              }
            }

            if (!best) {
              const divs = [];
              try { root.querySelectorAll('div,section,ul').forEach(el => divs.push(el)); } catch (e) {}
              for (const el of divs) {
                if (!isVisible(el)) continue;
                if (!overflowOk(el)) continue;
                if (!isScrollable(el)) continue;
                const scrollRoom = Math.max(0, (el.scrollHeight - el.clientHeight));
                if (scrollRoom > bestScore) {
                  best = el;
                  bestScore = scrollRoom;
                }
              }
            }

            return best;
            """,
            root,
        )
    except Exception:
        return None

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

    try:
        save_article_el(article_el)
    except Exception:
        pass

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
        # Ưu tiên lấy author_url từ các link profile/user phổ biến (kể cả avatar link).
        # Dùng XPath relative (.//) để giới hạn trong article_el.
        author_link = article_el.find_element(
            By.XPATH,
            ".//a[contains(@href,'/user/') or contains(@href,'profile.php') or contains(@href,'/people/')]",
        )

        href = (author_link.get_attribute("href") or "").strip()
        if href and href != "#":
            result["author_url"] = href

    except Exception:
        # fallback từ aria-label
        try:
            aria = (article_el.get_attribute("aria-label") or "").strip()
            m = re.search(r"tên\s+(.+?)\s+vào\s+", aria, flags=re.IGNORECASE)
            if m:
                result["nickname"] = m.group(1).strip()
        except Exception:
            pass

    # Nếu nickname vẫn chưa có, thử thêm vài pattern khác (group/page có thể khác wording).
    if not result.get("nickname"):
        try:
            aria = (article_el.get_attribute("aria-label") or "").strip()
            # Ví dụ: "Comment under the name X at ..."
            m = re.search(r"(?:dưới\s+tên|under\s+the\s+name)\s+(.+?)\s+(?:vào|at)\s+", aria, flags=re.IGNORECASE)
            if m:
                result["nickname"] = m.group(1).strip()
        except Exception:
            pass

    # =========================
    # content (FIX selector + lọc noise)
    # =========================
    try:
        # Selector FB thay đổi liên tục; ưu tiên gom text từ node dir=auto rồi lọc noise.
        candidates = []
        # try:
        #     candidates = article_el.find_elements(By.CSS_SELECTOR, "[data-ad-preview='message']")
        # except Exception:
        #     candidates = []

        if not candidates:
            candidates = article_el.find_elements(By.CSS_SELECTOR, "div[dir='auto'], span[dir='auto']")

        seen_texts: Set[str] = set()
        texts: List[str] = []
        nickname = (result.get("nickname") or "").strip()
        time_text = (result.get("time") or "").strip()

        for node in candidates:
            raw = (node.text or "").strip()
            normalized = _normalize_comment_text(raw)
            if not normalized:
                continue
            if nickname and normalized == nickname:
                continue
            if time_text and normalized == time_text:
                continue
            if normalized in seen_texts:
                continue
            seen_texts.add(normalized)
            texts.append(normalized)

        # FB hay render cùng 1 comment thành nhiều node (full text + các đoạn con),
        # dẫn tới list có phần tử bị "lồng" (substring) như ví dụ user gửi.
        if texts:
            ordered = sorted(texts, key=len, reverse=True)
            filtered: List[str] = []
            for t in ordered:
                if any(t != kept and t in kept for kept in filtered):
                    continue
                filtered.append(t)
            # trả về theo thứ tự xuất hiện gần đúng (ngắn->dài sorting phá order)
            filtered_set = set(filtered)
            result["comment"] = [t for t in texts if t in filtered_set] or None
        else:
            result["comment"] = None

    except Exception:
        pass

    # =========================
    # reactions
    # =========================
    try:
        # FB có thể để số reaction ở nhiều node khác nhau; lấy max nếu tìm thấy.
        possible_counts: List[int] = []

        # 1) aria-label tiếng Việt/Anh thường chứa 'cảm xúc' / 'reaction'
        for sel in (
            "[aria-label*='cảm xúc']",
            "[aria-label*='Bày tỏ cảm xúc']",
            "[aria-label*='reaction']",
            "[aria-label*='React']",
        ):
            try:
                els = article_el.find_elements(By.CSS_SELECTOR, sel)
            except Exception:
                els = []
            for e in els:
                label = (e.get_attribute("aria-label") or "").strip()
                if label:
                    possible_counts.append(_parse_engagement_count(label))
                txt = (e.text or "").strip()
                if txt:
                    possible_counts.append(_parse_engagement_count(txt))

        # 2) fallback: scan toàn bộ button/link trong article, pick max parseable number
        if not possible_counts:
            try:
                els = article_el.find_elements(By.CSS_SELECTOR, "[role='button'], a")
            except Exception:
                els = []
            for e in els:
                label = (e.get_attribute("aria-label") or "").strip()
                if label:
                    possible_counts.append(_parse_engagement_count(label))
                txt = (e.text or "").strip()
                if txt:
                    possible_counts.append(_parse_engagement_count(txt))

        result["react_count"] = max([c for c in possible_counts if isinstance(c, int)], default=0)

    except Exception:
        result["react_count"] = 0

    
    return result



def parse_comment(driver, post_link, source_url):
    """
    Phase 2: mở `post_link` rồi bung/scroll để lấy comment chi tiết.
    Trả về list[dict] (mỗi dict có nickname, comment, time, react_count, comment_id,...).
    """
    if not post_link:
        return []

    driver.get(post_link)
    logger.info(f"Redirect to {post_link}...")
    post_id = _extract_post_id_from_link(post_link)

    seen: Set[str] = set()
    collected: List[dict] = []
    stable_rounds = 0
    clicked_fingerprints: Set[str] = set()

    for _ in range(80):
        try:
            root = _find_comment_context_root(driver)
        except Exception:
            root = None

        try:
            if root is not None:
                _select_all_comments_filter(driver, root=root, timeout=2.0)
        except Exception:
            pass

        try:
            if root is not None:
                expand_comments_near_viewport(
                    driver,
                    root,
                    clicked_fingerprints,
                    margin_px=EXPAND_VIEWPORT_MARGIN_PX,
                    max_clicks=EXPAND_MAX_CLICKS_PER_ROUND,
                    pause_after_click=0.35,
                )
        except Exception:
            pass

        new_this_round = 0
        try:
            if root is not None:
                anchors = root.find_elements(By.CSS_SELECTOR, "a[href*='comment_id=']")
            else:
                anchors = driver.find_elements(By.CSS_SELECTOR, "a[href*='comment_id=']")
        except Exception:
            anchors = []

        for a in anchors:
            try:
                href = (a.get_attribute("href") or "").strip()
                # Tránh lẫn anchors từ background feed: filter theo post_id nếu có.
                if post_id and post_id not in href:
                    continue
                ids = _extract_comment_ids_from_href(href)
                comment_id = ids.get("comment_id")
                reply_comment_id = ids.get("reply_comment_id")
                key = str(reply_comment_id or comment_id or "").strip()
                if not key or key in seen:
                    continue

                article = _closest_comment_article(driver, a)
                
                if article is None:
                    continue

                detail = _extract_comment_detail_from_article(article)
                # logger.info("Comments info: ", detail)
                if not detail.get("comment_id") and comment_id:
                    detail["comment_id"] = comment_id
                if not detail.get("reply_comment_id") and reply_comment_id:
                    detail["reply_comment_id"] = reply_comment_id
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

        # Chỉ dừng sớm khi đã thu được comment và không còn tăng nữa.
        # Trường hợp mới mở post, comment có thể chỉ load sau khi scroll trong khung post.
        if stable_rounds >= 4 and collected:
            break
        if stable_rounds >= 12:
            break

        # Prune DOM theo article đã processed để tránh DOM phình.
        try:
            if root is not None and seen:
                prune_processed_comment_articles(
                    driver,
                    root,
                    seen,
                    keep_anchors=KEEP_ANCHORS,
                    prune_buffer=PRUNE_BUFFER,
                    max_articles=PRUNE_MAX_ARTICLES_PER_ROUND,
                    protect_margin_px=EXPAND_VIEWPORT_MARGIN_PX,
                )
        except Exception:
            pass

        try:
            _scroll_comment_context(driver, root)
        except Exception:
            break
        time.sleep(0.75)

    return collected


def _extract_post_link(post_element, source_url: str) -> str:
    candidates = _safe_find_elements(
        post_element,
        [
            "a[href*='/posts/']",
            "a[href*='/permalink/']",
            "a[href*='story_fbid=']",
            "a[href*='/reel/']",
            "a[role='link'][href*='facebook.com']",
        ],
        selector_name=_post_selector("link", source_url),
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


def _extract_comment(post_element, source_url: str) -> int:
    raw_text = _safe_text(
        post_element,
        [
            "span.xkrqix3.x1sur9pj",
            "div[aria-label*='bình luận' i]",
            "div[aria-label*='comment' i]",
        ],
        selector_name=_post_selector("comment", source_url),
    )
    return _parse_engagement_count(raw_text)


def _extract_share(post_element, source_url: str) -> int:
    raw_text = _safe_text(
        post_element,
        [
            "span.xkrqix3.x1sur9pj",
            "div[aria-label*='chia sẻ' i]",
            "div[aria-label*='share' i]",
        ],
        selector_name=_post_selector("share", source_url),
    )
    return _parse_engagement_count(raw_text)


def _extract_engagement_counts(post_element, source_url: str) -> tuple[Dict[str, int], Dict[str, int]]:
    engagement_counts = {
        "comment": _extract_comment(post_element, source_url),
        "share": _extract_share(post_element, source_url),
    }
    reaction_breakdown = {
        "like": 0,
        "love": 0,
        "haha": 0,
        "wow": 0,
        "sad": 0,
        "angry": 0,
        "care": 0,
    }
    return engagement_counts, reaction_breakdown

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

                # tooltip = wait.until(
                #     EC.presence_of_element_located((By.CSS_SELECTOR, "[role='tooltip']"))
                # )
                # wait.until(lambda d: tooltip.text.strip() != "")

                href = el.get_attribute("href")
                print("href: ", href)
                if not href:
                    continue

                if "facebook.com/groups" in href and "/posts/" in href:
                     
                    result["timestamp_url"] = href

                    # ✅ Ưu tiên lấy từ aria-label hoặc title (không bị nhiễu)
                    timestamp_text = (
                        el.get_attribute("aria-label")
                        or el.get_attribute("title")
                        or ""
                    )

                    # ✅ Fallback: lấy text từ element con <abbr> hoặc <span>
                    if not timestamp_text:
                        try:
                            abbr = el.find_element(By.TAG_NAME, "abbr")
                            timestamp_text = abbr.get_attribute("title") or abbr.text
                        except Exception:
                            pass

                    # ✅ Fallback cuối: dùng tooltip sau khi hover
                    if not timestamp_text:
                        try:
                            tooltip = driver.find_element(
                                By.CSS_SELECTOR, "[role='tooltip']"
                            )
                            timestamp_text = tooltip.text
                        except Exception:
                            pass

                    result["timestamp_text"] = timestamp_text.strip()

                    # Extract post_id
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

import re
from urllib.parse import urlparse, parse_qs


def get_author_id(url: str):
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

def _extract_author(post_element, source_url: str) -> Dict[str, str]:
    author_name = _safe_text(
        post_element,
        [
            "h2 a[role='link'] span[dir='auto']",
            "h2 a b span",
            "strong span[dir='auto']",
            "a[role='link'] h3 span[dir='auto']",
        ],
        selector_name=_post_selector("author_name", source_url),
    )
    author_url = _safe_attr(
        post_element,
        [
            "h2 a[href*='/user/']",
            "a[aria-label][href*='facebook.com/profile.php']",
        ],
        "href",
        selector_name=_post_selector("author_url", source_url),
    )
    # author_url = _clean_fb_link(author_url)
    author_id = get_author_id(author_url)
    
    avatar = ""
    avatar_el = _safe_find_first(
        post_element,
        [
            ".xjp7ctv > [type='nested/pressable'] > .x1i10hfl",
            "object > a:nth-child(1)",
            "a[aria-label] image",
        ],
        selector_name=_post_selector("author_avatar", source_url),
    )
    if avatar_el is not None:
        for attr in ("xlink:href", "href", "src"):
            try:
                avatar = avatar_el.get_attribute(attr) or ""
            except Exception:
                avatar = ""
            if avatar:
                break

    return {"name": author_name, "url": author_url, "avatar": avatar, "id":author_id}


def _find_post_content_roots(post_element, source_url: str):
    selectors = [
        "[data-ad-comet-preview='message']",
        "div[data-ad-preview='message']",
        "ol[class*='html-ol']",
        "div[dir='auto'][style*='text-align']",
    ]

    roots = _safe_find_elements(post_element, selectors)
    if roots:
        return roots

    return [post_element]


POST_SEE_MORE_PATTERNS = [
    "xem thêm",
    "see more",
]


def expand_post_content_until_done(
    driver: "WebDriver",
    post_element: "WebElement",
    source_url: str,
    *,
    pause_after_click: float = 0.2,
    max_rounds: int = 3,
) -> int:
    """
    Phase 1 helper: chỉ bung nội dung bài viết (ví dụ nút 'Xem thêm' trong content),
    KHÔNG đụng tới comment/reply và KHÔNG scroll để load comment.
    """
    total_clicked = 0
    clicked_fingerprints: Set[str] = set()

    for _ in range(max_rounds):
        clicked_this_round = 0

        for root in _find_post_content_roots(post_element, source_url):
            try:
                buttons = root.find_elements(
                    By.XPATH,
                    ".//*[self::div or self::span]"
                    "[@role='button' and (normalize-space(.)='Xem thêm' or normalize-space(.)='See more')]",
                )
            except Exception:
                continue

            for btn in buttons:
                try:
                    if not _element_visible_enabled(btn):
                        continue

                    label = _normalize_text(btn.text) or _normalize_text(
                        btn.get_attribute("aria-label")
                    )
                    if not label:
                        continue
                    if not any(pat == label for pat in POST_SEE_MORE_PATTERNS):
                        continue

                    fp = f"{label}|{btn.get_attribute('outerHTML')[:200]}"
                    if fp in clicked_fingerprints:
                        continue

                    if _safe_click(driver, btn):
                        clicked_fingerprints.add(fp)
                        clicked_this_round += 1
                        total_clicked += 1
                        time.sleep(pause_after_click)
                except StaleElementReferenceException:
                    continue
                except Exception:
                    continue

        if clicked_this_round == 0:
            break

    return total_clicked


def _expand_post_content(driver, post_element, source_url: str) -> None:
    try:
        expand_post_content_until_done(driver, post_element, source_url)
    except Exception:
        return


def _extract_content(driver, post_element, source_url: str) -> str:
    _expand_post_content(driver, post_element, source_url)

    content_texts = _collect_texts(
        post_element,
        [
            "ol[class*='html-ol'] li div span",
        ],
        selector_name=_post_selector("content", source_url),
    )
    content = "\n".join(content_texts).strip()
    if content.strip():
        return re.sub(r"\n{3,}", "\n\n", content).strip()
    try:
        content_elements = WebDriverWait(post_element, 3).until(
            lambda root: root.find_elements(By.CSS_SELECTOR, ".xyinxu5 > .x193iq5w")
        )
        content = "\n".join(el.text for el in content_elements if el.text.strip())
        return re.sub(r"\n{3,}", "\n\n", content).strip()
    except TimeoutException:
        return ""


def _extract_images(post_element, source_url: str) -> List[str]:
    images: List[str] = []
    for img in _safe_find_elements(
        post_element,
        [
            "[data-visualcompletion='media-vc-image']",
            "img[data-visualcompletion='media-vc-image']",
            "img[data-imgperflogname='feedCoverPhoto']",
            "img[referrerpolicy]",
        ],
        selector_name=_post_selector("image", source_url),
    ):
        try:
            src = (
                img.get_attribute("src")
                or img.get_attribute("currentSrc")
                or img.get_attribute("data-src")
                or ""
            )
        except Exception:
            continue
        if not src or "fbcdn" not in src:
            continue
        if src not in images:
            images.append(src)
    return images


def _extract_videos(post_element, source_url: str) -> List[str]:
    videos: List[str] = []
    for video in _safe_find_elements(post_element, ["video"], selector_name=_post_selector("video", source_url)):
        try:
            src = video.get_attribute("src") or video.get_attribute("poster") or ""
        except Exception:
            continue
        if src and src not in videos:
            videos.append(src)
    return videos


def _extract_visibility(post_element, source_url: str) -> str:
    return _safe_text(
        post_element,
        [
            "span.xzpqnlu.x179tack",
            "div[aria-label*='Công khai'] span",
            "div[aria-label*='Public'] span",
            ".xs7f9wi"
        ],
        selector_name=_post_selector("visibility", source_url),
    )

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

# ===========================================================================
#                   EXTRACT POST PINELINE
# ===========================================================================
def _extract_selector_post_context(driver, post_element, group_url: str) -> Dict[str, Any]:
    
    author = _extract_author(post_element, group_url)
    visibility = _extract_visibility(post_element, group_url)
    engagement_counts, reaction_breakdown = _extract_engagement_counts(post_element, group_url)
    content = _extract_content(driver, post_element, group_url)
    timestamp_info = get_post_timestamp_and_id(driver, post_element, group_url)
    print("timestamp_info: ", timestamp_info)
    # Phase 1: chỉ thu metadata + post link. Comment chi tiết sẽ cào ở phase 2
    # để tránh driver.get(post_link) làm stale DOM khi đang duyệt nhiều post.
    comments: list[dict] = []
    
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
        "comments": comments,
        "comments_crawled": False,
        "comment": engagement_counts["comment"],
        "share": engagement_counts["share"],
        "reactions": reaction_breakdown,
        "hashtag": re.findall(r"#\w+", content or ""),
        "source_id": _extract_source_id(group_url),
        "visibility": visibility,
    }
    identity = _build_post_identity(item)
    if not identity:
        return {
            "item": None,
        }

    if not item["id"]:
        item["id"] = identity
    if not item["rid"]:
        item["rid"] = identity

    return {
        "item": item,
    }

def extract_selector_post(driver, post_element, group_url: str) -> Optional[Dict[str, Any]]:
    context = _extract_selector_post_context(driver, post_element, group_url)
    return context["item"]

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

def collect_visible_selector_posts(driver, source_url: str):
    seen = []
    unique_ids = set()

    # Case 1: dialog trong feed
    elements = driver.find_elements(
        By.CSS_SELECTOR,
        "div[role='dialog'][aria-label='Trình xem ảnh'] div[role='complementary']"
    )

    # Case 2: navigate thẳng driver.get → photo URL
    if not elements:
        elements = driver.find_elements(
            By.CSS_SELECTOR,
            "div[role='complementary']"
        )

    # Case 3: fallback
    if not elements:
        elements = driver.find_elements(
            By.CSS_SELECTOR,
            "[data-name='media-viewer-nav-container']"
        )

    elements = _keep_outermost_elements(driver, elements)

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

import json
from datetime import datetime

def _dump_post_debug(driver, post_element, context, idx: int):
    try:
        data = {
        "index": idx,
        "tag": post_element.tag_name,
        "text": post_element.text[:500],
        "html": post_element.get_attribute("outerHTML"),
        "context": context,  # cái này đã là dict → dump OK
        "url": driver.current_url,
        "timestamp": datetime.utcnow().isoformat()
        }

        filename = f"debug_post_{idx}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.info(f"Dump failed: {e}")

def extract_best_selector_post(
    driver,
    group_url: str,
    preferred_photo_id: str | None = None,
    *,
    require_preferred_match: bool = False,
) -> Optional[Dict[str, Any]]:
    fallback_item: Optional[Dict[str, Any]] = None

    # elements = collect_visible_selector_posts(driver, group_url)
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

        # Debug xem extract được gì
        logger.info(f"[DEBUG] idx={idx}")
        logger.info(f"[DEBUG] author={context.get('item', {}) and context['item'].get('author')}")
        logger.info(f"[DEBUG] content snippet={context.get('item', {}) and str(context['item'].get('comments', ''))[:100]}")
        logger.info(f"[DEBUG] photo_fbid={context.get('photo_fbid')}")
        logger.info(f"[DEBUG] link={context.get('link')}")
        logger.info(f"[DEBUG] item is None: {context.get('item') is None}")

        _dump_post_debug(driver, post_element, context, idx)
        item = context.get("item")
        logger.info("item`: ", item)
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

# ===============================================

import time
from typing import List, Optional, Set

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver, WebElement
from selenium.common.exceptions import (
    StaleElementReferenceException,
    ElementClickInterceptedException,
    ElementNotInteractableException,
    JavascriptException,
)

EXPAND_PATTERNS = [
    "xem thêm",
    "xem thêm bình luận",
    "xem thêm câu trả lời",
    "xem thêm phản hồi",
    "xem tất cả phản hồi",
    "xem tất cả",
    "phản hồi trước",
    "bình luận trước",
]

SKIP_PATTERNS = [
    "ẩn bớt",
    "thích",
    "chia sẻ",
    "gửi",
    "viết bình luận",
    "bày tỏ cảm xúc",
    "tất cả cảm xúc",
    "hành động với bài viết này",
]

# Windowed expand + prune (tinh chỉnh theo máy/post)
KEEP_ANCHORS = 250
PRUNE_BUFFER = 150
EXPAND_VIEWPORT_MARGIN_PX = 1200
EXPAND_MAX_CLICKS_PER_ROUND = 30
PRUNE_MAX_ARTICLES_PER_ROUND = 200


def _normalize_text(text: Optional[str]) -> str:
    return " ".join((text or "").split()).strip().lower()


def _is_expand_text(text: str) -> bool:
    t = _normalize_text(text)
    if not t:
        return False

    if any(skip in t for skip in SKIP_PATTERNS):
        return False

    # match rộng để bắt cả "Xem tất cả 2 phản hồi"
    if any(pat in t for pat in EXPAND_PATTERNS):
        return True

    # fallback cho các dạng có số chen giữa
    if "xem" in t and (
        "bình luận" in t or
        "phản hồi" in t or
        "trả lời" in t or
        "thêm" in t
    ):
        return True

    return False


def _element_visible_enabled(el: WebElement) -> bool:
    try:
        return el.is_displayed() and el.is_enabled()
    except Exception:
        return False


def _safe_click(driver: WebDriver, el: WebElement) -> bool:
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center', inline:'center'});", el
        )
        time.sleep(0.15)
    except Exception:
        pass

    # click thường
    try:
        el.click()
        return True
    except (StaleElementReferenceException,
            ElementClickInterceptedException,
            ElementNotInteractableException):
        pass
    except Exception:
        pass

    # click JS fallback
    try:
        driver.execute_script("arguments[0].click();", el)
        return True
    except (StaleElementReferenceException, JavascriptException):
        return False
    except Exception:
        return False


def _get_relative_top(driver: WebDriver, el: WebElement, container: WebElement | None) -> float | None:
    """
    Trả về `top` của element so với viewport của container (nếu có),
    hoặc so với window viewport nếu container=None.
    """
    try:
        return driver.execute_script(
            """
            const el = arguments[0];
            const container = arguments[1];
            if (!el) return null;
            const r = el.getBoundingClientRect();
            if (!r) return null;
            if (!container) return r.top;
            const cr = container.getBoundingClientRect();
            if (!cr) return null;
            return (r.top - cr.top);
            """,
            el,
            container,
        )
    except Exception:
        return None


def _is_near_viewport(driver: WebDriver, el: WebElement, container: WebElement | None, margin_px: int) -> bool:
    try:
        return bool(
            driver.execute_script(
                """
                const el = arguments[0];
                const container = arguments[1];
                const margin = arguments[2] || 0;
                if (!el) return false;

                const r = el.getBoundingClientRect();
                if (!r) return false;

                if (!container) {
                  const viewTop = -margin;
                  const viewBottom = (window.innerHeight || 0) + margin;
                  return (r.bottom >= viewTop) && (r.top <= viewBottom);
                }

                const cr = container.getBoundingClientRect();
                if (!cr) return false;
                const top = r.top - cr.top;
                const bottom = r.bottom - cr.top;
                const viewTop = -margin;
                const viewBottom = (cr.height || 0) + margin;
                return (bottom >= viewTop) && (top <= viewBottom);
                """,
                el,
                container,
                int(margin_px),
            )
        )
    except Exception:
        return False


def _find_articles_near_viewport(
    driver: WebDriver,
    root: WebElement,
    container: WebElement | None,
    margin_px: int,
) -> List[WebElement]:
    try:
        return driver.execute_script(
            """
            const root = arguments[0];
            const container = arguments[1];
            const margin = arguments[2] || 0;
            if (!root) return [];

            const els = root.querySelectorAll("[role='article']");
            const res = [];

            let viewHeight = window.innerHeight || 0;
            let cTop = 0;
            if (container) {
              const cr = container.getBoundingClientRect();
              viewHeight = cr && cr.height ? cr.height : viewHeight;
              cTop = cr && typeof cr.top === 'number' ? cr.top : 0;
            }

            const viewTop = -margin;
            const viewBottom = viewHeight + margin;

            for (const el of els) {
              try {
                const r = el.getBoundingClientRect();
                const top = container ? (r.top - cTop) : r.top;
                const bottom = container ? (r.bottom - cTop) : r.bottom;
                if (bottom >= viewTop && top <= viewBottom) res.push(el);
              } catch (e) {}
            }

            return res;
            """,
            root,
            container,
            int(margin_px),
        )
    except Exception:
        return []


def expand_comments_near_viewport(
    driver: WebDriver,
    root: WebElement,
    clicked_fingerprints: Set[str],
    *,
    margin_px: int = EXPAND_VIEWPORT_MARGIN_PX,
    max_clicks: int = EXPAND_MAX_CLICKS_PER_ROUND,
    pause_after_click: float = 0.35,
) -> int:
    if root is None:
        return 0

    container = _find_comment_scroll_target(driver, root)
    articles = _find_articles_near_viewport(driver, root, container, margin_px=margin_px)

    total_clicked = 0
    for article in articles:
        try:
            buttons = _find_expand_buttons(article)
        except StaleElementReferenceException:
            continue
        except Exception:
            continue

        for btn in buttons:
            try:
                label = (
                    _normalize_text(btn.text)
                    or _normalize_text(btn.get_attribute("aria-label"))
                    or _normalize_text(btn.get_attribute("title"))
                )
                fp = f"{label}|{btn.get_attribute('outerHTML')[:200]}"
                if fp in clicked_fingerprints:
                    continue

                if _safe_click(driver, btn):
                    clicked_fingerprints.add(fp)
                    total_clicked += 1
                    time.sleep(pause_after_click)
                    if total_clicked >= max_clicks:
                        return total_clicked
            except StaleElementReferenceException:
                continue
            except Exception:
                continue

    return total_clicked


def prune_processed_comment_articles(
    driver: WebDriver,
    root: WebElement,
    seen: Set[str],
    *,
    keep_anchors: int = KEEP_ANCHORS,
    prune_buffer: int = PRUNE_BUFFER,
    max_articles: int = PRUNE_MAX_ARTICLES_PER_ROUND,
    protect_margin_px: int = EXPAND_VIEWPORT_MARGIN_PX,
) -> int:
    """
    Prune DOM theo đơn vị [role="article"] (comment articles), chỉ xóa các article
    đã processed (key in `seen`) và nằm ngoài vùng gần viewport.

    Bù scroll để neo (cutoff_anchor) giữ vị trí, tránh cảm giác "giật".
    """
    if root is None:
        return 0

    try:
        anchors = root.find_elements(By.CSS_SELECTOR, "a[href*='comment_id=']")
    except Exception:
        anchors = []

    if len(anchors) <= (keep_anchors + prune_buffer):
        return 0

    cutoff_index = max(0, len(anchors) - keep_anchors)
    if cutoff_index <= 0 or cutoff_index >= len(anchors):
        return 0

    cutoff_anchor = anchors[cutoff_index]
    container = _find_comment_scroll_target(driver, root)

    before_top = _get_relative_top(driver, cutoff_anchor, container)
    if before_top is None:
        return 0

    prunable: List[WebElement] = []
    seen_articles: Set[str] = set()

    for a in anchors[:cutoff_index]:
        try:
            href = (a.get_attribute("href") or "").strip()
            ids = _extract_comment_ids_from_href(href)
            comment_id = ids.get("comment_id")
            reply_comment_id = ids.get("reply_comment_id")
            key = str(reply_comment_id or comment_id or "").strip()
            if not key or key not in seen:
                continue

            article = _closest_comment_article(driver, a)
            if article is None:
                continue

            # Không prune vùng gần viewport (tránh mất trước mắt)
            if _is_near_viewport(driver, article, container, margin_px=protect_margin_px):
                continue

            fp = article.get_attribute("outerHTML")[:220]
            if fp in seen_articles:
                continue
            seen_articles.add(fp)
            prunable.append(article)
            if len(prunable) >= max_articles:
                break
        except StaleElementReferenceException:
            continue
        except Exception:
            continue

    if not prunable:
        return 0

    removed = 0
    for article in prunable:
        try:
            driver.execute_script(
                """
                const el = arguments[0];
                if (el && el.remove) el.remove();
                """,
                article,
            )
            removed += 1
        except Exception:
            continue

    if removed <= 0:
        return 0

    after_top = _get_relative_top(driver, cutoff_anchor, container)
    if after_top is None:
        return removed

    delta = after_top - before_top
    if abs(delta) < 1:
        return removed

    try:
        if container is not None:
            driver.execute_script(
                """
                const container = arguments[0];
                const delta = arguments[1];
                if (container) container.scrollTop = (container.scrollTop || 0) + delta;
                """,
                container,
                float(delta),
            )
        else:
            driver.execute_script("window.scrollBy(0, arguments[0]);", float(delta))
    except Exception:
        pass

    return removed


COMMENT_SORT_DROPDOWN_PATTERNS = [
    "phù hợp nhất",
    "most relevant",
    "top comments",
    "relevant",
]

ALL_COMMENTS_PATTERNS = [
    "tất cả bình luận",
    "all comments",
]


def _select_all_comments_filter(
    driver: WebDriver,
    root: Optional[WebElement] = None,
    timeout: float = 2.0,
) -> bool:
    """
    Chọn filter 'Tất cả bình luận' (All comments) nếu đang ở 'Phù hợp nhất' (Most relevant).

    Lưu ý: menu có thể render ở layer global (document.body), nên tìm option theo `driver`.
    Hàm chạy best-effort, không raise.
    """
    try:
        scope = root if root is not None else driver
        buttons = scope.find_elements(By.CSS_SELECTOR, "[role='button']")
    except Exception:
        buttons = []

    dropdown = None
    for btn in buttons:
        try:
            label = (
                _normalize_text(btn.text)
                or _normalize_text(btn.get_attribute("aria-label"))
                or _normalize_text(btn.get_attribute("title"))
            )
            if not label:
                continue
            if any(pat in label for pat in ALL_COMMENTS_PATTERNS):
                return True
            if any(pat in label for pat in COMMENT_SORT_DROPDOWN_PATTERNS):
                dropdown = btn
                break
        except StaleElementReferenceException:
            continue
        except Exception:
            continue

    if dropdown is None:
        return False

    if not _safe_click(driver, dropdown):
        return False

    # Đợi menu xuất hiện và click 'Tất cả bình luận' / 'All comments'
    wait = WebDriverWait(driver, timeout)

    def _pick_all_comments(_driver: WebDriver) -> bool:
        try:
            candidates = _driver.find_elements(
                By.XPATH,
                "//*[@role='menuitem' or @role='menuitemradio' or @role='option' or @role='radio']",
            )
        except Exception:
            candidates = []

        for el in candidates:
            try:
                text = (
                    _normalize_text(el.text)
                    or _normalize_text(el.get_attribute("aria-label"))
                    or _normalize_text(el.get_attribute("title"))
                )
                if not text:
                    continue
                if any(pat == text for pat in ALL_COMMENTS_PATTERNS):
                    return _safe_click(_driver, el)
            except StaleElementReferenceException:
                continue
            except Exception:
                continue

        return False

    try:
        wait.until(_pick_all_comments)
        time.sleep(0.2)
        return True
    except TimeoutException:
        return False
    except Exception:
        return False


def _find_expand_buttons(root: WebElement) -> List[WebElement]:
    """
    Tìm các button/div/span/a có text kiểu 'Xem thêm', 'Xem tất cả 2 phản hồi',...
    Dùng XPath tương đối để chỉ quét trong root.
    """
    xpath = """
    .//*[self::div or self::span or self::a]
      [
        @role='button'
        or @role='link'
        or self::a
      ]
    """
    candidates = root.find_elements(By.XPATH, xpath)

    results = []
    seen = set()

    for el in candidates:
        try:
            text = _normalize_text(el.text)
            aria = _normalize_text(el.get_attribute("aria-label"))
            title = _normalize_text(el.get_attribute("title"))
            merged = " | ".join(x for x in [text, aria, title] if x)

            if not _is_expand_text(merged):
                continue

            if not _element_visible_enabled(el):
                continue

            key = (
                merged,
                el.get_attribute("outerHTML")[:300]
            )
            if key in seen:
                continue
            seen.add(key)
            results.append(el)
        except StaleElementReferenceException:
            continue
        except Exception:
            continue

    return results


def expand_comments_until_done(
    driver: WebDriver,
    root: WebElement,
    pause_after_click: float = 0.35,
    max_rounds: int = 50,
) -> int:
    total_clicked = 0
    clicked_fingerprints: Set[str] = set()

    # # Ưu tiên chuyển sang 'Tất cả bình luận' trước khi bung comment/scroll.
    # try:
    #     _select_all_comments_filter(driver, root=root, timeout=2.0)
    # except Exception:
    #     pass

    for _ in range(max_rounds):
        try:
            buttons = _find_expand_buttons(root)
        except StaleElementReferenceException:
            break
        except Exception:
            break

        if not buttons:
            break

        clicked_this_round = 0

        for btn in buttons:
            try:
                label = (
                    _normalize_text(btn.text)
                    or _normalize_text(btn.get_attribute("aria-label"))
                    or _normalize_text(btn.get_attribute("title"))
                )

                fp = f"{label}|{btn.get_attribute('outerHTML')[:200]}"
                if fp in clicked_fingerprints:
                    continue

                if _safe_click(driver, btn):
                    clicked_fingerprints.add(fp)
                    clicked_this_round += 1
                    total_clicked += 1
                    time.sleep(pause_after_click)

            except StaleElementReferenceException:
                continue
            except Exception:
                continue

        if clicked_this_round == 0:
            break

    return total_clicked