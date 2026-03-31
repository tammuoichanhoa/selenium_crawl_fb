"""Selector parsing and extraction helpers for Selenium."""

from __future__ import annotations

import os  # file/path handling for selector configs
import re  # pattern matching for selector rules
import time  # timing for waits/retries
import logging
from typing import Any, Dict, List, Tuple  # type hints

from selenium.common.exceptions import TimeoutException  # wait timeout handling
from selenium.webdriver.common.by import By  # locator strategies
from selenium.webdriver.support import expected_conditions as EC  # wait conditions
from selenium.webdriver.support.ui import WebDriverWait  # explicit waits

logger = logging.getLogger(__name__)


def resolve_by(by_value: str) -> str:
    """Map a locator type string to a Selenium By constant."""
    mapping = {
        "css": By.CSS_SELECTOR,
        "css selector": By.CSS_SELECTOR,
        "css_selector": By.CSS_SELECTOR,
        "xpath": By.XPATH,
        "full xpath": By.XPATH,
        "full_xpath": By.XPATH,
        "id": By.ID,
        "name": By.NAME,
        "tag": By.TAG_NAME,
        "tagname": By.TAG_NAME,
        "tag_name": By.TAG_NAME,
        "class": By.CLASS_NAME,
        "classname": By.CLASS_NAME,
        "class_name": By.CLASS_NAME,
        "linktext": By.LINK_TEXT,
        "link_text": By.LINK_TEXT,
        "partiallinktext": By.PARTIAL_LINK_TEXT,
        "partial_link_text": By.PARTIAL_LINK_TEXT,
    }
    key = by_value.strip().lower()
    if key not in mapping:
        supported = ", ".join(sorted(mapping.keys()))
        raise ValueError(f"Unsupported locator strategy '{by_value}'. Use: {supported}")
    return mapping[key]


def validate_selector_payload(payload: Any) -> Dict[str, Any]:
    """Validate selector payload structure and return it."""
    if not isinstance(payload, dict):
        raise ValueError("Selectors config must be a JSON object.")
    if "elements" not in payload:
        non_dict_values = [
            key for key, value in payload.items() if not isinstance(value, dict)
        ]
        if non_dict_values:
            raise ValueError(
                "Selectors config must include an 'elements' object when metadata keys "
                f"are present. Unexpected keys: {', '.join(non_dict_values)}"
            )
    return payload


def normalize_elements_config(raw_elements: Any) -> List[Dict[str, Any]]:
    """Normalize elements config into a list of element dicts."""
    if raw_elements is None:
        return []
    if isinstance(raw_elements, list):
        normalized_list: List[Dict[str, Any]] = []
        for index, item in enumerate(raw_elements):
            if not isinstance(item, dict):
                raise ValueError(f"Element at index {index} must be an object.")
            normalized_list.append(dict(item))
        return _sort_elements_by_priority(normalized_list)
    if isinstance(raw_elements, dict):
        normalized: List[Dict[str, Any]] = []
        for name, cfg in raw_elements.items():
            if cfg is None:
                cfg = {}
            if not isinstance(cfg, dict):
                raise ValueError(f"Element '{name}' must be an object.")
            element_cfg = dict(cfg)
            element_cfg.setdefault("name", name)
            normalized.append(element_cfg)
        return _sort_elements_by_priority(normalized)
    raise ValueError("elements must be a list (legacy) or an object mapping keys to config.")


