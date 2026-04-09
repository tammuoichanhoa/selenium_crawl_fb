"""Page list utilities and worker assignment helpers."""

from __future__ import annotations

import os  # file access for pages list
import logging
from typing import Any, List, Tuple  # type hints

logger = logging.getLogger(__name__)


def read_pages(path: str) -> List[str]:
    """Read a newline-delimited list of page URLs."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Pages list not found: {path}")
    with open(path, "r", encoding="utf-8") as file:
        pages = [
            line.strip()
            for line in file
            if line.strip() and not line.strip().startswith("#")
        ]
    if not pages:
        raise ValueError("pages.txt is empty. Please add at least one URL.")
    return pages


def resolve_max_workers(
    configured_value: Any,
    total_pages: int,
    login_method: str,
    available_profiles: int,
) -> int:
    """Compute a safe worker count based on login mode and profiles."""
    try:
        max_workers = int(configured_value or 1)
    except (TypeError, ValueError):
        max_workers = 1

    max_workers = max(1, min(max_workers, total_pages))

    if login_method == "profile":
        if available_profiles <= 1:
            if max_workers > 1:
                logger.warning(
                    "[crawl] Only one profile is configured, "
                    "so multi-threading is disabled."
                )
            return 1
        return min(max_workers, available_profiles)

    if login_method == "cookies" and max_workers > 1:
        logger.warning(
            "[crawl] LOGIN_METHOD=cookies now runs with one worker by default. "
            "Use multiple Facebook profiles for safe parallel crawling."
        )
        return 1
    logger.info("max_workers: %s", max_workers)
    return max_workers


def split_urls_for_workers(
    items: List[Any],
    max_workers: int,
) -> List[List[Tuple[int, Any]]]:
    """Split crawl inputs into round-robin batches for workers."""
    batches: List[List[Tuple[int, Any]]] = [[] for _ in range(max_workers)]
    for index, item in enumerate(items):
        batches[index % max_workers].append((index, item))
    return [batch for batch in batches if batch]


def split_pages_for_workers(
    pages: List[Any],
    max_workers: int,
) -> List[List[Tuple[int, Any]]]:
    """Backward-compatible alias for older callers."""
    return split_urls_for_workers(pages, max_workers)
