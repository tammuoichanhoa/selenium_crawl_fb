# post/v3/graphql/parser.py
import json
import re
from typing import List, Any, Optional

from urllib.parse import urlparse, urlunparse

from ..config import PROJECT_ROOT


def deep_collect_timestamps(obj) -> List[int]:
    keys_hint = {"creation_time", "created_time", "creationTime", "createdTime"}
    out = []

    def as_epoch_s(x):
        try:
            v = int(x)
            if v > 10_000_000_000:
                v //= 1000
            if 1104537600 <= v <= 4102444800:
                return v
        except Exception:
            pass
        return None

    def dive(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in keys_hint:
                    vv = as_epoch_s(v)
                    if vv:
                        out.append(vv)
                dive(v)
        elif isinstance(o, list):
            for v in o:
                dive(v)

    dive(obj)
    return out


def _strip_xssi_prefix(s: str) -> str:
    if not s:
        return s
    s2 = s.lstrip()
    s2 = re.sub(r'^\s*for\s*\(\s*;\s*;\s*\)\s*;\s*', '', s2)
    s2 = re.sub(r"^\s*\)\]\}'\s*", '', s2)
    return s2


def iter_json_values(s: str):
    dec = json.JSONDecoder()
    i, n = 0, len(s)
    while i < n:
        m = re.search(r'\S', s[i:])
        if not m:
            break
        j = i + m.start()
        try:
            obj, k = dec.raw_decode(s, j)
            yield obj
            i = k
        except json.JSONDecodeError:
            chunk = _strip_xssi_prefix(s[j:])
            if chunk == s[j:]:
                break
            try:
                obj, k_rel = dec.raw_decode(chunk, 0)
                yield obj
                i = j + k_rel
            except json.JSONDecodeError:
                break


def choose_best_graphql_obj(objs):
    objs = list(objs)
    if not objs:
        return None
    with_data = [o for o in objs if isinstance(o, dict) and "data" in o]
    pick = with_data or objs
    return max(pick, key=lambda o: len(json.dumps(o, ensure_ascii=False)))


def parse_fb_graphql_payload(text: str):
    if not text:
        return None

    cleaned = _strip_xssi_prefix(text)
    objs = list(iter_json_values(cleaned))
    payload = choose_best_graphql_obj(objs) if objs else None
    if payload is not None:
        return payload

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None
