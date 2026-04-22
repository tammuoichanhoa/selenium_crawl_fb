from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def extract_group_id_from_url(group_url: str) -> str | None:
    if not isinstance(group_url, str):
        return None

    match = re.search(r"/groups/([^/?#]+)", group_url)
    if not match:
        return None

    group_id = (match.group(1) or "").strip()
    return group_id or None


def default_output_path(group_url: str) -> Path:
    group_id = extract_group_id_from_url(group_url) or "group"
    filename = f"group_media_pipeline_{group_id}.json"
    return PROJECT_ROOT / "groups" / "outputs" / filename

