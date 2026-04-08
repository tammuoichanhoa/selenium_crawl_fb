from datetime import date
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException


def wait_for(driver, timeout: int = 20) -> WebDriverWait:
    return WebDriverWait(driver, timeout)


def open_filter_dialog(driver):
    """
    Click nút 'Bộ lọc' ở header group để mở panel có 'Đi đến:' + các combobox.
    """
    w = wait_for(driver)
    btn = w.until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "//span[normalize-space()='Bộ lọc']"
                "/ancestor::div[@role='none' or @role='button'][1]",
            )
        )
    )
    driver.execute_script("arguments[0].click();", btn)


def _select_enddate_combo_option(driver, part: str, option_text: str):
    if part not in ("năm", "tháng", "ngày"):
        raise ValueError("part phải là 'năm' / 'tháng' / 'ngày'")

    w = wait_for(driver)
    label_contains = f"kết thúc {part}"

    combo_xpath = (
        "//div[@role='combobox' and contains(@aria-label, '%s')]" % label_contains
    )
    combo = w.until(EC.element_to_be_clickable((By.XPATH, combo_xpath)))
    driver.execute_script("arguments[0].click();", combo)

    option_xpath = "//div[@role='option']//span[normalize-space()='%s']" % option_text
    opt = w.until(EC.element_to_be_clickable((By.XPATH, option_xpath)))
    driver.execute_script("arguments[0].click();", opt)


def go_to_date(driver, target: date):
    w = wait_for(driver)

    open_filter_dialog(driver)

    # Năm
    _select_enddate_combo_option(driver, "năm", str(target.year))

    # Tháng
    month_text = f"Tháng {target.month}"
    try:
        _select_enddate_combo_option(driver, "tháng", month_text)
    except Exception:
        _select_enddate_combo_option(driver, "tháng", str(target.month))

    # Ngày
    _select_enddate_combo_option(driver, "ngày", str(target.day))

    # Xong
    done_btn = w.until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "//div[@role='button' and .//span[normalize-space()='Xong'] and not(@aria-disabled='true')]",
            )
        )
    )
    driver.execute_script("arguments[0].click();", done_btn)
    time.sleep(2.0)
