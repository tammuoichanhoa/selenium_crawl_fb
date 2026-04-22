from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from .group_media_common import PROJECT_ROOT


def _cookie_pairs_to_header(cookie_items: Iterable[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in cookie_items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if not name:
            continue
        parts.append(f"{name}={value}")
    return "; ".join(parts)


def load_cookie_header(
    *,
    cookie_header: str | None = None,
    cookie_file: str | None = None,
) -> str:
    candidates: list[str] = []

    if cookie_header and cookie_header.strip():
        candidates.append(cookie_header.strip())

    if cookie_file and Path(cookie_file).exists():
        candidates.append(Path(cookie_file).read_text(encoding="utf-8").strip())

    env_cookie = (os.getenv("COOKIES") or "").strip()
    if env_cookie:
        candidates.append(env_cookie)

    default_cookie_file = PROJECT_ROOT / "cookie_string.txt"
    if default_cookie_file.exists():
        candidates.append(default_cookie_file.read_text(encoding="utf-8").strip())

    for raw_value in candidates:
        if not raw_value:
            continue

        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            parsed = None

        if isinstance(parsed, list):
            normalized = _cookie_pairs_to_header(parsed)
            if normalized:
                return normalized

        if isinstance(parsed, dict):
            normalized = _cookie_pairs_to_header([parsed])
            if normalized:
                return normalized

        if "=" in raw_value:
            return raw_value

    raise ValueError(
        "Không tìm thấy cookie hợp lệ. Hãy truyền --cookie, --cookie-file, "
        "hoặc thiết lập COOKIES/cookie_string.txt."
    )

