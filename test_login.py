import argparse
import json
import logging
import os
import time
from dataclasses import dataclass
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
DEFAULT_USER_AGENT_FILE = "user_agents.txt"
DEFAULT_PROXY_FILE = "proxies.txt"
LOG_FILE = "login_test.log"


logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


@dataclass(frozen=True)
class LoginAttempt:
    user_agent: Optional[str]
    proxy: Optional[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Test dang nhap Facebook bang profile folder, cookies, user-agent va proxy "
            "trong cung mot script."
        )
    )
    parser.add_argument(
        "--profile-path",
        default=os.getenv("PROFILE_PATH", DEFAULT_PROFILE_PATH),
        help="Duong dan user data dir cua Chrome.",
    )
    parser.add_argument(
        "--profile-name",
        default=os.getenv("PROFILE_NAME", DEFAULT_PROFILE_NAME),
        help="Ten profile ben trong user data dir. Mac dinh la Default.",
    )
    parser.add_argument(
        "--cookies-file",
        default=os.getenv("COOKIES_FILE", DEFAULT_COOKIE_FILE),
        help="File cookies JSON export tu browser/extension.",
    )
    parser.add_argument(
        "--user-agent",
        default=os.getenv("USER_AGENT") or None,
        help="User-Agent don le. Neu co, se uu tien hon user_agents.txt.",
    )
    parser.add_argument(
        "--proxy",
        default=os.getenv("PROXY") or None,
        help="Proxy don le, vi du http://host:port. Neu co, se uu tien hon proxies.txt.",
    )
    parser.add_argument(
        "--user-agents-file",
        default=os.getenv("USER_AGENT_FILE", DEFAULT_USER_AGENT_FILE),
        help="File danh sach user-agent.",
    )
    parser.add_argument(
        "--proxies-file",
        default=os.getenv("PROXY_FILE", DEFAULT_PROXY_FILE),
        help="File danh sach proxy.",
    )
    parser.add_argument(
        "--continue-after-success",
        action="store_true",
        help="Khong dung sau khi tim thay lan dang nhap thanh cong dau tien.",
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
            "name": str(name),
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


def load_non_comment_lines(file_path: str, required: bool) -> List[str]:
    if not os.path.exists(file_path):
        if required:
            raise RuntimeError(f"Missing file: {file_path}")
        return []

    values = []
    with open(file_path, "r", encoding="utf-8") as file:
        for line in file:
            value = line.strip()
            if value and not value.startswith("#"):
                values.append(value)

    if required and not values:
        raise RuntimeError(f"No usable values found in {file_path}")

    return values


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


def build_attempts(args: argparse.Namespace) -> List[LoginAttempt]:
    if args.user_agent:
        user_agents = [args.user_agent]
    else:
        user_agents = load_non_comment_lines(args.user_agents_file, required=False)

    if args.proxy:
        proxies = [args.proxy]
    else:
        proxies = load_non_comment_lines(args.proxies_file, required=False)

    if proxies:
        if not user_agents:
            user_agents = [None]

        return [
            LoginAttempt(
                user_agent=user_agents[index % len(user_agents)],
                proxy=proxy,
            )
            for index, proxy in enumerate(proxies)
        ]

    if user_agents:
        return [LoginAttempt(user_agent=user_agent, proxy=None) for user_agent in user_agents]

    return [LoginAttempt(user_agent=None, proxy=None)]


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
    user_agent: Optional[str],
    proxy: Optional[str],
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
    page_title = (driver.title or "").lower()
    if "login" in current_url or "checkpoint" in current_url:
        return False

    if "log in" in page_title or "sign up" in page_title:
        return False

    return driver.get_cookie("c_user") is not None


def describe_attempt(attempt: LoginAttempt) -> str:
    user_agent = attempt.user_agent or "default-browser-user-agent"
    proxy = attempt.proxy or "no-proxy"
    return f"proxy={proxy} | user-agent={user_agent}"


def try_login_with_existing_profile(
    profile_path: str,
    profile_name: str,
    attempt: LoginAttempt,
    index: int,
    total: int,
) -> bool:
    description = describe_attempt(attempt)
    logging.info(
        "Trying existing profile %s/%s | profile_path=%s | profile_name=%s | %s",
        index,
        total,
        profile_path,
        profile_name,
        description,
    )
    print(f"[{index}/{total}] Reusing profile -> {description}")

    driver = None
    try:
        driver = build_driver(
            profile_path=profile_path,
            profile_name=profile_name,
            user_agent=attempt.user_agent,
            proxy=attempt.proxy,
        )
        open_facebook(driver)
        success = is_logged_in(driver)
        if success:
            logging.info("SUCCESS | Existing profile | %s", description)
            print("  -> Logged in by saved profile")
        else:
            logging.warning("FAILED | Existing profile not logged in | %s", description)
            print("  -> Saved profile is not logged in")
        return success
    except Exception as exc:
        logging.exception("ERROR | Existing profile | %s | %s", description, exc)
        print(f"  -> Error: {exc}")
        return False
    finally:
        if driver:
            driver.quit()


def login_and_persist_profile(
    profile_path: str,
    profile_name: str,
    attempt: LoginAttempt,
    cookies: List[dict],
    index: int,
    total: int,
) -> bool:
    description = describe_attempt(attempt)
    logging.info(
        "Trying cookie login %s/%s | profile_path=%s | profile_name=%s | %s",
        index,
        total,
        profile_path,
        profile_name,
        description,
    )
    print(f"[{index}/{total}] Cookie login -> {description}")

    driver = None
    try:
        driver = build_driver(
            profile_path=profile_path,
            profile_name=profile_name,
            user_agent=attempt.user_agent,
            proxy=attempt.proxy,
        )
        login_with_cookies(driver, cookies)
        success = is_logged_in(driver)

        if success:
            logging.info("SUCCESS | Cookie login persisted | %s", description)
            print("  -> Cookie login successful")
            time.sleep(5)
        else:
            logging.warning("FAILED | Cookie login did not create session | %s", description)
            print("  -> Cookie login failed")

        return success
    except Exception as exc:
        logging.exception("ERROR | Cookie login | %s | %s", description, exc)
        print(f"  -> Error: {exc}")
        return False
    finally:
        if driver:
            driver.quit()


def create_authenticated_driver(
    profile_path: str,
    profile_name: str,
    attempt: LoginAttempt,
    cookies: List[dict],
    reuse_profile: bool,
) -> webdriver.Chrome:
    description = describe_attempt(attempt)
    driver = build_driver(
        profile_path=profile_path,
        profile_name=profile_name,
        user_agent=attempt.user_agent,
        proxy=attempt.proxy,
    )

    try:
        if reuse_profile:
            logging.info(
                "Checking existing profile before cookie login | profile_path=%s | profile_name=%s | %s",
                profile_path,
                profile_name,
                description,
            )
            open_facebook(driver)
            if is_logged_in(driver):
                logging.info("SUCCESS | Existing profile reused in active driver | %s", description)
                return driver

            logging.warning("Existing profile is not logged in, falling back to cookies | %s", description)

        login_with_cookies(driver, cookies)
        if is_logged_in(driver):
            logging.info("SUCCESS | Cookie login in active driver | %s", description)
            return driver

        raise RuntimeError(f"Login failed for attempt: {description}")
    except Exception:
        driver.quit()
        raise


def open_first_authenticated_driver(
    profile_path: str,
    profile_name: str,
    attempts: List[LoginAttempt],
    cookies: List[dict],
) -> tuple[webdriver.Chrome, str]:
    profile_exists = profile_has_data(profile_path)
    last_error = None

    for index, attempt in enumerate(attempts, start=1):
        description = describe_attempt(attempt)
        logging.info(
            "Opening authenticated driver %s/%s | profile_path=%s | profile_name=%s | %s",
            index,
            len(attempts),
            profile_path,
            profile_name,
            description,
        )
        print(f"[{index}/{len(attempts)}] Open authenticated driver -> {description}")

        try:
            driver = create_authenticated_driver(
                profile_path=profile_path,
                profile_name=profile_name,
                attempt=attempt,
                cookies=cookies,
                reuse_profile=profile_exists,
            )
            return driver, description
        except Exception as exc:
            last_error = exc
            logging.exception("ERROR | Unable to open authenticated driver | %s | %s", description, exc)
            print(f"  -> Error: {exc}")

    summary = f"No successful login found after {len(attempts)} attempt(s)"
    logging.error(summary)
    raise RuntimeError(summary) from last_error


def main() -> None:
    load_dotenv()
    args = parse_args()

    profile_path = ensure_profile_path(args.profile_path)
    attempts = build_attempts(args)
    cookies = load_cookies(args.cookies_file)
    profile_exists = profile_has_data(profile_path)

    logging.info(
        "Start login flow | profile_path=%s | profile_name=%s | attempts=%s | profile_has_data=%s",
        profile_path,
        args.profile_name,
        len(attempts),
        profile_exists,
    )

    success_count = 0
    first_success_description = None

    for index, attempt in enumerate(attempts, start=1):
        success = False

        if profile_exists:
            success = try_login_with_existing_profile(
                profile_path=profile_path,
                profile_name=args.profile_name,
                attempt=attempt,
                index=index,
                total=len(attempts),
            )

        if not success:
            success = login_and_persist_profile(
                profile_path=profile_path,
                profile_name=args.profile_name,
                attempt=attempt,
                cookies=cookies,
                index=index,
                total=len(attempts),
            )

        if success:
            success_count += 1
            profile_exists = True
            first_success_description = describe_attempt(attempt)
            if not args.continue_after_success:
                break

    if success_count == 0:
        summary = f"No successful login found after {len(attempts)} attempt(s)"
        logging.error(summary)
        print(summary)
        print(f"Log file: {LOG_FILE}")
        raise RuntimeError(summary)

    if args.continue_after_success:
        summary = (
            f"Completed {len(attempts)} attempt(s). Success: {success_count}, "
            f"Failed: {len(attempts) - success_count}"
        )
    else:
        summary = f"Found successful login: {first_success_description}"

    logging.info(summary)
    print(summary)
    print(f"Profile saved to: {profile_path}")
    print(f"Log file: {LOG_FILE}")


if __name__ == "__main__":
    main()
