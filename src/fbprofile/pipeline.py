# post/v3/pipeline.py
from pathlib import Path
from typing import Dict, Any, Set, List, Optional

from logs.loging_config import logger
from .graphql.parser import parse_fb_graphql_payload
from .graphql.extractors import (
    collect_post_summaries,
    coalesce_posts,
    _best_primary_key,
)
from .storage.ndjson import append_ndjson


LATEST_CREATED_TS: Optional[int] = None
EARLIEST_CREATED_TS: Optional[int] = None  # NEW

def process_single_gql_rec(
    rec: Dict[str, Any],
    group_url: str,
    seen_ids: Set[str],
    out_path: Path,
    log_prefix: str = "",
    ts_state: dict = None,
) -> int:
    global LATEST_CREATED_TS, EARLIEST_CREATED_TS  # UPDATED
    
    if ts_state is None:
        ts_state = {"latest": LATEST_CREATED_TS, "earliest": EARLIEST_CREATED_TS}
        use_global = True
    else:
        use_global = False

    text = rec.get("responseText")
    if not text:
        return 0

    payload = parse_fb_graphql_payload(text)
    if payload is None:
        logger.debug("[GQL%s] responseText parse fail (no JSON payload)", log_prefix)
        return 0

    raw_items: List[Dict[str, Any]] = []

    if isinstance(payload, dict):
        collect_post_summaries(payload, raw_items, group_url)
    elif isinstance(payload, list):
        for obj in payload:
            collect_post_summaries(obj, raw_items, group_url)

    if not raw_items:
        logger.debug("[GQL%s] collect_post_summaries -> 0 items", log_prefix)
        return 0

    logger.debug(
        "[GQL%s] collect_post_summaries -> %d items", log_prefix, len(raw_items)
    )

    page_posts = coalesce_posts(raw_items)
    logger.debug("[GQL%s] coalesce_posts -> %d items", log_prefix, len(page_posts))

    if not page_posts:
        return 0

    written_this_round: Set[str] = set()
    fresh: List[Dict[str, Any]] = []
    for p in page_posts:
        pk = _best_primary_key(p)
        if not pk:
            import hashlib
            raw = str(p.get("created_time", "")) + str(p.get("content", "")) + str(p.get("link", ""))
            if len(raw) > 5:
                pk = "hash_" + hashlib.md5(raw.encode("utf-8")).hexdigest()
        
        if pk and (pk not in seen_ids) and (pk not in written_this_round):
            fresh.append(p)
            written_this_round.add(pk)

    if not fresh:
        logger.debug("[GQL%s] no fresh posts after dedup", log_prefix)
        return 0

    # cập nhật min/max created_time
    for p in fresh:
        ts = p.get("created_time")
        if isinstance(ts, (int, float)):
            ts_int = int(ts)
            if ts_state["latest"] is None or ts_int > ts_state["latest"]:
                ts_state["latest"] = ts_int
            if ts_state["earliest"] is None or ts_int < ts_state["earliest"]:
                ts_state["earliest"] = ts_int

    append_ndjson(fresh, out_path)

    for p in fresh:
        pk = _best_primary_key(p)
        if pk:
            seen_ids.add(pk)

    if use_global:
        LATEST_CREATED_TS = ts_state["latest"]
        EARLIEST_CREATED_TS = ts_state["earliest"]

    logger.info("[GQL%s] wrote %d fresh posts", log_prefix, len(fresh))
    return len(fresh)