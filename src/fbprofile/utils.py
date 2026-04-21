import json, re
from typing import List, Optional
from .config import *
from urllib.parse import parse_qs, urlparse, urlunparse

def _norm_link(u: str) -> Optional[str]:
    if not u or not isinstance(u, str):
        return None
    try:
        p = urlparse(u)
        host = p.netloc.lower()
        if host.endswith("facebook.com"): host = "facebook.com"
        path = (p.path or "").rstrip("/")
        if re.search(r"/(?:reel|posts|permalink)/[A-Za-z0-9_-]+$", path):
            return urlunparse(("https", host, path, "", "", ""))
        query = parse_qs(p.query or "")
        for key in ("story_fbid", "fbid", "photo_id", "video_id", "v"):
            value = query.get(key, [None])[0]
            if value:
                return urlunparse(("https", host, p.path or "", "", f"{key}={value}", ""))
        return None
    except Exception:
        return None
