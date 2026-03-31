import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import yaml
from dotenv import load_dotenv
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from tests.test_login import (
    DEFAULT_COOKIE_FILE,
    DEFAULT_PROFILE_NAME,
    DEFAULT_PROFILE_PATH,
    DEFAULT_PROXY_FILE,
    DEFAULT_USER_AGENT_FILE,
    build_attempts,
    ensure_profile_path,
    load_cookies,
    load_non_comment_lines,
    open_first_authenticated_driver,
    wait_for_page_ready,
)


DEFAULT_CONFIG_FILE = "configs/config.yml"
DEFAULT_PAGES_FILE = "data/pages.txt"
DEFAULT_OUTPUT_FILE = "data/pages_data.json"
DEFAULT_FIELD_TIMEOUT = 15
DEFAULT_PAGE_WAIT_SECONDS = 2.0
DRIVER_VALUE_ATTRIBUTES = {"driver_title", "current_url", "page_source"}

BY_MAP = {
    "css": By.CSS_SELECTOR,
    "css_selector": By.CSS_SELECTOR,
    "xpath": By.XPATH,
    "id": By.ID,
    "name": By.NAME,
    "class_name": By.CLASS_NAME,
    "tag_name": By.TAG_NAME,
    "link_text": By.LINK_TEXT,
    "partial_link_text": By.PARTIAL_LINK_TEXT,
}


@dataclass(frozen=True)
class FieldConfig:
    name: str
    by: str
    selector: Optional[str]
    attribute: str = "text"
    multiple: bool = False
    optional: bool = False
    wait_until: str = "presence"
    timeout: int = DEFAULT_FIELD_TIMEOUT
    strip: bool = True
    limit: Optional[int] = None
    default: Any = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dang nhap Facebook, crawl cac trang trong pages.txt va luu ket qua JSON."
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_FILE,
        help="File YAML cau hinh truong du lieu can crawl.",
    )
    parser.add_argument(
        "--pages-file",
        default=None,
        help="Ghi de file danh sach URL neu khong muon dung gia tri trong config.",
    )
    parser.add_argument(
        "--output-file",
        default=None,
        help="Ghi de file JSON dau ra neu khong muon dung gia tri trong config.",
    )
    parser.add_argument(
        "--profile-path",
        default=None,
        help="Duong dan user data dir cua Chrome.",
    )
    parser.add_argument(
        "--profile-name",
        default=None,
        help="Ten profile ben trong user data dir.",
    )
    parser.add_argument(
        "--cookies-file",
        default=None,
        help="File cookies JSON export tu browser/extension.",
    )
    parser.add_argument(
        "--user-agent",
        default=None,
        help="User-Agent don le. Neu co, se uu tien hon user_agents.txt.",
    )
    parser.add_argument(
        "--proxy",
        default=None,
        help="Proxy don le, vi du http://host:port. Neu co, se uu tien hon proxies.txt.",
    )
    parser.add_argument(
        "--user-agents-file",
        default=None,
        help="File danh sach user-agent.",
    )
    parser.add_argument(
        "--proxies-file",
        default=None,
        help="File danh sach proxy.",
    )
    return parser.parse_args()


def load_config(config_path: str) -> dict[str, Any]:
    if not os.path.exists(config_path):
        raise RuntimeError(f"Missing config file: {config_path}")

    with open(config_path, "r", encoding="utf-8") as file:
        content = yaml.safe_load(file) or {}

    if not isinstance(content, dict):
        raise RuntimeError("config.yml must contain a YAML mapping at the top level")

    return content


def resolve_path(base_dir: str, value: str) -> str:
    if os.path.isabs(value):
        return value

    return os.path.abspath(os.path.join(base_dir, value))


