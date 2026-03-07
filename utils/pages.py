from __future__ import annotations

import os
from typing import Any, List, Tuple


def read_pages(path: str) -> List[str]:
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
    try:
        max_workers = int(configured_value or 1)
    except (TypeError, ValueError):
        max_workers = 1

    max_workers = max(1, min(max_workers, total_pages))

    if login_method == "profile":
        if available_profiles <= 1:
            if max_workers > 1:
                print(
                    "[crawl] Only one profile is configured, "
                    "so multi-threading is disabled."
                )
            return 1
        return min(max_workers, available_profiles)

    if login_method == "cookies" and max_workers > 1:
        print(
            "[crawl] LOGIN_METHOD=cookies now runs with one worker by default. "
            "Use multiple Facebook profiles for safe parallel crawling."
        )
        return 1
    print("max_workers: ", max_workers)
    return max_workers


def split_pages_for_workers(
    pages: List[str],
    max_workers: int,
) -> List[List[Tuple[int, str]]]:
    batches: List[List[Tuple[int, str]]] = [[] for _ in range(max_workers)]
    for index, url in enumerate(pages):
        batches[index % max_workers].append((index, url))
    return [batch for batch in batches if batch]
