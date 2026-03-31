import os
import time
import logging
from typing import List

from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import InvalidCookieDomainException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


FACEBOOK_URL = "https://www.facebook.com/"
USER_AGENT_FILE = "user_agents.txt"
LOG_FILE = "login_test.log"


logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


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


def load_user_agents(file_path: str) -> List[str]:
    if not os.path.exists(file_path):
        raise RuntimeError(f"Missing user-agent file: {file_path}")

    user_agents = []
    with open(file_path, "r", encoding="utf-8") as file:
        for line in file:
            value = line.strip()
            if value and not value.startswith("#"):
                user_agents.append(value)

    if not user_agents:
        raise RuntimeError(f"No user agents found in {file_path}")

    return user_agents


def build_driver(user_agent: str) -> webdriver.Chrome:
    options = Options()
    if os.getenv("HEADLESS", "false").lower() == "true":
        options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--start-maximized")
    options.add_argument(f"--user-agent={user_agent}")

    chrome_binary = os.getenv("CHROME_BINARY")
    chromedriver_path = os.getenv("CHROMEDRIVER_PATH")

    if chrome_binary:
        options.binary_location = chrome_binary

    if chromedriver_path:
        return webdriver.Chrome(service=Service(chromedriver_path), options=options)

    return webdriver.Chrome(options=options)


def login_with_cookies(driver: webdriver.Chrome, cookies: List[dict]) -> None:
    driver.get(FACEBOOK_URL)
    WebDriverWait(driver, 20).until(
        lambda current_driver: current_driver.execute_script("return document.readyState")
        == "complete"
    )

    for cookie in cookies:
        try:
            driver.add_cookie(cookie)
        except InvalidCookieDomainException:
            fallback_cookie = {key: value for key, value in cookie.items() if key != "domain"}
            driver.add_cookie(fallback_cookie)

    driver.get(FACEBOOK_URL)


def is_logged_in(driver: webdriver.Chrome) -> bool:
    time.sleep(3)
    current_url = driver.current_url.lower()
    if "login" in current_url:
        return False

    login_fields = driver.find_elements(By.NAME, "email")
    return len(login_fields) == 0


def test_user_agent(user_agent: str, cookies: List[dict], index: int, total: int) -> bool:
    logging.info("Starting test %s/%s with user-agent: %s", index, total, user_agent)
    print(f"[{index}/{total}] Testing user-agent: {user_agent}")

    driver = build_driver(user_agent)
    try:
        login_with_cookies(driver, cookies)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        success = is_logged_in(driver)
        if success:
            logging.info("SUCCESS | %s", user_agent)
            print("  -> Success")
        else:
            logging.warning("FAILED | %s", user_agent)
            print("  -> Failed")

        return success
    except Exception as exc:
        logging.exception("ERROR | %s | %s", user_agent, exc)
        print(f"  -> Error: {exc}")
        return False
    finally:
        driver.quit()


def main() -> None:
    load_dotenv()
    cookie_header = os.getenv("COOKIES", "").strip()
    if not cookie_header:
        raise RuntimeError("Missing COOKIES in .env")

    cookies = parse_cookie_header(cookie_header)
    if not cookies:
        raise RuntimeError("COOKIES exists but could not be parsed")

    user_agents = load_user_agents(USER_AGENT_FILE)
    success_count = 0

    for index, user_agent in enumerate(user_agents, start=1):
        if test_user_agent(user_agent, cookies, index, len(user_agents)):
            success_count += 1

    summary = f"Completed {len(user_agents)} tests. Success: {success_count}, Failed: {len(user_agents) - success_count}"
    logging.info(summary)
    print(summary)
    print(f"Log file: {LOG_FILE}")


if __name__ == "__main__":
    main()