def _sort_elements_by_priority(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort elements by priority (desc), defaulting to 0 when missing."""
    def get_priority(cfg: Dict[str, Any]) -> int:
        value = cfg.get("priority")
        if value is None and isinstance(cfg.get("primary"), dict):
            value = cfg["primary"].get("priority")
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    return sorted(elements, key=get_priority, reverse=True)


def _parse_data_locator_value(value: Any) -> Tuple[str, str | None]:
    """Parse data-* locator values into (attr, value)."""
    if isinstance(value, dict):
        attr = value.get("attr") or value.get("name") or value.get("attribute")
        val = value.get("value")
        return (str(attr).strip() if attr else "", str(val) if val is not None else None)
    if value is None:
        return ("", None)
    raw = str(value).strip()
    if "=" in raw:
        attr, val = raw.split("=", 1)
        return attr.strip(), val.strip()
    if ":" in raw:
        attr, val = raw.split(":", 1)
        return attr.strip(), val.strip()
    return raw, None


def resolve_locator(locator_cfg: Dict[str, Any]) -> Tuple[str, str]:
    """Resolve a locator config into (By, selector) tuple."""
    locator_type = (
        locator_cfg.get("type")
        or locator_cfg.get("by")
        or locator_cfg.get("strategy")
        or "css"
    )
    value = locator_cfg.get("value") or locator_cfg.get("selector")

    if value is None:
        raise ValueError("Locator config must include 'value' (or legacy 'selector').")

    key = str(locator_type).strip().lower()
    if key in {"data", "data-*", "data_attr", "data-attr"}:
        attr, val = _parse_data_locator_value(value)
        if not attr:
            raise ValueError("Data locator requires an attribute name (e.g. data-testid).")
        if val is None:
            selector = f"[{attr}]"
        else:
            selector = f'[{attr}="{val}"]'
        return By.CSS_SELECTOR, selector

    by = resolve_by(key)
    return by, str(value)


def _normalize_locator_type(locator_cfg: Dict[str, Any]) -> str:
    """Normalize locator type to a lowercase string."""
    locator_type = (
        locator_cfg.get("type")
        or locator_cfg.get("by")
        or locator_cfg.get("strategy")
        or "css"
    )
    return str(locator_type).strip().lower()


def _lint_xpath(value: str) -> List[str]:
    """Return warnings about brittle XPath patterns."""
    warnings: List[str] = []
    raw = value.strip()

    # Absolute XPaths are brittle (e.g. /html/body/...)
    if raw.startswith("/") and not raw.startswith("//"):
        warnings.append("absolute_xpath")
    if raw.startswith("//html") or "/html/" in raw or "/body/" in raw:
        warnings.append("absolute_xpath")

    indexes = re.findall(r"\[(\d+)\]", raw)
    if len(indexes) >= 2:
        warnings.append("many_indexes")
    if any(int(idx) >= 5 for idx in indexes if idx.isdigit()):
        warnings.append("large_index")

    segments = [seg for seg in raw.split("/") if seg and seg not in {".", ".."}]
    if len(segments) >= 7:
        warnings.append("deep_xpath")

    return warnings


def _lint_css(value: str) -> List[str]:
    """Return warnings about brittle CSS patterns."""
    warnings: List[str] = []
    raw = value.strip()

    if ":nth-child" in raw or ":nth-of-type" in raw:
        warnings.append("nth_child")

    if raw.count(">") >= 4:
        warnings.append("deep_css")

    return warnings


def _lint_locator(locator_cfg: Dict[str, Any]) -> List[str]:
    """Lint a locator config based on its type."""
    value = locator_cfg.get("value") or locator_cfg.get("selector")
    if value is None:
        return []

    locator_type = _normalize_locator_type(locator_cfg)
    raw_value = str(value)

    if locator_type in {"xpath", "full xpath", "full_xpath"}:
        return _lint_xpath(raw_value)
    if locator_type in {"css", "css selector", "css_selector"}:
        return _lint_css(raw_value)
    return []


def _extract_tag_from_xpath(value: str) -> str | None:
    """Extract the leading tag from an XPath expression."""
    raw = value.strip()
    if not raw.startswith("//"):
        return None
    remainder = raw[2:]
    if not remainder or remainder[0] == "*":
        return None
    match = re.match(r"([a-zA-Z][\w-]*)", remainder)
    if match:
        return match.group(1)
    return None


def _suggest_locator(locator_cfg: Dict[str, Any]) -> List[str]:
    """Suggest more stable locator alternatives."""
    value = locator_cfg.get("value") or locator_cfg.get("selector")
    if value is None:
        return []

    locator_type = _normalize_locator_type(locator_cfg)
    raw = str(value).strip()
    suggestions: List[str] = []

    if locator_type in {"xpath", "full xpath", "full_xpath"}:
        tag = _extract_tag_from_xpath(raw)

        def add_css(css: str):
            """Add a CSS suggestion if not already present."""
            if css and css not in suggestions:
                suggestions.append(css)

        data_eq = re.search(r"@data-([\w-]+)\s*=\s*['\"]([^'\"]+)['\"]", raw)
        if data_eq:
            attr = f"data-{data_eq.group(1)}"
            val = data_eq.group(2)
            add_css(f'[{attr}="{val}"]')

        data_contains = re.search(
            r"contains\(\s*@data-([\w-]+)\s*,\s*['\"]([^'\"]+)['\"]\s*\)",
            raw,
        )
        if data_contains:
            attr = f"data-{data_contains.group(1)}"
            val = data_contains.group(2)
            add_css(f'[{attr}*="{val}"]')

        id_eq = re.search(r"@id\s*=\s*['\"]([^'\"]+)['\"]", raw)
        if id_eq:
            add_css(f"#{id_eq.group(1)}")

        id_contains = re.search(
            r"contains\(\s*@id\s*,\s*['\"]([^'\"]+)['\"]\s*\)", raw
        )
        if id_contains:
            add_css(f'[id*="{id_contains.group(1)}"]')

        class_eq = re.search(r"@class\s*=\s*['\"]([^'\"]+)['\"]", raw)
        if class_eq:
            classes = [c for c in class_eq.group(1).split() if c]
            if classes:
                add_css("".join(f".{c}" for c in classes))

        class_contains = re.search(
            r"contains\(\s*@class\s*,\s*['\"]([^'\"]+)['\"]\s*\)", raw
        )
        if class_contains:
            add_css(f".{class_contains.group(1)}")

        href_contains = re.search(
            r"contains\(\s*@href\s*,\s*['\"]([^'\"]+)['\"]\s*\)", raw
        )
        if href_contains:
            href_val = href_contains.group(1)
            if tag:
                add_css(f'{tag}[href*="{href_val}"]')
            else:
                add_css(f'[href*="{href_val}"]')

        if tag and suggestions:
            suggestions = [s if s.startswith(tag) else f"{tag}{s}" for s in suggestions]

        if not suggestions:
            suggestions.append("add data-testid and use [data-testid=\"...\"]")

    elif locator_type in {"css", "css selector", "css_selector"}:
        if ":nth-child" in raw or ":nth-of-type" in raw:
            suggestions.append("add data-testid and use [data-testid=\"...\"]")
        elif raw.count(">") >= 4:
            suggestions.append("prefer shorter css or data-testid")

    return suggestions


def guard_fragile_locators(
    elements_cfg: List[Dict[str, Any]],
    mode: str = "warn",
) -> None:
    """Warn or raise if fragile locators are detected."""
    normalized = str(mode or "warn").strip().lower()
    if normalized in {"off", "false", "0", "disabled", "disable"}:
        return

    warnings: List[Tuple[str, str, str, List[str], List[str]]] = []
    for element_cfg in elements_cfg:
        name = element_cfg.get("name") or element_cfg.get("selector") or "unknown"
        for locator_cfg in build_locator_chain(element_cfg):
            locator_type = _normalize_locator_type(locator_cfg)
            value = locator_cfg.get("value") or locator_cfg.get("selector")
            if value is None:
                continue
            lint = _lint_locator(locator_cfg)
            if lint:
                suggestions = _suggest_locator(locator_cfg)
                warnings.append(
                    (name, locator_type, str(value), sorted(set(lint)), suggestions)
                )

    if not warnings:
        return

    lines = ["[locator-guard] Fragile locator(s) detected:"]
    for name, locator_type, value, lint, suggestions in warnings:
        suggest_text = ""
        if suggestions:
            suggest_text = f" suggestions={'; '.join(suggestions)}"
        lines.append(
            f"  - element='{name}' locator={locator_type} value={value!r} issues={','.join(lint)}{suggest_text}"
        )

    message = "\n".join(lines)
    if normalized in {"error", "strict", "raise"}:
        raise ValueError(message)
    logger.warning(message)


def _is_data_locator(locator_cfg: Dict[str, Any]) -> bool:
    """Return True if locator type is data-* based."""
    locator_type = (
        locator_cfg.get("type")
        or locator_cfg.get("by")
        or locator_cfg.get("strategy")
        or ""
    )
    return str(locator_type).strip().lower() in {"data", "data-*", "data_attr", "data-attr"}


def build_locator_chain(element_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build an ordered list of locator configs for an element."""
    locators: List[Dict[str, Any]] = []
    if "primary" in element_cfg:
        primary = element_cfg.get("primary") or {}
        if isinstance(primary, dict):
            locators.append(primary)
    else:
        locators.append(element_cfg)

    fallbacks = element_cfg.get("fallbacks") or []
    if isinstance(fallbacks, list):
        for item in fallbacks:
            if isinstance(item, dict):
                locators.append(item)

    if not locators:
        return []

    data_locators = [loc for loc in locators if _is_data_locator(loc)]
    other_locators = [loc for loc in locators if not _is_data_locator(loc)]
    return data_locators + other_locators


def build_locator_chain_with_meta(
    element_cfg: Dict[str, Any]
) -> List[Tuple[str, Dict[str, Any]]]:
    """Build locators with role metadata (primary/fallback)."""
    locators: List[Tuple[str, Dict[str, Any]]] = []
    if "primary" in element_cfg:
        primary = element_cfg.get("primary") or {}
        if isinstance(primary, dict):
            locators.append(("primary", primary))
    else:
        locators.append(("primary", element_cfg))

    fallbacks = element_cfg.get("fallbacks") or []
    if isinstance(fallbacks, list):
        for item in fallbacks:
            if isinstance(item, dict):
                locators.append(("fallback", item))

    if not locators:
        return []

    data_locators = [loc for loc in locators if _is_data_locator(loc[1])]
    other_locators = [loc for loc in locators if not _is_data_locator(loc[1])]
    return data_locators + other_locators


def _safe_filename(value: str) -> str:
    """Sanitize a value for use in filenames."""
    sanitized = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip())
    return sanitized.strip("._") or "element"


