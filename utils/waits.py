from __future__ import annotations

import time
from typing import Any

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def wait_for_body(driver: Any, timeout: int = 20):
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )


def wait_for_document_ready(driver: Any, timeout: int = 20) -> bool:
    def _is_ready(d):
        try:
            return d.execute_script("return document.readyState") == "complete"
        except Exception:
            return False

    WebDriverWait(driver, timeout).until(_is_ready)
    return True


def wait_for_page_ready(driver: Any, timeout: int = 20):
    wait_for_body(driver, timeout)
    wait_for_document_ready(driver, timeout)


def wait_for_seconds(driver: Any, seconds: int | float) -> None:
    if seconds is None:
        return
    try:
        seconds_value = float(seconds)
    except (TypeError, ValueError):
        return
    if seconds_value <= 0:
        return

    end_time = time.monotonic() + seconds_value
    WebDriverWait(driver, seconds_value).until(lambda _d: time.monotonic() >= end_time)
