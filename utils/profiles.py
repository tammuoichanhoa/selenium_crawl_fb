from __future__ import annotations

import os
from typing import Any, Dict, List


def parse_profile_dirs(raw_value: Any) -> List[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        parts = raw_value.replace("\n", ",").split(",")
    elif isinstance(raw_value, list):
        parts = raw_value
    else:
        return []

    profile_dirs: List[str] = []
    seen: set[str] = set()
    for part in parts:
        candidate = str(part).strip()
        if not candidate:
            continue
        normalized = os.path.abspath(os.path.expanduser(candidate))
        if normalized in seen:
            continue
        seen.add(normalized)
        profile_dirs.append(normalized)
    return profile_dirs


def resolve_profile_dirs(
    env: Dict[str, str],
    crawl_cfg: Dict[str, Any],
    login_cfg: Dict[str, Any],
) -> List[str]:
    profile_dirs = parse_profile_dirs(env.get("PROFILE_DIRS"))
    if profile_dirs:
        return profile_dirs

    profile_dirs = parse_profile_dirs(crawl_cfg.get("profile_dirs"))
    if profile_dirs:
        return profile_dirs

    profiles_root = os.path.join(os.getcwd(), "profiles")
    if os.path.isdir(profiles_root):
        try:
            entries = [
                os.path.join(profiles_root, name)
                for name in os.listdir(profiles_root)
            ]
        except OSError:
            entries = []
        subdirs = [path for path in entries if os.path.isdir(path)]
        if subdirs:
            subdirs.sort()
            return parse_profile_dirs(subdirs)

    fallback_profile_dir = (
        env.get("PROFILE_DIR")
        or login_cfg.get("profile_dir")
        or os.path.join(os.getcwd(), "chrome_profile")
    )
    return parse_profile_dirs([fallback_profile_dir])
