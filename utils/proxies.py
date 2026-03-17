"""Proxy selection helpers with health checks."""

from __future__ import annotations

import os  # read proxies file
import logging
from typing import List  # type hints
from urllib.parse import urlparse  # validate proxy scheme

import requests  # HTTP checks for proxy health

logger = logging.getLogger(__name__)

DEFAULT_PROXIES_FILE = "proxies.txt"


def _iter_proxy_candidates(primary_proxy: str | None, proxies_file: str) -> List[str]:
    """Collect proxy candidates from env and proxies file."""
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
    """Return True if the proxy scheme is supported by requests."""
    parsed = urlparse(proxy_url)
    scheme = (parsed.scheme or "http").lower()
    return scheme in {"http", "https"}


def _validate_proxy_with_requests(proxy_url: str, test_url: str, timeout: float) -> bool:
    """Check if a proxy can reach the test URL via requests."""
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
    """Return the first proxy that passes a health check."""
    # Pick the first proxy that passes a health check via requests.

    for candidate in _iter_proxy_candidates(env_proxy, proxies_file):
        if not _proxy_supports_requests(candidate):
            logger.warning("[proxy] Skipping unsupported proxy scheme: %s", candidate)
            continue
        if _validate_proxy_with_requests(candidate, test_url, timeout):
            logger.info("[proxy] Using proxy: %s", candidate)
            return candidate
        logger.warning("[proxy] Proxy failed health check: %s", candidate)

    logger.warning("[proxy] No working proxy found; continuing without proxy")
    return None
