"""
element_helpers.py
------------------
Các wrapper "an toàn" (không throw ra ngoài) để tìm element, đọc text/attr,
và lọc outermost elements. Phụ thuộc vào selector_utils để resolve locator chain.
"""

import re
from typing import Any, Dict, List, Optional, Set

from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

from logs.loging_config import logger
from src.utils.selectors import resolve_locator
from .selector_utils import build_locator_chain


# ---------------------------------------------------------------------------
# Read element text
# ---------------------------------------------------------------------------

def read_element_text(element) -> tuple[str, str]:
    """Đọc text của 1 WebElement; trả về (text, nguồn)."""
    try:
        text = (element.text or "").strip()
    except Exception:
        text = ""
    if text:
        return re.sub(r"\s+", " ", text).strip(), "text"

    for attr in ("textContent", "innerText"):
        try:
            raw = element.get_attribute(attr)
        except Exception:
            raw = ""
        normalized = re.sub(r"\s+", " ", (raw or "")).strip()
        if normalized:
            return normalized, attr

    return "", ""


def _locator_debug_repr(locator: Dict[str, Any]) -> str:
    loc_type = locator.get("type") or locator.get("by") or locator.get("strategy") or "css"
    value = locator.get("value") or locator.get("selector") or ""
    return f"{loc_type}={value}"


# ---------------------------------------------------------------------------
# safe_text / safe_attr
# ---------------------------------------------------------------------------

def safe_text(root, selectors: List[str], selector_name: str | None = None) -> str:
    if selector_name:
        for locator in build_locator_chain(selector_name):
            try:
                by, value = resolve_locator(locator)
                element = root.find_element(by, value)
                text, text_source = read_element_text(element)
                if text:
                    if text_source != "text":
                        logger.debug(
                            "[SEL] selector=%s locator=%s used_fallback=%s value=%r",
                            selector_name, _locator_debug_repr(locator), text_source, text,
                        )
                    return text
                logger.debug(
                    "[SEL] selector=%s locator=%s found element but text empty",
                    selector_name, _locator_debug_repr(locator),
                )
            except NoSuchElementException:
                logger.debug("[SEL] selector=%s locator=%s not found", selector_name, _locator_debug_repr(locator))
                continue
            except Exception as exc:
                logger.debug("[SEL] selector=%s locator=%s failed: %s", selector_name, _locator_debug_repr(locator), exc)
                continue

    for selector in selectors:
        try:
            element = root.find_element(By.CSS_SELECTOR, selector)
            text, text_source = read_element_text(element)
            if text:
                if text_source != "text":
                    logger.debug(
                        "[SEL] selector=%s fallback_css=%s used_fallback=%s value=%r",
                        selector_name or "<inline>", selector, text_source, text,
                    )
                return text
            logger.debug(
                "[SEL] selector=%s fallback_css=%s found element but text empty",
                selector_name or "<inline>", selector,
            )
        except NoSuchElementException:
            logger.debug("[SEL] selector=%s fallback_css=%s not found", selector_name or "<inline>", selector)
            continue
        except Exception as exc:
            logger.debug("[SEL] selector=%s fallback_css=%s failed: %s", selector_name or "<inline>", selector, exc)
            continue
    return ""


def safe_attr(root, selectors: List[str], attr: str, selector_name: str | None = None) -> str:
    if selector_name:
        for locator in build_locator_chain(selector_name):
            try:
                by, value = resolve_locator(locator)
                element = root.find_element(by, value)
                val = element.get_attribute(attr)
                val = val.strip() if isinstance(val, str) else val
                if val:
                    return val
            except Exception:
                continue

    for selector in selectors:
        try:
            element = root.find_element(By.CSS_SELECTOR, selector)
            val = element.get_attribute(attr)
            val = val.strip() if isinstance(val, str) else val
            if val:
                return val
        except Exception:
            continue
    return ""


# ---------------------------------------------------------------------------
# safe_find_elements / safe_find_first / collect_texts
# ---------------------------------------------------------------------------

def safe_find_elements(root, selectors: List[str], selector_name: str | None = None):
    if selector_name:
        for locator in build_locator_chain(selector_name):
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


def safe_find_first(root, selectors: List[str], selector_name: str | None = None):
    if selector_name:
        for locator in build_locator_chain(selector_name):
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


def collect_texts(root, selectors: List[str], selector_name: str | None = None) -> List[str]:
    texts: List[str] = []
    seen: Set[str] = set()
    for element in safe_find_elements(root, selectors, selector_name=selector_name):
        text, _ = read_element_text(element)
        if not text or text in seen:
            continue
        seen.add(text)
        texts.append(text)
    return texts


# ---------------------------------------------------------------------------
# keep_outermost_elements
# ---------------------------------------------------------------------------

def keep_outermost_elements(driver, elements):
    """Loại bỏ các node lồng nhau, chỉ giữ lại ancestor ngoài cùng."""
    filtered = []
    for element in elements:
        try:
            is_nested = any(
                bool(driver.execute_script(
                    "return arguments[0] !== arguments[1] && arguments[0].contains(arguments[1]);",
                    existing, element,
                ))
                for existing in filtered
            )
            if is_nested:
                continue

            filtered = [
                existing for existing in filtered
                if not bool(driver.execute_script(
                    "return arguments[0] !== arguments[1] && arguments[0].contains(arguments[1]);",
                    element, existing,
                ))
            ]
        except Exception:
            pass
        filtered.append(element)
    return filtered
