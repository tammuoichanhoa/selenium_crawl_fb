"""Cookie parsing helpers."""

from __future__ import annotations

from typing import Dict, List  # type hints


def parse_cookie_string(cookie_string: str) -> List[Dict[str, str]]:
    """Parse a Cookie header string into Selenium cookie dicts."""
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
