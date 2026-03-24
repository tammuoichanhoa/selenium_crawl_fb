"""Profile backup helper to archive Chrome profiles."""

from __future__ import annotations

import shutil  # build zip archives
import time  # timestamp for archive names
from pathlib import Path  # path utilities
import re  # simple slugging


def _sanitize_archive_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned.strip("._-")


def backup_profile_folder(
    source_folder: str,
    destination_root: str = "profiles",
    archive_name: str | None = None,
) -> str:
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

    archive_stem: Path
    if archive_name:
        safe_name = _sanitize_archive_name(archive_name)
        if safe_name:
            archive_stem = destination_dir / safe_name
        else:
            archive_stem = destination_dir / f"profile_backup_{time.strftime('%Y%m%d_%H%M%S')}"
    else:
        archive_stem = destination_dir / f"profile_backup_{time.strftime('%Y%m%d_%H%M%S')}"
    archive_path = shutil.make_archive(
        str(archive_stem),
        "zip",
        root_dir=str(source.parent),
        base_dir=str(source.name),
    )
    return archive_path
