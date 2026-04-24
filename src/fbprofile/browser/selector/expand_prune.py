"""
expand_prune.py
---------------
Bung nút 'Xem thêm', click expand comment/reply,
và prune các comment article đã xử lý để tránh DOM phình.
"""

import time
from typing import List, Optional, Set

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    JavascriptException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from .element_helpers import safe_find_elements
from .scroll_context import find_comment_scroll_target
from .url_text_utils import extract_comment_ids_from_href, normalize_text


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXPAND_PATTERNS = [
    "xem thêm", "xem thêm bình luận", "xem thêm câu trả lời",
    "xem thêm phản hồi", "xem tất cả phản hồi", "xem tất cả",
    "phản hồi trước", "bình luận trước",
]

SKIP_PATTERNS = [
    "ẩn bớt", "thích", "chia sẻ", "gửi", "viết bình luận",
    "bày tỏ cảm xúc", "tất cả cảm xúc", "hành động với bài viết này",
]

POST_SEE_MORE_PATTERNS = ["xem thêm", "see more"]

COMMENT_SORT_DROPDOWN_PATTERNS = ["phù hợp nhất", "most relevant", "top comments", "relevant"]
ALL_COMMENTS_PATTERNS = ["tất cả bình luận", "all comments"]

KEEP_ANCHORS = 250
PRUNE_BUFFER = 150
EXPAND_VIEWPORT_MARGIN_PX = 1200
EXPAND_MAX_CLICKS_PER_ROUND = 30
PRUNE_MAX_ARTICLES_PER_ROUND = 200


# ---------------------------------------------------------------------------
# Element state helpers
# ---------------------------------------------------------------------------

def element_visible_enabled(el) -> bool:
    try:
        return el.is_displayed() and el.is_enabled()
    except Exception:
        return False


def safe_click(driver, el) -> bool:
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center', inline:'center'});", el
        )
        time.sleep(0.15)
    except Exception:
        pass

    try:
        el.click()
        return True
    except (StaleElementReferenceException, ElementClickInterceptedException, ElementNotInteractableException):
        pass
    except Exception:
        pass

    try:
        driver.execute_script("arguments[0].click();", el)
        return True
    except (StaleElementReferenceException, JavascriptException):
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Expand text detection
# ---------------------------------------------------------------------------

def is_expand_text(text: str) -> bool:
    t = normalize_text(text)
    if not t:
        return False
    if any(skip in t for skip in SKIP_PATTERNS):
        return False
    if any(pat in t for pat in EXPAND_PATTERNS):
        return True
    if "xem" in t and ("bình luận" in t or "phản hồi" in t or "trả lời" in t or "thêm" in t):
        return True
    return False


# ---------------------------------------------------------------------------
# Viewport helpers
# ---------------------------------------------------------------------------

def _get_relative_top(driver, el, container) -> Optional[float]:
    try:
        return driver.execute_script(
            """
            const el = arguments[0], container = arguments[1];
            if (!el) return null;
            const r = el.getBoundingClientRect();
            if (!r) return null;
            if (!container) return r.top;
            const cr = container.getBoundingClientRect();
            if (!cr) return null;
            return r.top - cr.top;
            """,
            el, container,
        )
    except Exception:
        return None


def _is_near_viewport(driver, el, container, margin_px: int) -> bool:
    try:
        return bool(driver.execute_script(
            """
            const el = arguments[0], container = arguments[1], margin = arguments[2] || 0;
            if (!el) return false;
            const r = el.getBoundingClientRect();
            if (!r) return false;
            if (!container) {
              return r.bottom >= -margin && r.top <= (window.innerHeight || 0) + margin;
            }
            const cr = container.getBoundingClientRect();
            if (!cr) return false;
            const top = r.top - cr.top, bottom = r.bottom - cr.top;
            return bottom >= -margin && top <= (cr.height || 0) + margin;
            """,
            el, container, int(margin_px),
        ))
    except Exception:
        return False


