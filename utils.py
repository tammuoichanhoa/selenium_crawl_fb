import os
import shutil
import time
from pathlib import Path
from typing import Dict, List
from urllib.parse import urlparse

import requests
import yaml


def load_env_file(path: str = ".env") -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not os.path.exists(path):
        return env

    with open(path, "r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def parse_cookie_string(cookie_string: str) -> List[Dict[str, str]]:
    cookies: List[Dict[str, str]] = []
    for part in cookie_string.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        cookies.append({"name": name, "value": value})
    return cookies


def backup_profile_folder(source_folder: str, destination_root: str = "profiles") -> str:
    """Archive a browser profile directory so it can be downloaded locally.

    Args:
        source_folder: Absolute path to the profile folder that Chrome/Selenium
            is using for the logged-in session.
        destination_root: Directory where the zipped archive should be written.

    Returns:
        Path to the created zip file.
    """

    source = Path(source_folder).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Profile folder not found: {source}")
    if not source.is_dir():
        raise NotADirectoryError(f"Profile folder is not a directory: {source}")

    destination_dir = Path(destination_root).expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    archive_stem = destination_dir / f"profile_backup_{timestamp}"
    archive_path = shutil.make_archive(
        str(archive_stem),
        "zip",
        root_dir=str(source.parent),
        base_dir=str(source.name),
    )
    return archive_path


def str_to_bool(value: str | bool | None, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return fallback

DEFAULT_CONFIG_PATH = "config.yml"
DEFAULT_PAGES_FILE = "pages.txt"
DEFAULT_PROXIES_FILE = "proxies.txt"

def load_config(path: str = DEFAULT_CONFIG_PATH) -> Dict[str, any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    data.setdefault("login", {})
    data.setdefault("crawl", {})
    data["crawl"].setdefault("elements", [])
    return data


def _iter_proxy_candidates(primary_proxy: str | None, proxies_file: str) -> List[str]:
    candidates: List[str] = []
    seen: set[str] = set()
    if primary_proxy:
        candidate = primary_proxy.strip()
        if candidate:
            seen.add(candidate)
            candidates.append(candidate)

    if os.path.exists(proxies_file):
        with open(proxies_file, "r", encoding="utf-8") as file:
            for raw_line in file:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line in seen:
                    continue
                seen.add(line)
                candidates.append(line)

    return candidates


def _proxy_supports_requests(proxy_url: str) -> bool:
    parsed = urlparse(proxy_url)
    scheme = (parsed.scheme or "http").lower()
    return scheme in {"http", "https"}


def _validate_proxy_with_requests(proxy_url: str, test_url: str, timeout: float) -> bool:
    if not _proxy_supports_requests(proxy_url):
        return False
    proxies = {"http": proxy_url, "https": proxy_url}
    try:
        response = requests.get(test_url, proxies=proxies, timeout=timeout)
        return response.ok
    except requests.RequestException:
        return False


def select_working_proxy(
    env_proxy: str | None = None,
    proxies_file: str = DEFAULT_PROXIES_FILE,
    test_url: str = "https://www.facebook.com/",
    timeout: float = 8.0,
) -> str | None:
    """Pick the first proxy that passes a health check via requests."""

    for candidate in _iter_proxy_candidates(env_proxy, proxies_file):
        if not _proxy_supports_requests(candidate):
            print(f"[proxy] Skipping unsupported proxy scheme: {candidate}")
            continue
        if _validate_proxy_with_requests(candidate, test_url, timeout):
            print(f"[proxy] Using proxy: {candidate}")
            return candidate
        print(f"[proxy] Proxy failed health check: {candidate}")

    print("[proxy] No working proxy found; continuing without proxy")
    return None
