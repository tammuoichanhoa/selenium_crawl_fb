"""Logging configuration helpers."""

from __future__ import annotations

import logging
import os
from typing import Optional


def setup_logging(level: Optional[str] = None) -> None:
    """Configure root logging with a consistent format."""
    level_name = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    level_value = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level_value,
        format="%(asctime)s [%(levelname)s] %(name)s %(filename)s:%(lineno)d: %(message)s",
    )