def _find_articles_near_viewport(driver, root, container, margin_px: int) -> List:
    try:
        return driver.execute_script(
            """
            const root = arguments[0], container = arguments[1], margin = arguments[2] || 0;
            if (!root) return [];
            const els = root.querySelectorAll("[role='article']");
            const res = [];
            let viewHeight = window.innerHeight || 0, cTop = 0;
            if (container) {
              const cr = container.getBoundingClientRect();
              viewHeight = cr && cr.height ? cr.height : viewHeight;
              cTop = cr && typeof cr.top === 'number' ? cr.top : 0;
            }
            for (const el of els) {
              try {
                const r = el.getBoundingClientRect();
                const top = container ? (r.top - cTop) : r.top;
                const bottom = container ? (r.bottom - cTop) : r.bottom;
                if (bottom >= -margin && top <= viewHeight + margin) res.push(el);
              } catch (e) {}
            }
            return res;
            """,
            root, container, int(margin_px),
        )
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Find expand buttons
# ---------------------------------------------------------------------------

def find_expand_buttons(root) -> List:
    xpath = """
    .//*[self::div or self::span or self::a]
      [@role='button' or @role='link' or self::a]
    """
    candidates = root.find_elements(By.XPATH, xpath)
    results, seen = [], set()

    for el in candidates:
        try:
            text = normalize_text(el.text)
            aria = normalize_text(el.get_attribute("aria-label"))
            title = normalize_text(el.get_attribute("title"))
            merged = " | ".join(x for x in [text, aria, title] if x)

            if not is_expand_text(merged):
                continue
            if not element_visible_enabled(el):
                continue

            key = (merged, el.get_attribute("outerHTML")[:300])
            if key in seen:
                continue
            seen.add(key)
            results.append(el)
        except StaleElementReferenceException:
            continue
        except Exception:
            continue

    return results


# ---------------------------------------------------------------------------
# Select "All comments" filter
# ---------------------------------------------------------------------------

