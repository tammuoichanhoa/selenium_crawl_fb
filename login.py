import os
import subprocess
import time
from typing import Dict, List, Optional
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# Đảm bảo bạn đã có các hàm này trong file utils.py
from utils import (
    backup_profile_folder,
    load_env_file,
    parse_cookie_string,
    select_working_proxy,
)

# Đường dẫn mặc định tới file thực thi của Chrome trên Windows
DEFAULT_CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

def _wait_for_port(port: int, timeout: int = 15) -> bool:
    """Hàm phụ trợ: Chờ cho đến khi port của Chrome được mở thành công."""
    import socket
    start_time = time.time()
    while time.time() - start_time < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            res = sock.connect_ex(('127.0.0.1', port))
            if res == 0:
                return True
        time.sleep(0.5)
    return False

def create_local_driver(
    profile_path: str,
    port: int,
    headless: bool = False,
    chrome_binary_path: Optional[str] = None,
    app_mode: bool = False,
    proxy: Optional[str] = None,
    user_agent: Optional[str] = None
) -> webdriver.Chrome:
    """Khởi tạo Chrome Driver bằng cách gọi process thật và attach Selenium qua cổng Debug."""
    
    actual_chrome_path = chrome_binary_path if chrome_binary_path else DEFAULT_CHROME_PATH
    options = Options()
    
    # 1. Xây dựng lệnh CMD để mở Chrome với các tham số cần thiết
    cmd = [
        actual_chrome_path,
        f"--user-data-dir={profile_path}",
        f"--remote-debugging-port={port}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-popup-blocking",
        "--disable-infobars"
    ]
    
    if headless:
        cmd.append("--headless=new")
        cmd.append("--disable-gpu")
        
    if app_mode:
        cmd.append("--app=https://www.facebook.com")
        cmd.append("--force-device-scale-factor=0.75")

    # Tích hợp Proxy và User Agent trực tiếp vào lệnh khởi động Chrome process
    if proxy and proxy.strip():
        cmd.append(f"--proxy-server={proxy.strip()}")
    if user_agent and user_agent.strip():
        cmd.append(f"--user-agent={user_agent.strip()}")

    print(f"[DRIVER] Mở Chrome tại Port {port} | Proxy: {'Có' if proxy else 'Không'}")
    
    # 2. Gọi process Chrome chạy độc lập
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        print(f"❌ Lỗi: Không tìm thấy file Chrome tại {actual_chrome_path}")
        raise

    # Đợi Chrome mở xong cổng debug
    if not _wait_for_port(port):
        raise Exception(f"Cổng {port} không mở được (Timeout). Trình duyệt có thể đã bị crash hoặc đang bị khóa bởi tiến trình khác.")

    # 3. Kết nối Selenium vào trình duyệt đã mở
    options.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
    driver = webdriver.Chrome(options=options)
    
    return driver

def login_facebook_with_cookies(driver: webdriver.Chrome, cookies_raw: str) -> bool:
    if not cookies_raw:
        raise ValueError("COOKIES trống. Vui lòng thiết lập COOKIES trong file .env")

    cookies = parse_cookie_string(cookies_raw)
    if not cookies:
        raise ValueError("Không thể parse được COOKIES. Hãy kiểm tra lại định dạng trong .env")

    driver.get("https://www.facebook.com/")
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )

    for cookie in cookies:
        payload = {
            "name": cookie["name"],
            "value": cookie["value"],
            "domain": ".facebook.com",
            "path": "/",
        }
        try:
            driver.add_cookie(payload)
        except Exception:
            continue

    return verify_facebook_login_state(driver)

def verify_facebook_login_state(driver: webdriver.Chrome) -> bool:
    driver.get("https://www.facebook.com/")
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    time.sleep(2)

    current_url = driver.current_url.lower()
    page_source = driver.page_source.lower()
    login_form_present = 'name="email"' in page_source and 'name="pass"' in page_source
    
    return ("login" not in current_url) and (not login_form_present)

def get_facebook_login_debug_state(driver: webdriver.Chrome) -> str:
    current_url = driver.current_url
    title = (driver.title or "").strip()
    page_source = driver.page_source.lower()
    hostname = urlparse(current_url).netloc or "unknown-host"

    hints: List[str] = []
    if "checkpoint" in current_url.lower():
        hints.append("checkpoint")
    if "login" in current_url.lower():
        hints.append("login")
    if 'name="email"' in page_source and 'name="pass"' in page_source:
        hints.append("login_form")
    if "suspicious" in page_source or "unusual" in page_source:
        hints.append("security_check")

    hint_text = ",".join(hints) if hints else "unknown"
    return f"url={current_url} host={hostname} title={title!r} hints={hint_text}"

def main() -> None:
    env = load_env_file(".env")
    cookies_raw = env.get("COOKIES", "")
    user_agent = env.get("USER_AGENT", "")
    login_method = env.get("LOGIN_METHOD", "cookies").strip().lower()
    profile_dir = env.get("PROFILE_DIR", "").strip() or os.path.join(
        os.getcwd(), "chrome_profile"
    )
    proxies_file = env.get("PROXIES_FILE", "proxies.txt").strip() or "proxies.txt"
    proxy = select_working_proxy(env.get("PROXY"), proxies_file)
    
    # Tạo thư mục profile nếu chưa có
    os.makedirs(profile_dir, exist_ok=True)
    abs_profile_dir = os.path.abspath(profile_dir)

    # Chọn một port để Debugger nối vào (bạn có thể đổi số này nếu bị trùng)
    DEBUG_PORT = 9222

    # Gọi hàm khởi tạo Driver mới
    driver = create_local_driver(
        profile_path=abs_profile_dir,
        port=DEBUG_PORT,
        headless=False,
        proxy=proxy or None,
        user_agent=user_agent or None
    )
    
    try:
        if login_method == "cookies":
            ok = login_facebook_with_cookies(driver, cookies_raw)
            if ok:
                print("Facebook login by cookies: SUCCESS")
                try:
                    archive_path = backup_profile_folder(abs_profile_dir)
                    print(f"Profile folder saved to: {archive_path}")
                except Exception as err:
                    print(f"Failed to backup profile folder: {err}")
            else:
                print("Facebook login by cookies: FAILED")
                print("Debug info:", get_facebook_login_debug_state(driver))
                
        elif login_method == "profile":
            ok = verify_facebook_login_state(driver)
            if ok:
                print("Facebook login via profile folder: SUCCESS")
            else:
                print("Facebook login via profile folder: FAILED")
                print("Debug info:", get_facebook_login_debug_state(driver))
        else:
            raise ValueError(
                "LOGIN_METHOD must be either 'cookies' or 'profile' (default 'cookies')."
            )

        input("Nhấn Enter để đóng trình duyệt...")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()