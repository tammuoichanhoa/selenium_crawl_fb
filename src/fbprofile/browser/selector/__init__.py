"""
crawlers/
=========
Re-export các hàm công khai để code bên ngoài không cần biết cấu trúc nội bộ.

Ví dụ:
    from src.crawlers import extract_selector_post, parse_comment
"""

from .comment_extractor import parse_comment
from .post_extractor import (
    collect_visible_selector_posts,
    extract_best_selector_post,
    extract_selector_post,
    process_visible_selector_posts,
)

__all__ = [
    "parse_comment",
    "collect_visible_selector_posts",
    "extract_best_selector_post",
    "extract_selector_post",
    "process_visible_selector_posts",
]
