"""
scroll_context.py
-----------------
Tìm context root phù hợp (dialog / main) và scroll để load thêm comment.
"""

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from .element_helpers import safe_find_elements
from .selector_utils import post_selector


# ---------------------------------------------------------------------------
# Scroll post (phase 1)
# ---------------------------------------------------------------------------

def scroll_post_for_comments(driver, post_element, source_url: str) -> None:
    wait = WebDriverWait(driver, 2)
    comment_sel = post_selector("comments", source_url)

    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'start', inline: 'nearest'});",
            post_element,
        )
        wait.until(
            lambda d: bool(safe_find_elements(post_element, [], selector_name=comment_sel))
            or abs(int(
                d.execute_script(
                    "return Math.round(arguments[0].getBoundingClientRect().top || 0);",
                    post_element,
                ) or 0
            )) < 400
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
                ) or 0
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
                lambda d, prev=last_height: bool(
                    safe_find_elements(post_element, [], selector_name=comment_sel)
                ) or int(
                    d.execute_script(
                        """
                        const rect = arguments[0].getBoundingClientRect();
                        return Math.max(rect.height || 0, arguments[0].scrollHeight || 0);
                        """,
                        post_element,
                    ) or 0
                ) != prev
            )
        except TimeoutException:
            pass

        if safe_find_elements(post_element, [], selector_name=comment_sel):
            break


# ---------------------------------------------------------------------------
# Find comment context root
# ---------------------------------------------------------------------------

def find_comment_context_root(driver):
    """
    Trả về container đúng để scope query comment:
    Ưu tiên dialog đang hiển thị có chứa comment_id anchors.
    """
    try:
        dialogs = driver.find_elements(By.CSS_SELECTOR, "div[role='dialog']")
    except Exception:
        dialogs = []

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


# ---------------------------------------------------------------------------
# Find scroll target
# ---------------------------------------------------------------------------

def find_comment_scroll_target(driver, root):
    """Best-effort tìm scrollable container để Facebook load thêm comment."""
    if root is None:
        return None
    try:
        return driver.execute_script(
            """
            const root = arguments[0];
            if (!root) return null;

            const isVisible = el => { if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
            const overflowOk = el => { const st = window.getComputedStyle(el); if (!st) return false; const oy = (st.overflowY || '').toLowerCase(); return oy === 'auto' || oy === 'scroll'; };
            const isScrollable = el => { try { return (el.scrollHeight - el.clientHeight) > 80; } catch (e) { return false; } };

            const candidates = [];
            for (const sel of ['[role="dialog"]', '[role="main"]', 'div', 'section', 'ul'])
              try { root.querySelectorAll(sel).forEach(el => candidates.push(el)); } catch (e) {}

            let best = null, bestScore = -1;
            for (const el of candidates) {
              if (!isVisible(el) || !overflowOk(el) || !isScrollable(el)) continue;
              let anchors = 0;
              try { anchors = el.querySelectorAll("a[href*='comment_id=']").length; } catch (e) {}
              const score = (anchors * 100000) + Math.min(Math.max(0, el.scrollHeight - el.clientHeight), 200000);
              if (score > bestScore) { best = el; bestScore = score; }
            }

            if (!best) {
              const divs = [];
              try { root.querySelectorAll('div,section,ul').forEach(el => divs.push(el)); } catch (e) {}
              for (const el of divs) {
                if (!isVisible(el) || !overflowOk(el) || !isScrollable(el)) continue;
                const scrollRoom = Math.max(0, el.scrollHeight - el.clientHeight);
                if (scrollRoom > bestScore) { best = el; bestScore = scrollRoom; }
              }
            }
            return best;
            """,
            root,
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Scroll comment context
# ---------------------------------------------------------------------------

def scroll_comment_context(driver, root) -> None:
    target = find_comment_scroll_target(driver, root)

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
