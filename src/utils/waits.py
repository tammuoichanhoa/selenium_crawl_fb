"""Selenium wait helpers for common page-ready checks."""

from __future__ import annotations

import time  # monotonic timing for waits
from typing import Any  # type hints for driver objects

from selenium.webdriver.common.by import By  # locator strategy constants
from selenium.webdriver.support import expected_conditions as EC  # wait conditions
from selenium.webdriver.support.ui import WebDriverWait  # explicit wait helper


def wait_for_body(driver: Any, timeout: int = 20):
    """Wait until the <body> element is present."""
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )


def wait_for_document_ready(driver: Any, timeout: int = 20) -> bool:
    """Wait until document.readyState is complete."""
    def _is_ready(d):
        """Return True when document.readyState is complete."""
        try:
            return d.execute_script("return document.readyState") == "complete"
        except Exception:
            return False

    WebDriverWait(driver, timeout).until(_is_ready)
    return True


def wait_for_page_ready(driver: Any, timeout: int = 20):
    """Wait for both body presence and document readiness."""
    wait_for_body(driver, timeout)
    wait_for_document_ready(driver, timeout)


def wait_for_seconds(driver: Any, seconds: int | float) -> None:
    """Sleep via WebDriverWait for a positive number of seconds."""
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
