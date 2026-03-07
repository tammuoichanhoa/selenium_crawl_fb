from __future__ import annotations

import shutil
import subprocess
import sys
import time
from typing import List, Optional
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from .cookies import parse_cookie_string
from .waits import wait_for_page_ready, wait_for_seconds

DEFAULT_CHROME_PATH_WIN = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
LINUX_CHROME_CANDIDATES = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
]


def _resolve_chrome_path(explicit_path: Optional[str] = None) -> str:
    """Tìm đường dẫn Chrome phù hợp trên Windows/Linux."""
    if explicit_path:
        return explicit_path
    print("Running on ", sys.platform.__str__())
    if sys.platform.startswith("win"):
        return DEFAULT_CHROME_PATH_WIN

    if sys.platform.startswith("linux"):
        for name in LINUX_CHROME_CANDIDATES:
            found = shutil.which(name)
            if found:
                return found

    raise FileNotFoundError(
        "Không tìm thấy Chrome/Chromium. Hãy truyền chrome_binary_path "
        "hoặc đặt CHROME_BINARY trong file .env."
    )


def _wait_for_port(port: int, timeout: int = 15) -> bool:
    """Hàm phụ trợ: Chờ cho đến khi port của Chrome được mở thành công."""
    import socket

    start_time = time.time()
    while time.time() - start_time < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            res = sock.connect_ex(("127.0.0.1", port))
            if res == 0:
                return True
        time.sleep(0.5)
    return False


def _terminate_process(proc: subprocess.Popen, timeout: int = 5) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=timeout)


def terminate_chrome_process(driver: webdriver.Chrome, timeout: int = 5) -> None:
    proc = getattr(driver, "_chrome_process", None)
    if proc is None:
        return
    try:
        _terminate_process(proc, timeout=timeout)
    except Exception:
        pass


def create_local_driver(
    profile_path: str,
    port: int,
    headless: bool = False,
    chrome_binary_path: Optional[str] = None,
    app_mode: bool = False,
    proxy: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> webdriver.Chrome:
    """Khởi tạo Chrome Driver bằng cách gọi process thật và attach Selenium qua cổng Debug."""

    actual_chrome_path = _resolve_chrome_path(chrome_binary_path)
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
        "--disable-infobars",
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
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        print(f"❌ Lỗi: Không tìm thấy file Chrome tại {actual_chrome_path}")
        raise

    # Đợi Chrome mở xong cổng debug
    '''
    @anhtb
    Khi Selenium tự khởi tạo ChromeDriver, trình duyệt thường có nhiều dấu hiệu “automation”.
    Attach qua debug port có thể ít bị phát hiện hơn trong một số trường hợp.
    '''
    if not _wait_for_port(port):
        _terminate_process(proc)
        raise Exception(
            f"Cổng {port} không mở được (Timeout). "
            "Trình duyệt có thể đã bị crash hoặc đang bị khóa bởi tiến trình khác."
        )

    # 3. Kết nối Selenium vào trình duyệt đã mở
    options.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
    try:
        driver = webdriver.Chrome(options=options)
    except Exception:
        _terminate_process(proc)
        raise
    driver._chrome_process = proc

    return driver


def login_facebook_with_cookies(driver: webdriver.Chrome, cookies_raw: str) -> bool:
    if not cookies_raw:
        raise ValueError("COOKIES trống. Vui lòng thiết lập COOKIES trong file .env")

    cookies = parse_cookie_string(cookies_raw)
    if not cookies:
        raise ValueError("Không thể parse được COOKIES. Hãy kiểm tra lại định dạng trong .env")

    driver.get("https://www.facebook.com/")
    wait_for_page_ready(driver, 20)

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
    wait_for_page_ready(driver, 20)
    wait_for_seconds(driver, 2)

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


def create_logged_in_driver(
    login_method: str,
    cookies_raw: str,
    user_agent: str,
    headless: bool,
    profile_dir: str,
    proxy: str | None,
    chrome_binary: str | None,
    debug_port: int,
):
    driver = create_local_driver(
        profile_path=profile_dir,
        port=debug_port,
        headless=headless,
        chrome_binary_path=chrome_binary,
        proxy=proxy,
        user_agent=user_agent,
    )

    try:
        if login_method == "cookies":
            ok = login_facebook_with_cookies(driver, cookies_raw)
        elif login_method == "profile":
            ok = verify_facebook_login_state(driver)
        else:
            raise ValueError("LOGIN_METHOD must be either 'cookies' or 'profile'.")

        if not ok:
            debug_state = get_facebook_login_debug_state(driver)
            raise RuntimeError(
                "Unable to verify Facebook login. "
                f"Facebook redirected the session: {debug_state}"
            )
        return driver
    except Exception:
        driver.quit()
        terminate_chrome_process(driver)
        raise
