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


def dismiss_login_popup_if_present(driver: Any):
    """Attempt to close Facebook login popup or any blocking overlay"""
    try:
        # 1. Thử tìm và click nút Đóng nếu có
        close_xpath = "//div[(@aria-label='Đóng' or @aria-label='Close') and @role='button']"
        close_btns = driver.find_elements(By.XPATH, close_xpath)
        for btn in close_btns:
            try:
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(0.5)
            except:
                pass
                
        # 2. Xoá cứng popup băng JS (dành cho loại không có nút Đóng)
        remove_popup_js = """
        // Tìm pop-up theo form login
        var form = document.getElementById('login_popup_cta_form');
        if (form) {
            var dialog = form.closest('div[role="dialog"]');
            if (dialog) {
                var overlay = dialog.closest('.__fb-light-mode');
                if (overlay) {
                    overlay.remove();
                } else {
                    dialog.remove();
                }
            }
            document.body.style.overflow = 'auto';
            document.body.style.position = '';
        }
        
        // Quét thêm bất kỳ div[role="dialog"] nào mang tính chất bắt đăng nhập (chứa các text kinh điển)
        var dialogs = document.querySelectorAll('div[role="dialog"]');
        for (var i = 0; i < dialogs.length; i++) {
            var text = dialogs[i].innerText || "";
            if (text.includes("Xem thêm trên Facebook") || 
                text.includes("See more on Facebook") || 
                (text.includes("Đăng nhập") && text.includes("Tạo tài khoản mới")) ||
                (text.includes("Log In") && text.includes("Create new account"))) {
                
                var blocker = dialogs[i].closest('.__fb-light-mode') || dialogs[i];
                if (blocker) blocker.remove();
                
                document.body.style.overflow = 'auto';
                document.body.style.position = '';
            }
        }
        
        // Quét các lớp phủ backdrop
        var backdrops = document.querySelectorAll('div.x1ey2m1c.xtijo5x'); 
        for (var i = 0; i < backdrops.length; i++) {
             // Lớp phủ tối màu của popup thường full màn hình
             if (window.getComputedStyle(backdrops[i]).backgroundColor.includes('rgba')) {
                  backdrops[i].remove();
             }
        }
        """
        driver.execute_script(remove_popup_js)
    except:
        pass


def wait_for_page_ready(driver: Any, timeout: int = 20):
    """Wait for both body presence and document readiness."""
    wait_for_body(driver, timeout)
    wait_for_document_ready(driver, timeout)
    # Tự động quét và đóng pop-up đăng nhập che khuất màn hình (cho chế độ ẩn danh)
    dismiss_login_popup_if_present(driver)


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
