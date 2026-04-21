"""Helpers for cleaning Facebook intro/about placeholder text."""

from __future__ import annotations

import re
import unicodedata


_EN_EMPTY_INTRO_RE = re.compile(r"^no\b.+\bto show$", re.IGNORECASE)


def _fold_ascii(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def is_empty_intro_placeholder(value: str) -> bool:
    """Return True for Facebook empty-state intro rows."""
    if not isinstance(value, str):
        return False

    text = " ".join(value.split()).strip()
    if not text:
        return False

    lowered = text.casefold()
    folded = _fold_ascii(lowered)
    return bool(_EN_EMPTY_INTRO_RE.match(lowered)) or (
        "khong co" in folded and "hien thi" in folded
    )


def clean_intro_text(value: str, separator: str = " - ") -> str:
    """Drop empty-state lines and normalize multi-line intro text."""
    if not isinstance(value, str):
        return ""

    parts = [part.strip() for part in value.splitlines() if part.strip()]
    if not parts:
        return ""

    parts = [part for part in parts if not is_empty_intro_placeholder(part)]
    if not parts:
        return ""

    cleaned = separator.join(parts).strip()
    if is_empty_intro_placeholder(cleaned):
        return ""
    return cleaned
