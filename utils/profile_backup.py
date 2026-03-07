from __future__ import annotations

import shutil
import time
from pathlib import Path


def backup_profile_folder(source_folder: str, destination_root: str = "profiles") -> str:
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

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    archive_stem = destination_dir / f"profile_backup_{timestamp}"
    archive_path = shutil.make_archive(
        str(archive_stem),
        "zip",
        root_dir=str(source.parent),
        base_dir=str(source.name),
    )
    return archive_path