def _capture_debug_artifacts(driver, name: str, capture_dir: str) -> List[str]:
    """Capture screenshot and HTML for debugging."""
    os.makedirs(capture_dir, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    millis = int((time.time() % 1) * 1000)
    base = _safe_filename(f"{name}_{timestamp}_{millis:03d}")
    screenshot_path = os.path.join(capture_dir, f"{base}.png")
    source_path = os.path.join(capture_dir, f"{base}.html")
    saved: List[str] = []

    try:
        if driver.save_screenshot(screenshot_path):
            saved.append(screenshot_path)
    except Exception:
        pass

    try:
        page_source = driver.page_source
        with open(source_path, "w", encoding="utf-8") as file:
            file.write(page_source or "")
        saved.append(source_path)
    except Exception:
        pass

    return saved


def resolve_wait_state(wait_state: str | None) -> str:
    """Normalize wait state to presence/visible/clickable."""
    if not wait_state:
        return "presence"
    normalized = str(wait_state).strip().lower()
    if normalized in {"presence", "present"}:
        return "presence"
    if normalized in {"visible", "visibility"}:
        return "visible"
    if normalized in {"clickable", "click"}:
        return "clickable"
    return "presence"


def resolve_wait_timeout_seconds(
    wait_cfg: Dict[str, Any] | None,
    default_timeout_seconds: int,
) -> int:
    """Resolve timeout seconds from wait config with defaults."""
    if not wait_cfg:
        return default_timeout_seconds
    timeout_ms = wait_cfg.get("timeout_ms")
    if timeout_ms is not None:
        try:
            return max(1, int(float(timeout_ms) / 1000))
        except (TypeError, ValueError):
            return default_timeout_seconds
    timeout_sec = wait_cfg.get("timeout")
    if timeout_sec is not None:
        try:
            return max(1, int(timeout_sec))
        except (TypeError, ValueError):
            return default_timeout_seconds
    return default_timeout_seconds


def extract_element(
    driver,
    element_cfg: Dict[str, Any],
    timeout: int,
    default_wait_cfg: Dict[str, Any] | None,
    debug_cfg: Dict[str, Any] | None = None,
) -> str | None:
    """Extract element text/attribute using locator chain and waits."""
    print(">>>>>>>>>>>", element_cfg)
    attr = element_cfg.get("attribute", "text")
    required = element_cfg.get("required", False)
    name = element_cfg.get("name") or element_cfg.get("selector") or "unknown"

    debug_cfg = debug_cfg or {}
    capture_on_fail = bool(debug_cfg.get("capture_on_fail"))
    capture_dir = str(debug_cfg.get("capture_dir") or os.path.join(os.getcwd(), "debug_artifacts"))

    locator_chain = build_locator_chain_with_meta(element_cfg)
    if not locator_chain:
        raise ValueError("Each element must include a locator definition.")

    wait_cfg = element_cfg.get("wait")
    if not isinstance(wait_cfg, dict):
        wait_cfg = None
    merged_wait_cfg = dict(default_wait_cfg or {})
    merged_wait_cfg.update(wait_cfg or {})
    wait_state = resolve_wait_state(merged_wait_cfg.get("state"))
    wait_timeout = resolve_wait_timeout_seconds(merged_wait_cfg, timeout)

    condition_map = {
        "presence": EC.presence_of_element_located,
        "visible": EC.visibility_of_element_located,
        "clickable": EC.element_to_be_clickable,
    }
    condition = condition_map[wait_state]

    last_error: Exception | None = None
    attempts: List[str] = []
    for role, locator_cfg in locator_chain:
        locator_type = _normalize_locator_type(locator_cfg)
        locator_value = locator_cfg.get("value") or locator_cfg.get("selector")
        attempts.append(f"{role}:{locator_type}={locator_value!r}")
        try:
            by, selector = resolve_locator(locator_cfg)
        except Exception as exc:
            last_error = exc
            logger.warning(
                f"[selector] element='{name}' {role} locator invalid: "
                f"type={locator_type} value={locator_value!r} error={exc}"
            )
            continue
        try:
            element = WebDriverWait(driver, wait_timeout).until(
                condition((by, selector))
            )
            if attr == "text":
                return element.text.strip()
            return element.get_attribute(attr)
        except TimeoutException as exc:
            last_error = exc
            logger.warning(
                f"[selector] element='{name}' {role} locator failed: "
                f"type={locator_type} value={locator_value!r} error={exc}"
            )
            continue

    attempted_text = ", ".join(attempts) if attempts else "none"
    logger.warning(
        f"[selector] element='{name}' not found; tried: {attempted_text}"
    )
    if capture_on_fail:
        saved = _capture_debug_artifacts(driver, name, capture_dir)
        if saved:
            logger.info("[selector] debug artifacts saved: %s", ", ".join(saved))

    if required:
        raise TimeoutException(
            f"Timed out waiting for element '{name}': {last_error}"
        )
    return None