def select_all_comments_filter(driver, root=None, timeout: float = 2.0) -> bool:
    try:
        scope = root if root is not None else driver
        buttons = scope.find_elements(By.CSS_SELECTOR, "[role='button']")
    except Exception:
        buttons = []

    dropdown = None
    for btn in buttons:
        try:
            label = (
                normalize_text(btn.text)
                or normalize_text(btn.get_attribute("aria-label"))
                or normalize_text(btn.get_attribute("title"))
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
    if not safe_click(driver, dropdown):
        return False

    wait = WebDriverWait(driver, timeout)

    def _pick_all_comments(d) -> bool:
        try:
            candidates = d.find_elements(
                By.XPATH,
                "//*[@role='menuitem' or @role='menuitemradio' or @role='option' or @role='radio']",
            )
        except Exception:
            candidates = []
        for el in candidates:
            try:
                text = (
                    normalize_text(el.text)
                    or normalize_text(el.get_attribute("aria-label"))
                    or normalize_text(el.get_attribute("title"))
                )
                if text and any(pat == text for pat in ALL_COMMENTS_PATTERNS):
                    return safe_click(d, el)
            except StaleElementReferenceException:
                continue
            except Exception:
                continue
        return False

    try:
        wait.until(_pick_all_comments)
        time.sleep(0.2)
        return True
    except (TimeoutException, Exception):
        return False


# ---------------------------------------------------------------------------
# Expand comments near viewport
# ---------------------------------------------------------------------------

def expand_comments_near_viewport(
    driver,
    root,
    clicked_fingerprints: Set[str],
    *,
    margin_px: int = EXPAND_VIEWPORT_MARGIN_PX,
    max_clicks: int = EXPAND_MAX_CLICKS_PER_ROUND,
    pause_after_click: float = 0.35,
) -> int:
    if root is None:
        return 0

    container = find_comment_scroll_target(driver, root)
    articles = _find_articles_near_viewport(driver, root, container, margin_px=margin_px)
    total_clicked = 0

    for article in articles:
        try:
            buttons = find_expand_buttons(article)
        except StaleElementReferenceException:
            continue
        except Exception:
            continue

        for btn in buttons:
            try:
                label = (
                    normalize_text(btn.text)
                    or normalize_text(btn.get_attribute("aria-label"))
                    or normalize_text(btn.get_attribute("title"))
                )
                fp = f"{label}|{btn.get_attribute('outerHTML')[:200]}"
                if fp in clicked_fingerprints:
                    continue
                if safe_click(driver, btn):
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


# ---------------------------------------------------------------------------
# Expand post content (See more in post body)
# ---------------------------------------------------------------------------

def expand_post_content_until_done(
    driver,
    post_element,
    source_url: str,
    *,
    pause_after_click: float = 0.2,
    max_rounds: int = 3,
) -> int:
    from .post_helpers import find_post_content_roots  # local import để tránh circular

    total_clicked = 0
    clicked_fingerprints: Set[str] = set()

    for _ in range(max_rounds):
        clicked_this_round = 0
        for root in find_post_content_roots(post_element, source_url):
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
                    if not element_visible_enabled(btn):
                        continue
                    label = normalize_text(btn.text) or normalize_text(btn.get_attribute("aria-label"))
                    if not label:
                        continue
                    if not any(pat == label for pat in POST_SEE_MORE_PATTERNS):
                        continue
                    fp = f"{label}|{btn.get_attribute('outerHTML')[:200]}"
                    if fp in clicked_fingerprints:
                        continue
                    if safe_click(driver, btn):
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


# ---------------------------------------------------------------------------
# Expand all comments (full loop)
# ---------------------------------------------------------------------------

def expand_comments_until_done(
    driver,
    root,
    pause_after_click: float = 0.35,
    max_rounds: int = 50,
) -> int:
    total_clicked = 0
    clicked_fingerprints: Set[str] = set()

    for _ in range(max_rounds):
        try:
            buttons = find_expand_buttons(root)
        except (StaleElementReferenceException, Exception):
            break

        if not buttons:
            break

        clicked_this_round = 0
        for btn in buttons:
            try:
                label = (
                    normalize_text(btn.text)
                    or normalize_text(btn.get_attribute("aria-label"))
                    or normalize_text(btn.get_attribute("title"))
                )
                fp = f"{label}|{btn.get_attribute('outerHTML')[:200]}"
                if fp in clicked_fingerprints:
                    continue
                if safe_click(driver, btn):
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


# ---------------------------------------------------------------------------
# Prune processed comment articles
# ---------------------------------------------------------------------------

def prune_processed_comment_articles(
    driver,
    root,
    seen: Set[str],
    *,
    keep_anchors: int = KEEP_ANCHORS,
    prune_buffer: int = PRUNE_BUFFER,
    max_articles: int = PRUNE_MAX_ARTICLES_PER_ROUND,
    protect_margin_px: int = EXPAND_VIEWPORT_MARGIN_PX,
) -> int:
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
    container = find_comment_scroll_target(driver, root)
    before_top = _get_relative_top(driver, cutoff_anchor, container)
    if before_top is None:
        return 0

    prunable: List = []
    seen_articles: Set[str] = set()

    for a in anchors[:cutoff_index]:
        try:
            href = (a.get_attribute("href") or "").strip()
            ids = extract_comment_ids_from_href(href)
            key = str(ids.get("reply_comment_id") or ids.get("comment_id") or "").strip()
            if not key or key not in seen:
                continue

            article = _closest_comment_article(driver, a)
            if article is None:
                continue
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

    removed = 0
    for article in prunable:
        try:
            driver.execute_script("const el = arguments[0]; if (el && el.remove) el.remove();", article)
            removed += 1
        except Exception:
            continue

    if removed <= 0:
        return 0

    after_top = _get_relative_top(driver, cutoff_anchor, container)
    if after_top is None:
        return removed

    delta = after_top - before_top
    if abs(delta) >= 1:
        try:
            if container is not None:
                driver.execute_script(
                    "const c = arguments[0]; c.scrollTop = (c.scrollTop || 0) + arguments[1];",
                    container, float(delta),
                )
            else:
                driver.execute_script("window.scrollBy(0, arguments[0]);", float(delta))
        except Exception:
            pass

    return removed


def _closest_comment_article(driver, el):
    try:
        return driver.execute_script("return arguments[0].closest('[role=\"article\"]');", el)
    except Exception:
        return None