def build_runtime_settings(args: argparse.Namespace, config: dict[str, Any], config_dir: str) -> dict[str, Any]:
    login_config = config.get("login", {})
    crawl_config = config.get("crawl", {})

    if login_config and not isinstance(login_config, dict):
        raise RuntimeError("login in config.yml must be a mapping")

    if crawl_config and not isinstance(crawl_config, dict):
        raise RuntimeError("crawl in config.yml must be a mapping")

    pages_file = args.pages_file or crawl_config.get("pages_file") or DEFAULT_PAGES_FILE
    output_file = args.output_file or crawl_config.get("output_file") or DEFAULT_OUTPUT_FILE

    return {
        "profile_path": args.profile_path
        or login_config.get("profile_path")
        or os.getenv("PROFILE_PATH")
        or DEFAULT_PROFILE_PATH,
        "profile_name": args.profile_name
        or login_config.get("profile_name")
        or os.getenv("PROFILE_NAME")
        or DEFAULT_PROFILE_NAME,
        "cookies_file": args.cookies_file
        or login_config.get("cookies_file")
        or os.getenv("COOKIES_FILE")
        or DEFAULT_COOKIE_FILE,
        "user_agent": args.user_agent or login_config.get("user_agent") or os.getenv("USER_AGENT"),
        "proxy": args.proxy or login_config.get("proxy") or os.getenv("PROXY"),
        "user_agents_file": args.user_agents_file
        or login_config.get("user_agents_file")
        or os.getenv("USER_AGENT_FILE")
        or DEFAULT_USER_AGENT_FILE,
        "proxies_file": args.proxies_file
        or login_config.get("proxies_file")
        or os.getenv("PROXY_FILE")
        or DEFAULT_PROXY_FILE,
        "pages_file": resolve_path(config_dir, pages_file),
        "output_file": resolve_path(config_dir, output_file),
        "page_wait_seconds": float(crawl_config.get("page_wait_seconds", DEFAULT_PAGE_WAIT_SECONDS)),
        "field_timeout": int(crawl_config.get("field_timeout", DEFAULT_FIELD_TIMEOUT)),
    }


