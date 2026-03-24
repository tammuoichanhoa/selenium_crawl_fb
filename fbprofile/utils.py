import json, re
from typing import List, Optional
from .config import *
from urllib.parse import urlparse, urlunparse

def _norm_link(u: str) -> Optional[str]:
    if not u or not isinstance(u, str):
        return None
    try:
        p = urlparse(u)
        host = p.netloc.lower()
        if host.endswith("facebook.com"): host = "facebook.com"
        path = (p.path or "").rstrip("/")
        if re.search(r"/(?:reel|posts|permalink)/\d+$", path.lower()):
            return urlunparse(("https", host, path.lower(), "", "", ""))
        return None
    except Exception:
        return None
