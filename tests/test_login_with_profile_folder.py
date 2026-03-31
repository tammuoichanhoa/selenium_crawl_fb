import argparse
import json
import logging
import os
import time
from typing import List, Optional

from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import InvalidCookieDomainException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


FACEBOOK_URL = "https://www.facebook.com/"
DEFAULT_PROFILE_PATH = "chrome_profiles/facebook"
DEFAULT_PROFILE_NAME = "Default"
DEFAULT_COOKIE_FILE = "cookies.txt"
LOG_FILE = "login_test.log"


logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Lan dau dang nhap bang COOKIES va luu Chrome profile. "
            "Nhung lan sau dung lai profile bang duong dan truyen vao."
        )
    )
    parser.add_argument(
        "--profile-path",
        default=os.getenv("PROFILE_PATH", DEFAULT_PROFILE_PATH),
        help=(
            "Duong dan user data dir cua Chrome. "
            "Neu folder da ton tai, script se uu tien dang nhap bang profile nay."
        ),
    )
    parser.add_argument(
        "--profile-name",
        default=os.getenv("PROFILE_NAME", DEFAULT_PROFILE_NAME),
        help="Ten profile ben trong user data dir. Mac dinh la Default.",
    )
    parser.add_argument(
        "--user-agent",
        default=os.getenv("USER_AGENT") or None,
        help="User-Agent tuy chon cho Chrome.",
    )
    parser.add_argument(
        "--proxy",
        default=os.getenv("PROXY") or None,
        help="Proxy tuy chon, vi du http://host:port.",
    )
    parser.add_argument(
        "--cookies-file",
        default=os.getenv("COOKIES_FILE", DEFAULT_COOKIE_FILE),
        help="Duong dan file cookies export dang JSON tu browser/extension.",
    )
    return parser.parse_args()


def parse_cookie_header(cookie_header: str) -> List[dict]:
    cookies = []
    for raw_item in cookie_header.split(";"):
        item = raw_item.strip()
        if not item or "=" not in item:
            continue

        name, value = item.split("=", 1)
        cookies.append(
            {
                "name": name.strip(),
                "value": value.strip(),
                "domain": ".facebook.com",
                "path": "/",
            }
        )

    return cookies


def parse_cookie_file(cookie_file: str) -> List[dict]:
    if not os.path.exists(cookie_file):
        raise RuntimeError(f"Missing cookies file: {cookie_file}")

    with open(cookie_file, "r", encoding="utf-8") as file:
        raw_cookies = json.load(file)

    if not isinstance(raw_cookies, list):
        raise RuntimeError("cookies file must contain a JSON array")

    cookies = []
    for item in raw_cookies:
        if not isinstance(item, dict):
            continue

        name = item.get("name")
        value = item.get("value")
        if not name or value is None:
            continue

        cookie = {
            "name": name,
            "value": str(value),
            "domain": item.get("domain", ".facebook.com"),
            "path": item.get("path", "/"),
        }

        if "secure" in item:
            cookie["secure"] = bool(item["secure"])

        if "httpOnly" in item:
            cookie["httpOnly"] = bool(item["httpOnly"])

        expiry = item.get("expirationDate")
        if expiry and not item.get("session", False):
            cookie["expiry"] = int(expiry)

        cookies.append(cookie)

    if not cookies:
        raise RuntimeError("cookies file exists but no valid cookies were found")

    return cookies


def wait_for_page_ready(driver: webdriver.Chrome) -> None:
    WebDriverWait(driver, 20).until(
        lambda current_driver: current_driver.execute_script("return document.readyState")
        == "complete"
    )


def ensure_profile_path(profile_path: str) -> str:
    absolute_path = os.path.abspath(profile_path)
    os.makedirs(absolute_path, exist_ok=True)
    return absolute_path


def profile_has_data(profile_path: str) -> bool:
    if not os.path.isdir(profile_path):
        return False

    return any(os.scandir(profile_path))


def build_driver(
    profile_path: str,
    profile_name: str,
    user_agent: Optional[str] = None,
    proxy: Optional[str] = None,
) -> webdriver.Chrome:
    options = Options()
    if os.getenv("HEADLESS", "false").lower() == "true":
        options.add_argument("--headless=new")

    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--start-maximized")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument(f"--user-data-dir={profile_path}")
    options.add_argument(f"--profile-directory={profile_name}")

    if user_agent:
        options.add_argument(f"--user-agent={user_agent}")

    if proxy:
        options.add_argument(f"--proxy-server={proxy}")

    chrome_binary = os.getenv("CHROME_BINARY")
    chromedriver_path = os.getenv("CHROMEDRIVER_PATH")

    if chrome_binary:
        options.binary_location = chrome_binary

    if chromedriver_path:
        return webdriver.Chrome(service=Service(chromedriver_path), options=options)

    return webdriver.Chrome(options=options)


