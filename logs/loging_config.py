"""Compatibility logger for modules that import logs.loging_config."""

import hashlib
import logging

from src.utils.logging_setup import setup_logging


setup_logging()

logger = logging.getLogger("crawl_sheet1")
logger.setLevel(logging.DEBUG)


def get_post_logger(postlink: str):
    """Return a child logger tagged by post URL hash."""
    h = hashlib.md5((postlink or "").encode("utf-8")).hexdigest()[:16]
    l = logging.getLogger(f"crawl_sheet1.post.{h}")
    l.propagate = True
    l.setLevel(logging.DEBUG)
    return l
