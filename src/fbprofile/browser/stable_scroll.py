from __future__ import annotations

import time
from typing import Any, Callable, Dict

from logs.loging_config import logger


DEFAULT_SCROLL_UNTIL_STABLE_CFG: Dict[str, float | int] = {
    "max_scrolls": 50,
    "stable_rounds": 3,
    "scroll_pause_seconds": 1.5,
    "settle_pause_seconds": 0.5,
}


'''
dừng khi cả scrollHeight lẫn số item thu được 
không còn tăng qua nhiều vòng liên tiếp.
'''
def normalize_scroll_until_stable_cfg(
    config: Dict[str, Any] | None = None,
    *,
    defaults: Dict[str, float | int] | None = None,
) -> Dict[str, float | int]:
    resolved: Dict[str, float | int] = dict(DEFAULT_SCROLL_UNTIL_STABLE_CFG)
    if defaults:
        resolved.update(defaults)
    if isinstance(config, dict):
        for key in DEFAULT_SCROLL_UNTIL_STABLE_CFG:
            value = config.get(key)
            if value is not None:
                resolved[key] = value

    try:
        resolved["max_scrolls"] = max(1, int(resolved["max_scrolls"]))
    except (TypeError, ValueError):
        resolved["max_scrolls"] = int(DEFAULT_SCROLL_UNTIL_STABLE_CFG["max_scrolls"])

    try:
        resolved["stable_rounds"] = max(1, int(resolved["stable_rounds"]))
    except (TypeError, ValueError):
        resolved["stable_rounds"] = int(DEFAULT_SCROLL_UNTIL_STABLE_CFG["stable_rounds"])

    for key in ("scroll_pause_seconds", "settle_pause_seconds"):
        try:
            resolved[key] = max(0.0, float(resolved[key]))
        except (TypeError, ValueError):
            resolved[key] = float(DEFAULT_SCROLL_UNTIL_STABLE_CFG[key])

    return resolved


def get_scroll_height(driver) -> int:
    try:
        height = driver.execute_script("return document.body.scrollHeight;")
    except Exception:
        return 0

    try:
        return int(height or 0)
    except (TypeError, ValueError):
        return 0


def scroll_until_stable(
    driver,
    *,
    get_progress_count: Callable[[], int],
    log_prefix: str,
    config: Dict[str, Any] | None = None,
    defaults: Dict[str, float | int] | None = None,
    scroll_script: str = "window.scrollTo(0, document.body.scrollHeight);",
) -> Dict[str, int | bool]:
    resolved_cfg = normalize_scroll_until_stable_cfg(config, defaults=defaults)
    max_scrolls = int(resolved_cfg["max_scrolls"])
    stable_rounds_required = int(resolved_cfg["stable_rounds"])
    scroll_pause_seconds = float(resolved_cfg["scroll_pause_seconds"])
    settle_pause_seconds = float(resolved_cfg["settle_pause_seconds"])

    prev_height = get_scroll_height(driver)
    try:
        prev_count = max(0, int(get_progress_count()))
    except Exception:
        prev_count = 0

    stable_rounds = 0
    iterations = 0

    for iteration in range(1, max_scrolls + 1):
        driver.execute_script(scroll_script)
        if scroll_pause_seconds > 0:
            time.sleep(scroll_pause_seconds)
        if settle_pause_seconds > 0:
            time.sleep(settle_pause_seconds)

        current_height = get_scroll_height(driver)
        try:
            current_count = max(0, int(get_progress_count()))
        except Exception:
            current_count = prev_count

        if current_height <= prev_height and current_count <= prev_count:
            stable_rounds += 1
        else:
            stable_rounds = 0

        logger.info(
            "%s Scroll #%d/%d height=%d items=%d stable=%d/%d",
            log_prefix,
            iteration,
            max_scrolls,
            current_height,
            current_count,
            stable_rounds,
            stable_rounds_required,
        )

        iterations = iteration
        prev_height = current_height
        prev_count = current_count

        if stable_rounds >= stable_rounds_required:
            logger.info(
                "%s Scroll reached stable state after %d iteration(s).",
                log_prefix,
                iteration,
            )
            return {
                "iterations": iterations,
                "stable_rounds": stable_rounds,
                "final_height": current_height,
                "final_count": current_count,
                "stopped_due_to_stable": True,
            }

    logger.info(
        "%s Scroll stopped after max_scrolls=%d.",
        log_prefix,
        max_scrolls,
    )
    return {
        "iterations": iterations,
        "stable_rounds": stable_rounds,
        "final_height": prev_height,
        "final_count": prev_count,
        "stopped_due_to_stable": False,
    }
