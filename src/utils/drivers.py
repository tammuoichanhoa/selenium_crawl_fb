"""Chrome/Selenium driver utilities for local debug attachment and login."""

from __future__ import annotations

import os  # process inspection
import shutil  # find Chrome binary on PATH
import signal  # terminate/kill by pid
import subprocess  # spawn/terminate Chrome process
import sys  # platform detection
import time  # retry/sleep timing
import logging
from typing import Iterable, List, Optional, Tuple  # type hints
from urllib.parse import urlparse  # parse URLs for logging/host extraction

from selenium import webdriver  # Selenium driver classes
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options  # Chrome option builder
from .cookies import parse_cookie_string  # cookie header parsing
from .profile_backup import backup_profile_folder
from .waits import wait_for_page_ready, wait_for_seconds  # explicit wait helpers

logger = logging.getLogger(__name__)

DEFAULT_CHROME_PATH_WIN = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEFAULT_FB_HOME_URL = "https://www.facebook.com/"
DEFAULT_FB_LOCALE_URL = "https://www.facebook.com/?locale=en_EN"
DEFAULT_LINUX_CHROME_CANDIDATES = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
]

def _resolve_chrome_path(
    explicit_path: Optional[str] = None,
    win_default_path: Optional[str] = None,
    linux_candidates: Optional[List[str]] = None,
) -> str:
    """Resolve Chrome/Chromium binary path for the current OS."""
    # Tìm đường dẫn Chrome phù hợp trên Windows/Linux.
    if explicit_path:
        return explicit_path
    logger.debug("Running on %s", sys.platform.__str__())
    if sys.platform.startswith("win"):
        return win_default_path or DEFAULT_CHROME_PATH_WIN

    if sys.platform.startswith("linux"):
        candidates = linux_candidates or DEFAULT_LINUX_CHROME_CANDIDATES
        for name in candidates:
            found = shutil.which(name)
            if found:
                return found

    raise FileNotFoundError(
        "Không tìm thấy Chrome/Chromium. Hãy truyền chrome_binary_path "
        "hoặc đặt CHROME_BINARY trong file .env."
    )


