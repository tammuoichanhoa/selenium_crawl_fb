from __future__ import annotations

import os
from typing import List
from urllib.parse import urlparse

import requests

DEFAULT_PROXIES_FILE = "proxies.txt"


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
