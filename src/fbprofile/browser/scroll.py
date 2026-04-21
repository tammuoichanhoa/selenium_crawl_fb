# post/v3/browser/scroll.py
import time
from pathlib import Path
from typing import Set, Dict, Any

from logs.loging_config import logger
from ..browser.hooks import CLEANUP_JS, flush_gql_recs, install_early_hook
from ..pipeline import process_single_gql_rec
from .selector_posts import process_visible_selector_posts
from .stable_scroll import get_scroll_height, normalize_scroll_until_stable_cfg



_SHOULD_STOP = False


def set_stop_flag():
    global _SHOULD_STOP
    _SHOULD_STOP = True


def _process_pending_gql_records(
    d,
    group_url: str,
    out_path: Path,
    seen_ids: Set[str],
    log_prefix: str,
    ts_state: dict = None,
) -> int:
    recs = flush_gql_recs(d)
    if not recs:
        return 0

    total_new = 0
    for rec_idx, rec in enumerate(recs):
        try:
            total_new += process_single_gql_rec(
                rec,
                group_url=group_url,
                seen_ids=seen_ids,
                out_path=out_path,
                log_prefix=f"{log_prefix}/gql{rec_idx}",
                ts_state=ts_state,
            )
        except Exception as exc:
            logger.debug("[GQL%s] process failed at index=%d: %s", log_prefix, rec_idx, exc)

    if total_new:
        logger.info(
            "[GQL%s] collected %d new posts from %d pending GraphQL record(s)",
            log_prefix,
            total_new,
            len(recs),
        )
    else:
        logger.debug("[GQL%s] drained %d GraphQL record(s), no fresh posts", log_prefix, len(recs))
    return total_new


def drain_pending_gql_records(
    d,
    group_url: str,
    out_path: Path,
    seen_ids: Set[str],
    log_prefix: str,
    ts_state: dict = None,
) -> int:
    return _process_pending_gql_records(
        d,
        group_url=group_url,
        out_path=out_path,
        seen_ids=seen_ids,
        log_prefix=log_prefix,
        ts_state=ts_state,
    )


def _collect_visible_media_hrefs(d, limit: int = 3) -> list[str]:
    try:
        hrefs = d.execute_script(
            """
            const mediaSelector = [
              "a[href*='photo.php']",
              "a[href*='/photo/']",
              "a[href*='fbid=']",
              "a[href*='/photos/']"
            ].join(",");
            const articleSelector = "div[role='article'], [data-pagelet^='FeedUnit_'], [aria-posinset]";
            const out = [];
            for (const article of document.querySelectorAll(articleSelector)) {
              if (!article || article.closest("[role='dialog']")) continue;
              const rect = article.getBoundingClientRect();
              if (!rect || rect.width < 120 || rect.height < 80) continue;
              if (rect.bottom < -200 || rect.top > window.innerHeight + 500) continue;
              const text = (article.innerText || "").trim();
              if (text.length < 10 && !article.querySelector("img")) continue;
              for (const a of article.querySelectorAll(mediaSelector)) {
                const href = a.href || a.getAttribute("href") || "";
                if (!href || !href.includes("facebook.com")) continue;
                if (href.includes("/profile.php") || href.includes("/groups/")) continue;
                out.push(href);
                break;
              }
              if (out.length >= arguments[0]) break;
            }
            return Array.from(new Set(out)).slice(0, arguments[0]);
            """,
            limit,
        )
        return [h for h in hrefs if isinstance(h, str)] if isinstance(hrefs, list) else []
    except Exception as exc:
        logger.debug("[MEDIA] collect visible media hrefs failed: %s", exc)
        return []


def _probe_visible_media_viewers(
    d,
    group_url: str,
    out_path: Path,
    seen_ids: Set[str],
    probed_media_hrefs: Set[str],
    log_prefix: str,
    ts_state: dict = None,
    limit: int = 3,
) -> int:
    hrefs = [
        href for href in _collect_visible_media_hrefs(d, limit=limit)
        if href not in probed_media_hrefs
    ]
    if not hrefs:
        return 0

    try:
        original_handle = d.current_window_handle
    except Exception:
        original_handle = None

    total_new = 0
    for idx, href in enumerate(hrefs):
        probed_media_hrefs.add(href)
        try:
            try:
                d.switch_to.new_window("tab")
            except Exception:
                d.execute_script("window.open('about:blank', '_blank');")
                d.switch_to.window(d.window_handles[-1])

            install_early_hook(d, keep_last=350)
            d.get(href)
            time.sleep(2.0)
            total_new += _process_pending_gql_records(
                d,
                group_url=group_url,
                seen_ids=seen_ids,
                out_path=out_path,
                log_prefix=f"{log_prefix}/media{idx}",
                ts_state=ts_state,
            )
        except Exception as exc:
            logger.debug("[MEDIA%s] probe failed for %s: %s", log_prefix, href, exc)
        finally:
            try:
                if original_handle and d.current_window_handle != original_handle:
                    d.close()
                    d.switch_to.window(original_handle)
            except Exception:
                try:
                    if original_handle:
                        d.switch_to.window(original_handle)
                except Exception:
                    pass

    if total_new:
        logger.info("[MEDIA%s] collected %d new posts from media viewer probes", log_prefix, total_new)
    return total_new


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
    probed_media_hrefs: Set[str] = set()

    while True:
        if _SHOULD_STOP:
            logger.info("[STOP] Received stop flag, breaking scroll loop.")
            break

        if i >= max_scrolls:
            logger.info("[STOP] Reach MAX_SCROLLS=%d, break loop.", max_scrolls)
            break

        log_prefix = f"#{i}"
        gql_new = _process_pending_gql_records(
            d,
            group_url=group_url,
            seen_ids=seen_ids,
            out_path=out_path,
            log_prefix=log_prefix,
            ts_state=ts_state,
        )

        selector_new = process_visible_selector_posts(
            d,
            group_url=group_url,
            seen_ids=seen_ids,
            out_path=out_path,
            log_prefix=log_prefix,
            ts_state=ts_state,
        )
        media_new = _probe_visible_media_viewers(
            d,
            group_url=group_url,
            out_path=out_path,
            seen_ids=seen_ids,
            probed_media_hrefs=probed_media_hrefs,
            log_prefix=log_prefix,
            ts_state=ts_state,
            limit=3 if i == 0 else 1,
        )
        total_new_from_batch = gql_new + selector_new + media_new

        if total_new_from_batch:
            logger.info(
                "[SCROLL] #%d collected %d new posts (gql=%d selector=%d media=%d total_seen=%d)",
                i,
                total_new_from_batch,
                gql_new,
                selector_new,
                media_new,
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