def _wait_for_port(port: int, timeout: int = 15) -> bool:
    """Wait until the given localhost TCP port is accepting connections."""
    # Hàm phụ trợ: Chờ cho đến khi port của Chrome được mở thành công.
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
    """Try to terminate a subprocess gracefully, then kill on timeout."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=timeout)


def _iter_process_cmdlines() -> Iterable[Tuple[int, str]]:
    """Yield (pid, cmdline) for running processes, best-effort across platforms."""
    if sys.platform.startswith("linux"):
        for pid_str in os.listdir("/proc"):
            if not pid_str.isdigit():
                continue
            pid = int(pid_str)
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as handle:
                    raw = handle.read()
                if not raw:
                    continue
                cmdline = raw.replace(b"\x00", b" ").decode(errors="ignore").strip()
                if cmdline:
                    yield pid, cmdline
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
        return

    if sys.platform.startswith("darwin"):
        try:
            out = subprocess.check_output(
                ["ps", "-axo", "pid=,command="],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            pid_str, cmdline = parts
            if pid_str.isdigit():
                yield int(pid_str), cmdline
        return

    if sys.platform.startswith("win"):
        try:
            out = subprocess.check_output(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        "Get-CimInstance Win32_Process | "
                        "Select-Object ProcessId,CommandLine | "
                        "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"
                    ),
                ],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            pid_str, cmdline = parts
            if pid_str.isdigit():
                yield int(pid_str), cmdline
        return


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_pids(pids: Iterable[int], timeout: int = 5) -> None:
    pids = [pid for pid in pids if isinstance(pid, int) and pid > 0]
    if not pids:
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            continue

    deadline = time.time() + timeout
    remaining = {pid for pid in pids if _pid_exists(pid)}
    while remaining and time.time() < deadline:
        remaining = {pid for pid in remaining if _pid_exists(pid)}
        if remaining:
            time.sleep(0.2)

    if not remaining:
        return
    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            continue


def _find_chrome_pids(profile_path: Optional[str], port: Optional[int]) -> List[int]:
    """Find Chrome PIDs that belong to this crawler based on unique flags."""
    markers: List[str] = []
    if profile_path:
        markers.append(f"--user-data-dir={profile_path}")
    if port is not None:
        markers.append(f"--remote-debugging-port={port}")
    if not markers:
        return []

    pids: List[int] = []
    for pid, cmdline in _iter_process_cmdlines():
        cmd_lower = cmdline.lower()
        if "chrome" not in cmd_lower and "chromium" not in cmd_lower:
            continue
        if all(marker in cmdline for marker in markers):
            pids.append(pid)
    return pids


def terminate_chrome_process(driver: webdriver.Chrome, timeout: int = 5) -> None:
    """Terminate the Chrome process attached to a Selenium driver."""
    proc = getattr(driver, "_chrome_process", None)
    profile_path = getattr(driver, "_chrome_profile_path", None)
    port = getattr(driver, "_chrome_debug_port", None)
    pid = getattr(driver, "_chrome_pid", None)
    try:
        if proc is not None:
            _terminate_process(proc, timeout=timeout)
            return
    except Exception:
        pass

    # Fallback: find and terminate Chrome by unique crawler flags.
    if pid:
        _terminate_pids([pid], timeout=timeout)
        return
    pids = _find_chrome_pids(profile_path, port)
    _terminate_pids(pids, timeout=timeout)


def create_local_driver(
    profile_path: str,
    port: int,
    headless: bool = False,
    chrome_binary_path: Optional[str] = None,
    chrome_binary_win_path: Optional[str] = None,
    chrome_binary_candidates: Optional[List[str]] = None,
    app_mode: bool = False,
    proxy: Optional[str] = None,
    user_agent: Optional[str] = None,
    window_size: Optional[Tuple[int, int]] = None,
    window_position: Optional[Tuple[int, int]] = None,
    incognito: bool = False,
) -> webdriver.Chrome:
    """Khởi tạo Chrome Driver bằng cách gọi process thật và attach Selenium qua cổng Debug."""

    actual_chrome_path = _resolve_chrome_path(
        chrome_binary_path,
        win_default_path=chrome_binary_win_path,
        linux_candidates=chrome_binary_candidates,
    )
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
        "--lang=en-US",  # <--- THÊM DÒNG NÀY
    ]

    if headless:
        cmd.append("--headless=new")
        cmd.append("--disable-gpu")

    if incognito:
        cmd.append("--incognito")

    if app_mode:
        cmd.append("--app=https://www.facebook.com")
        cmd.append("--force-device-scale-factor=0.75")

    # Tích hợp Proxy và User Agent trực tiếp vào lệnh khởi động Chrome process
    if proxy and proxy.strip():
        cmd.append(f"--proxy-server={proxy.strip()}")
    if user_agent and user_agent.strip():
        cmd.append(f"--user-agent={user_agent.strip()}")
        
    if window_size:
        w, h = window_size
        print("w,h size",w, h)
        cmd.append(f"--window-size={w},{400}")
    if window_position:
        x, y = window_position
        print("x,y position",x, y)
        cmd.append(f"--window-position={x},{y}")

    logger.info(
        "[DRIVER] Mở Chrome tại Port %s | Proxy: %s",
        port,
        "Có" if proxy else "Không",
    )
    prefs = {
        "intl.accept_languages": "en-US,en",
    }
    options.add_experimental_option("prefs", prefs)
    # 2. Gọi process Chrome chạy độc lập
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        logger.error("Không tìm thấy file Chrome tại %s", actual_chrome_path)
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
    driver._chrome_profile_path = profile_path
    driver._chrome_debug_port = port
    driver._chrome_pid = proc.pid

    return driver


def login_facebook_with_cookies(
    driver: webdriver.Chrome,
    cookies_raw: str,
    home_url: str = DEFAULT_FB_HOME_URL,
) -> bool:
    """Navigate to Facebook and inject cookies to establish a session."""
    if not cookies_raw:
        raise ValueError("COOKIES trống. Vui lòng thiết lập COOKIES trong file .env")

    cookies = parse_cookie_string(cookies_raw)
    if not cookies:
        raise ValueError("Không thể parse được COOKIES. Hãy kiểm tra lại định dạng trong .env")

    driver.get(home_url)
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

    return verify_facebook_login_state(driver, home_url=home_url)


def verify_facebook_login_state(
    driver: webdriver.Chrome,
    home_url: str = DEFAULT_FB_HOME_URL,
) -> bool:
    """Check whether the Facebook session appears logged in."""
    driver.get(home_url)
    wait_for_page_ready(driver, 20)
    wait_for_seconds(driver, 2)

    current_url = driver.current_url.lower()
    page_source = driver.page_source.lower()
    login_form_present = 'name="email"' in page_source and 'name="pass"' in page_source

    return ("login" not in current_url) and (not login_form_present)


def get_facebook_login_debug_state(driver: webdriver.Chrome) -> str:
    """Build a compact debug string describing the login page state."""
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
    home_url: str = DEFAULT_FB_HOME_URL,
    locale_url: str = DEFAULT_FB_LOCALE_URL,
    chrome_binary_win_path: str | None = None,
    chrome_binary_candidates: List[str] | None = None,
    profile_backup_name: str | None = None,
    window_size: Optional[Tuple[int, int]] = None,
    window_position: Optional[Tuple[int, int]] = None,
):
    """Create a driver and verify login via cookies or profile."""
    driver = create_local_driver(
        profile_path=profile_dir,
        port=debug_port,
        headless=headless,
        chrome_binary_path=chrome_binary,
        chrome_binary_win_path=chrome_binary_win_path,
        chrome_binary_candidates=chrome_binary_candidates,
        proxy=proxy,
        user_agent=user_agent,
        window_size=window_size,
        window_position=window_position,
        incognito=(login_method in ("anonymous", "none", "no_login")),
    )

    try:
        if login_method == "cookies":
            ok = login_facebook_with_cookies(
                driver,
                cookies_raw,
                home_url=home_url,
            )
        elif login_method == "profile":
            ok = verify_facebook_login_state(driver, home_url=home_url)
        elif login_method in ("anonymous", "none", "no_login"):
            # Chế độ ẩn danh: không đăng nhập, chỉ mở browser thạo rồi trả về driver luôn
            logger.info("[DRIVER] Anonymous mode: bỏ qua login, không inject cookies.")
            return driver
        else:
            raise ValueError("LOGIN_METHOD must be 'cookies', 'profile', or 'anonymous'.")

        if not ok:
            debug_state = get_facebook_login_debug_state(driver)
            raise RuntimeError(
                "Unable to verify Facebook login. "
                f"Facebook redirected the session: {debug_state}"
            )
        if profile_backup_name:
            try:
                backup_profile_folder(profile_dir, archive_name=profile_backup_name)
            except Exception as exc:
                logger.warning("Failed to backup profile folder: %s", exc)
        if locale_url:
            driver.get(locale_url)
            wait_for_page_ready(driver, 20)
            wait_for_seconds(driver, 2)
        return driver
    except Exception:
        driver.quit()
        terminate_chrome_process(driver)
        raise
