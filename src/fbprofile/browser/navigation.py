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
    Click nút 'Bộ lọc' hoặc 'Filters' ở header group.
    """
    w = wait_for(driver)
    btn = w.until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "//span[normalize-space()='Bộ lọc' or normalize-space()='Filters']/ancestor::div[@role='none' or @role='button'][1]",
            )
        )
    )
    driver.execute_script("arguments[0].click();", btn)
    time.sleep(1) # Chờ panel mở ra hoàn toàn

def _select_date_dropdown(driver, part_en: str, part_vi: str, option_text: str):
    """
    Hàm chọn giá trị trong combobox (năm/tháng/ngày).
    """
    w = wait_for(driver, 2)
    
    # 1. Mở Dropdown
    # Dùng translate để giả lập lower-case trong XPath 1.0, hỗ trợ tìm aria-label chứa 'year', 'month', 'day' hoặc 'năm', 'tháng', 'ngày'
    xpath_lower = "translate(@aria-label, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')"
    combo_xpath = (
        f"//div[@role='combobox' and ("
        f"contains({xpath_lower}, '{part_en}') or contains({xpath_lower}, '{part_vi}')"
        f")]"
    )
    
    combo = w.until(EC.presence_of_element_located((By.XPATH, combo_xpath)))
    driver.execute_script("arguments[0].click();", combo)
    
    # Đợi một chút để React render danh sách (rất quan trọng đối với Facebook)
    time.sleep(0.5) 

    # 2. Chọn Option
    option_xpath = f"//div[@role='option']//span[normalize-space()='{option_text}']"
    
    try:
        opt = w.until(EC.presence_of_element_located((By.XPATH, option_xpath)))
        # Sử dụng JS click để tránh lỗi ElementClickInterceptedException nếu option bị che khuất
        driver.execute_script("arguments[0].click();", opt)
    except TimeoutException:
        raise ValueError(f"Không tìm thấy tuỳ chọn '{option_text}' trong danh sách.")
    
    # Đợi dropdown đóng lại
    time.sleep(0.5)

def go_to_date(driver, target: date, lang: str = 'en'):
    """
    Đi đến ngày đích. 
    Lưu ý: set lang='vi' nếu giao diện Facebook của bạn hoàn toàn bằng tiếng Việt.
    """
    open_filter_dialog(driver)

    # Xử lý mapping tên tháng (HTML của bạn báo là January, February...)
    if lang == 'en':
        months_en = [
            "January", "February", "March", "April", "May", "June", 
            "July", "August", "September", "October", "November", "December"
        ]
        month_str = months_en[target.month - 1]
    else:
        # Nếu dùng FB tiếng Việt, nó thường hiển thị "Tháng 1", "Tháng 2"...
        month_str = f"Tháng {target.month}"

    # Chọn Năm
    _select_date_dropdown(driver, part_en="year", part_vi="năm", option_text=str(target.year))

    # Chọn Tháng
    _select_date_dropdown(driver, part_en="month", part_vi="tháng", option_text=month_str)

    # Chọn Ngày
    _select_date_dropdown(driver, part_en="day", part_vi="ngày", option_text=str(target.day))

    # Click nút Xong (Done)
    w = wait_for(driver)
    done_btn = w.until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "//div[@role='button' and .//span[normalize-space()='Xong' or normalize-space()='Done'] and not(@aria-disabled='true')]",
            )
        )
    )
    driver.execute_script("arguments[0].click();", done_btn)
    time.sleep(2.0)