def open_facebook(driver: webdriver.Chrome) -> None:
    driver.get(FACEBOOK_URL)
    wait_for_page_ready(driver)
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))


def login_with_cookies(driver: webdriver.Chrome, cookies: List[dict]) -> None:
    open_facebook(driver)

    for cookie in cookies:
        try:
            driver.add_cookie(cookie)
        except InvalidCookieDomainException:
            fallback_cookie = {key: value for key, value in cookie.items() if key != "domain"}
            driver.add_cookie(fallback_cookie)

    driver.get(FACEBOOK_URL)
    wait_for_page_ready(driver)


def is_logged_in(driver: webdriver.Chrome) -> bool:
    time.sleep(3)
    current_url = driver.current_url.lower()
    if "login" in current_url or "checkpoint" in current_url:
        return False

    login_fields = driver.find_elements(By.NAME, "email")
    return len(login_fields) == 0


def load_cookies(cookie_file: Optional[str]) -> List[dict]:
    if cookie_file and os.path.exists(cookie_file):
        logging.info("Loading cookies from file: %s", cookie_file)
        return parse_cookie_file(cookie_file)

    cookie_header = os.getenv("COOKIES", "").strip()
    if not cookie_header:
        raise RuntimeError("Missing cookies source: set COOKIES or provide --cookies-file")

    cookies = parse_cookie_header(cookie_header)
    if not cookies:
        raise RuntimeError("COOKIES exists but could not be parsed")

    return cookies


def try_login_with_existing_profile(
    profile_path: str,
    profile_name: str,
    user_agent: Optional[str],
    proxy: Optional[str],
) -> bool:
    logging.info("Trying existing profile: %s | profile=%s", profile_path, profile_name)
    print(f"Using saved profile: {profile_path} [{profile_name}]")

    driver = build_driver(profile_path, profile_name, user_agent=user_agent, proxy=proxy)
    try:
        open_facebook(driver)
        success = is_logged_in(driver)
        if success:
            logging.info("SUCCESS | Existing profile login")
            print("  -> Logged in by saved profile")
        else:
            logging.warning("FAILED | Existing profile is not logged in")
            print("  -> Saved profile is not logged in")
        return success
    finally:
        driver.quit()


def login_and_persist_profile(
    profile_path: str,
    profile_name: str,
    user_agent: Optional[str],
    proxy: Optional[str],
    cookies: List[dict],
) -> bool:
    logging.info("Trying cookie login and persisting profile: %s | profile=%s", profile_path, profile_name)
    print(f"Creating or updating profile: {profile_path} [{profile_name}]")

    driver = build_driver(profile_path, profile_name, user_agent=user_agent, proxy=proxy)
    try:
        login_with_cookies(driver, cookies)
        success = is_logged_in(driver)

        if success:
            logging.info("SUCCESS | Cookie login persisted to profile")
            print("  -> Cookie login successful")
            # Give Chrome a moment to flush session data into the profile folder.
            time.sleep(5)
        else:
            logging.warning("FAILED | Cookie login did not create a valid session")
            print("  -> Cookie login failed")

        return success
    finally:
        driver.quit()


def main() -> None:
    load_dotenv()
    args = parse_args()

    profile_path = ensure_profile_path(args.profile_path)
    profile_exists = profile_has_data(profile_path)

    logging.info(
        "Start login flow | profile_path=%s | profile_name=%s | has_data=%s",
        profile_path,
        args.profile_name,
        profile_exists,
    )

    if profile_exists and try_login_with_existing_profile(
        profile_path,
        args.profile_name,
        args.user_agent,
        args.proxy,
    ):
        print(f"Profile ready: {profile_path}")
        print(f"Log file: {LOG_FILE}")
        return

    cookies = load_cookies(args.cookies_file)
    success = login_and_persist_profile(
        profile_path,
        args.profile_name,
        args.user_agent,
        args.proxy,
        cookies,
    )

    if not success:
        raise RuntimeError("Login failed with both saved profile and cookies")

    print(f"Profile saved to: {profile_path}")
    print("Next runs can reuse this path with --profile-path")
    print(f"Log file: {LOG_FILE}")


if __name__ == "__main__":
    main()