def parse_fields(config: dict[str, Any], default_timeout: int) -> list[FieldConfig]:
    raw_fields = config.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise RuntimeError("config.yml must contain a non-empty fields list")

    fields = []
    for index, item in enumerate(raw_fields, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(f"Field #{index} must be a mapping")

        name = item.get("name")
        selector = item.get("selector")
        by = str(item.get("by", "css")).lower()

        if not name:
            raise RuntimeError(f"Field #{index} must define name")

        if by not in BY_MAP:
            raise RuntimeError(f"Unsupported locator strategy for field {name}: {by}")

        attribute = str(item.get("attribute", "text"))
        if not selector and attribute not in DRIVER_VALUE_ATTRIBUTES:
            raise RuntimeError(
                f"Field #{index} must define selector unless attribute is one of "
                f"{sorted(DRIVER_VALUE_ATTRIBUTES)}"
            )

        fields.append(
            FieldConfig(
                name=str(name),
                by=by,
                selector=str(selector) if selector else None,
                attribute=attribute,
                multiple=bool(item.get("multiple", False)),
                optional=bool(item.get("optional", False)),
                wait_until=str(item.get("wait_until", "presence")).lower(),
                timeout=int(item.get("timeout", default_timeout)),
                strip=bool(item.get("strip", True)),
                limit=int(item["limit"]) if item.get("limit") is not None else None,
                default=item.get("default"),
            )
        )

    return fields


def build_locator(field: FieldConfig) -> tuple[str, str]:
    if not field.selector:
        raise RuntimeError(f"Field '{field.name}' does not define a selector")
    return BY_MAP[field.by], field.selector


def build_wait_condition(locator: tuple[str, str], wait_until: str):
    if wait_until == "visible":
        return EC.visibility_of_element_located(locator)

    if wait_until == "clickable":
        return EC.element_to_be_clickable(locator)

    return EC.presence_of_element_located(locator)


def extract_value(element: WebElement, attribute: str, strip: bool) -> Any:
    if attribute == "text":
        value = element.text
    elif attribute == "inner_html":
        value = element.get_attribute("innerHTML")
    elif attribute == "outer_html":
        value = element.get_attribute("outerHTML")
    else:
        value = element.get_attribute(attribute)

    if isinstance(value, str) and strip:
        return value.strip()

    return value


def extract_driver_value(driver, attribute: str, strip: bool) -> Any:
    if attribute == "driver_title":
        value = driver.title
    elif attribute == "current_url":
        value = driver.current_url
    elif attribute == "page_source":
        value = driver.page_source
    else:
        raise RuntimeError(f"Unsupported driver attribute: {attribute}")

    if isinstance(value, str) and strip:
        return value.strip()

    return value


def extract_field(driver, field: FieldConfig) -> Any:
    if field.attribute in DRIVER_VALUE_ATTRIBUTES and not field.selector:
        value = extract_driver_value(driver, field.attribute, field.strip)
        if value in (None, "") and field.default is not None:
            return field.default
        if value in (None, "") and field.optional:
            return field.default if field.default is not None else ([] if field.multiple else None)
        if value in (None, ""):
            raise RuntimeError(f"Field '{field.name}' returned an empty value")
        return value

    locator = build_locator(field)
    wait = WebDriverWait(driver, field.timeout)

    try:
        wait.until(build_wait_condition(locator, field.wait_until))
    except TimeoutException:
        if field.optional:
            return field.default if field.default is not None else ([] if field.multiple else None)
        raise RuntimeError(f"Timed out waiting for field '{field.name}'")

    if field.multiple:
        elements = driver.find_elements(*locator)
        if field.limit is not None:
            elements = elements[: field.limit]

        values = [extract_value(element, field.attribute, field.strip) for element in elements]
        return values if values else (field.default if field.default is not None else [])

    element = driver.find_element(*locator)
    value = extract_value(element, field.attribute, field.strip)

    if value in (None, "") and field.default is not None:
        return field.default

    return value


def crawl_page(driver, url: str, fields: list[FieldConfig], page_wait_seconds: float) -> dict[str, Any]:
    driver.get(url)
    wait_for_page_ready(driver)
    if page_wait_seconds > 0:
        time.sleep(page_wait_seconds)

    record = {
        "url": url,
        "final_url": driver.current_url,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "data": {},
        "errors": [],
    }

    for field in fields:
        try:
            record["data"][field.name] = extract_field(driver, field)
        except Exception as exc:
            record["errors"].append({"field": field.name, "error": str(exc)})
            if field.default is not None:
                record["data"][field.name] = field.default
            elif field.multiple:
                record["data"][field.name] = []
            else:
                record["data"][field.name] = None

    return record


def write_output(output_file: str, payload: dict[str, Any]) -> None:
    output_dir = os.path.dirname(output_file)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def main() -> None:
    load_dotenv()
    args = parse_args()
    config_path = os.path.abspath(args.config)
    config_dir = os.path.dirname(config_path)
    config = load_config(config_path)
    settings = build_runtime_settings(args, config, config_dir)
    fields = parse_fields(config, settings["field_timeout"])

    pages = load_non_comment_lines(settings["pages_file"], required=True)
    profile_path = ensure_profile_path(settings["profile_path"])

    login_args = argparse.Namespace(
        user_agent=settings["user_agent"],
        proxy=settings["proxy"],
        user_agents_file=settings["user_agents_file"],
        proxies_file=settings["proxies_file"],
    )
    attempts = build_attempts(login_args)
    cookies = load_cookies(settings["cookies_file"])

    driver = None
    try:
        driver, login_description = open_first_authenticated_driver(
            profile_path=profile_path,
            profile_name=settings["profile_name"],
            attempts=attempts,
            cookies=cookies,
        )

        results = []
        for index, url in enumerate(pages, start=1):
            print(f"[{index}/{len(pages)}] Crawling {url}")
            results.append(
                crawl_page(
                    driver=driver,
                    url=url,
                    fields=fields,
                    page_wait_seconds=settings["page_wait_seconds"],
                )
            )

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "login_attempt": login_description,
            "page_count": len(results),
            "results": results,
        }
        write_output(settings["output_file"], payload)

        print(f"Saved JSON output to: {settings['output_file']}")
    finally:
        if driver:
            driver.quit()


if __name__ == "__main__":
    main()
