# post/v3/browser/driver.py
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from ..config import env


def make_headless(args) -> bool:
    if getattr(args, "headless", False):
        return True
    if getattr(args, "no_headless", False):
        return False
    return False  # mặc định có UI cho dễ debug


def create_chrome(headless: bool = False):
    chrome_opts = Options()
    chrome_opts.add_argument("--disable-notifications")
    chrome_opts.add_argument("--disable-dev-shm-usage")
    chrome_opts.add_argument("--disable-gpu")
    chrome_opts.add_argument("--no-sandbox")
    chrome_opts.add_argument("--start-maximized")

    # 🚀 NGĂN CHROME DỪNG KHI KHÔNG FOCUS
    chrome_opts.add_argument("--disable-renderer-backgrounding")
    chrome_opts.add_argument("--disable-backgrounding-occluded-windows")
    chrome_opts.add_argument("--disable-background-timer-throttling")
    chrome_opts.add_argument("--disable-features=CalculateNativeWinOcclusion")

    if headless:
        chrome_opts.add_argument("--headless=new")

    driver = webdriver.Chrome(options=chrome_opts)
    driver.set_page_load_timeout(40)
    driver.set_script_timeout(40)
    return driver
