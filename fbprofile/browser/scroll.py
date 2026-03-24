# post/v3/browser/scroll.py
import time
from pathlib import Path
from typing import Set, Dict, Any

from logs.loging_config import logger
from ..browser.hooks import CLEANUP_JS, flush_gql_recs
from ..graphql.extractors import _best_primary_key
from ..pipeline import process_single_gql_rec  # sẽ tạo file này bên dưới


_SHOULD_STOP = False


def set_stop_flag():
    global _SHOULD_STOP
    _SHOULD_STOP = True


def crawl_scroll_loop(
    d,
    group_url: str,
    out_path: Path,
    seen_ids: Set[str],
    keep_last: int,
    max_scrolls: int = 10000000000,
    ts_state: dict = None,
) -> bool:
    """
    Return:
        True  -> dừng vì stall (Stall confirmed ...)
        False -> dừng vì lý do khác (STOP flag, MAX_SCROLLS, error...)
    """
    MAX_SCROLLS = max_scrolls
    CLEANUP_EVERY = 25
    STALL_THRESHOLD = 8

    DOM_KEEP = max(30, min(keep_last or 40, 60))

    prev_height = None
    stall_count = 0
    idle_rounds_no_new_posts = 0
    i = 0
    stopped_due_to_stall = False  # NEW

    while True:
        if _SHOULD_STOP:
            logger.info("[STOP] Received stop flag, breaking scroll loop.")
            break

        if i >= MAX_SCROLLS:
            logger.info("[STOP] Reach MAX_SCROLLS=%d, break loop.", MAX_SCROLLS)
            break

        try:
            d.execute_script(
                "window.scrollBy(0, Math.floor(window.innerHeight * 0.9));"
            )
        except Exception as e:
            logger.warning("[SCROLL] execute_script error: %s", e)
            break

        time.sleep(1.0)

        recs = flush_gql_recs(d)
        total_new_from_batch = 0

        if recs:
            for idx, rec in enumerate(recs):
                num_new = process_single_gql_rec(
                    rec,
                    group_url=group_url,
                    seen_ids=seen_ids,
                    out_path=out_path,
                    log_prefix=f"#{i}/{idx}",
                    ts_state=ts_state,
                )
                total_new_from_batch += num_new

            if total_new_from_batch:
                logger.info(
                    "[GQL] #%d: collected %d new posts (total_seen=%d)",
                    i,
                    total_new_from_batch,
                    len(seen_ids),
                )

        if total_new_from_batch == 0:
            idle_rounds_no_new_posts += 1
        else:
            idle_rounds_no_new_posts = 0

        if i > 0 and (i % CLEANUP_EVERY == 0):
            try:
                d.execute_script(CLEANUP_JS, DOM_KEEP)
            except Exception:
                pass

        try:
            cur_height = d.execute_script("return document.body.scrollHeight;")
        except Exception:
            break

        if prev_height is None:
            prev_height = cur_height
        else:
            if cur_height <= prev_height and total_new_from_batch == 0:
                stall_count += 1
            else:
                stall_count = 0
                prev_height = cur_height

        if stall_count >= STALL_THRESHOLD and idle_rounds_no_new_posts >= 10:
            logger.info(
                "[STOP] Stall confirmed: no new posts for %d rounds & height stagnant.",
                idle_rounds_no_new_posts,
            )
            stopped_due_to_stall = True  # NEW
            break

        i += 1
        time.sleep(1)

    logger.info("[DONE] Crawl loop finished. Total unique posts seen: %d", len(seen_ids))
    return stopped_due_to_stall  # NEW
