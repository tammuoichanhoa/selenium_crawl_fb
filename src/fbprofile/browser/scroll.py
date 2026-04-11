# post/v3/browser/scroll.py
import time
from pathlib import Path
from typing import Set, Dict, Any

from logs.loging_config import logger
from ..browser.hooks import CLEANUP_JS
from .selector_posts import process_visible_selector_posts
from .stable_scroll import get_scroll_height, normalize_scroll_until_stable_cfg



_SHOULD_STOP = False


def set_stop_flag():
    global _SHOULD_STOP
    _SHOULD_STOP = True


'''
dùng tiêu chí stable đó thay cho ngưỡng stall hard-code cũ,
và nhận cấu hình ngoài qua scroll_until_stable_cfg
'''
def crawl_scroll_loop(
    d,
    group_url: str,
    out_path: Path,
    seen_ids: Set[str],
    keep_last: int,
    max_scrolls: int = 10000000000,
    ts_state: dict = None,
    scroll_until_stable_cfg: Dict[str, Any] | None = None,
) -> bool:
    """
    Return:
        True  -> dừng vì stall (Stall confirmed ...)
        False -> dừng vì lý do khác (STOP flag, MAX_SCROLLS, error...)
    """
    CLEANUP_EVERY = 25
    DOM_KEEP = max(30, min(keep_last or 40, 60))
    resolved_scroll_cfg = normalize_scroll_until_stable_cfg(
        scroll_until_stable_cfg,
        defaults={
            "max_scrolls": max_scrolls,
            "stable_rounds": 8,
            "scroll_pause_seconds": 1.0,
            "settle_pause_seconds": 1.0,
        },
    )
    max_scrolls = int(resolved_scroll_cfg["max_scrolls"])
    stable_rounds_required = int(resolved_scroll_cfg["stable_rounds"])
    scroll_pause_seconds = float(resolved_scroll_cfg["scroll_pause_seconds"])
    settle_pause_seconds = float(resolved_scroll_cfg["settle_pause_seconds"])

    prev_height = get_scroll_height(d)
    prev_seen_count = len(seen_ids)
    stall_count = 0
    i = 0
    stopped_due_to_stall = False

    while True:
        if _SHOULD_STOP:
            logger.info("[STOP] Received stop flag, breaking scroll loop.")
            break

        if i >= max_scrolls:
            logger.info("[STOP] Reach MAX_SCROLLS=%d, break loop.", max_scrolls)
            break

        total_new_from_batch = process_visible_selector_posts(
            d,
            group_url=group_url,
            seen_ids=seen_ids,
            out_path=out_path,
            log_prefix=f"#{i}",
            ts_state=ts_state,
        )

        if total_new_from_batch:
            logger.info(
                "[SEL] #%d: collected %d new posts (total_seen=%d)",
                i,
                total_new_from_batch,
                len(seen_ids),
            )

        try:
            d.execute_script(
                "window.scrollBy(0, Math.floor(window.innerHeight * 0.9));"
            )
        except Exception as e:
            logger.warning("[SCROLL] execute_script error: %s", e)
            break

        if scroll_pause_seconds > 0:
            time.sleep(scroll_pause_seconds)

        if i > 0 and (i % CLEANUP_EVERY == 0):
            try:
                d.execute_script(CLEANUP_JS, DOM_KEEP)
            except Exception:
                pass

        try:
            cur_height = get_scroll_height(d)
        except Exception:
            break

        current_seen_count = len(seen_ids)
        if cur_height <= prev_height and current_seen_count <= prev_seen_count:
            stall_count += 1
        else:
            stall_count = 0

        prev_height = cur_height
        prev_seen_count = current_seen_count

        logger.info(
            "[SCROLL] #%d height=%d total_seen=%d stable=%d/%d",
            i,
            cur_height,
            current_seen_count,
            stall_count,
            stable_rounds_required,
        )

        if stall_count >= stable_rounds_required:
            logger.info(
                "[STOP] Stable scroll detected: no new posts and height stagnant for %d rounds.",
                stall_count,
            )
            stopped_due_to_stall = True
            break

        i += 1
        if settle_pause_seconds > 0:
            time.sleep(settle_pause_seconds)

    logger.info("[DONE] Crawl loop finished. Total unique posts seen: %d", len(seen_ids))
    return stopped_due_to_stall
