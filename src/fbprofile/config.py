import os
import re
from pathlib import Path

# =========================
# GLOBAL CONFIG
# =========================

CURSOR_KEYS = {"end_cursor", "endCursor", "after", "afterCursor", "feedAfterCursor", "cursor"}

POST_URL_RE = re.compile(
    r"""https?://(?:[a-zA-Z0-9\-\.]+\.)?(?:facebook\.com|fb\.watch|fb\.me|fb\.com)/
        (?:
            groups/[^/]+/(?:permalink|posts|videos)/\d+
          | [A-Za-z0-9.\-]+/posts/\d+
          | [A-Za-z0-9.\-]+/videos(?:/[^/]+)?/\d+
          | [A-Za-z0-9.\-]+(?:/photos/a\.\d+\.\d+)?/photos/.*?\d+
          | [A-Za-z0-9.\-]+/reel/\d+
          | photo(?:\.php)?\?(?:.*(?:fbid|story_fbid|video_id|v)=\d+)
          | .*?/pfbid[A-Za-z0-9]+
          | .*?
        )
    """,
    re.I | re.X,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def env(key: str, default=None, cast=str):
    v = os.environ.get(key, default)
    if v is None:
        return None
    if cast is bool:
        return str(v).lower() in ("1", "true", "yes", "y", "on")
    try:
        return cast(v)
    except Exception:
        return default
