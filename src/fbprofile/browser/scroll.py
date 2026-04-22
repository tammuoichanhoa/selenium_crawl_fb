# post/v3/browser/scroll.py
import os
import time
from pathlib import Path
from typing import Set, Dict, Any

from selenium.webdriver.common.keys import Keys

from logs.loging_config import logger
from ..browser.hooks import CLEANUP_JS, flush_gql_recs, install_early_hook
from ..pipeline import process_single_gql_rec
from .selector_posts import process_visible_selector_posts
from .stable_scroll import get_scroll_height, normalize_scroll_until_stable_cfg



_SHOULD_STOP = False
_DOTENV_VALUES: Dict[str, str] | None = None


def _load_dotenv_values(path: str = ".env") -> Dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        return {}
    values: Dict[str, str] = {}
    try:
        with env_path.open("r", encoding="utf-8") as file:
            for raw_line in file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return values


def _get_env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value not in (None, ""):
        return value

    global _DOTENV_VALUES
    if _DOTENV_VALUES is None:
        _DOTENV_VALUES = _load_dotenv_values()
    value = _DOTENV_VALUES.get(name) if _DOTENV_VALUES else None
    if value not in (None, ""):
        return value
    return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_get_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_get_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_str(name: str, default: str) -> str:
    value = _get_env(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _env_bool(name: str, default: bool = False) -> bool:
    value = _get_env(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


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


def _scroll_posts_viewport(d) -> Dict[str, Any]:
    mode = _env_str("POSTS_SCROLL_MODE", "adaptive").lower()
    if mode not in {"adaptive", "bottom", "step", "end"}:
        mode = "adaptive"
    step_viewports = max(1.0, _env_float("POSTS_SCROLL_STEP_VIEWPORTS", 2.5))
    bottom_threshold_viewports = max(
        0.5,
        _env_float("POSTS_SCROLL_BOTTOM_THRESHOLD_VIEWPORTS", 3.0),
    )
    bottom_offset_px = max(0, _env_int("POSTS_SCROLL_BOTTOM_OFFSET_PX", 0))

    result = d.execute_script(
        """
        const mode = arguments[0];
        const stepViewports = Number(arguments[1]) || 2.5;
        const bottomThresholdViewports = Number(arguments[2]) || 3.0;
        const rawBottomOffsetPx = Number(arguments[3]);
        const bottomOffsetPx = Number.isFinite(rawBottomOffsetPx) ? rawBottomOffsetPx : 120;
        const root = document.scrollingElement || document.documentElement || document.body;
        const body = document.body || root;
        const candidates = [root, document.documentElement, body, document.querySelector("[role='main']")]
          .filter(Boolean);
        const scrollables = [];
        for (const element of Array.from(document.querySelectorAll("div, main, section"))) {
          const style = window.getComputedStyle(element);
          const overflow = `${style.overflowY || ""} ${style.overflow || ""}`;
          if (!/(auto|scroll)/.test(overflow)) continue;
          if ((element.scrollHeight || 0) <= (element.clientHeight || 0) + 40) continue;
          scrollables.push(element);
          if (scrollables.length >= 8) break;
        }
        candidates.push(...scrollables);

        function metrics(element) {
          const isWindow =
            element === root || element === document.documentElement || element === body;
          const viewport = Math.max(
            1,
            isWindow
              ? (window.innerHeight || root.clientHeight || body.clientHeight || 800)
              : (element.clientHeight || 1)
          );
          const height = Math.max(
            isWindow ? root.scrollHeight || 0 : 0,
            isWindow ? body.scrollHeight || 0 : 0,
            isWindow && document.documentElement ? document.documentElement.scrollHeight || 0 : 0,
            element.scrollHeight || 0
          );
          const y = Math.max(
            0,
            isWindow ? (window.pageYOffset || root.scrollTop || body.scrollTop || 0) : (element.scrollTop || 0)
          );
          return {element, isWindow, viewport, height, y, maxY: Math.max(0, height - viewport)};
        }

        let active = metrics(root);
        for (const candidate of candidates) {
          const item = metrics(candidate);
          if (item.maxY > active.maxY) active = item;
        }

        const beforeY = active.y;
        const viewport = active.viewport;
        const height = active.height;
        const maxY = active.maxY;
        const remainingBefore = Math.max(0, maxY - beforeY);
        let targetY;

        if (mode === "bottom" || mode === "end" || remainingBefore <= viewport * bottomThresholdViewports) {
          targetY = maxY - bottomOffsetPx;
        } else {
          targetY = beforeY + viewport * stepViewports;
          if (mode === "adaptive" && maxY - targetY <= viewport * bottomThresholdViewports) {
            targetY = maxY - bottomOffsetPx;
          }
        }

        targetY = Math.max(0, Math.min(maxY, Math.floor(targetY)));
        if (active.isWindow) {
          window.scrollTo(0, targetY);
          root.scrollTop = targetY;
          body.scrollTop = targetY;
          window.dispatchEvent(new Event("scroll", {bubbles: true}));
        } else {
          active.element.scrollTop = targetY;
          active.element.dispatchEvent(new Event("scroll", {bubbles: true}));
        }

        const after = metrics(active.element);
        return {
          mode,
          beforeY,
          afterY: after.y,
          targetY,
          viewport,
          height,
          maxY,
          scroller: active.isWindow ? "window" : (active.element.tagName || "element"),
          remainingBefore,
          remainingAfter: Math.max(0, maxY - after.y)
        };
        """,
        mode,
        step_viewports,
        bottom_threshold_viewports,
        bottom_offset_px,
    )
    result = result if isinstance(result, dict) else {}
    if mode in {"bottom", "end"} or result.get("afterY") == result.get("beforeY"):
        try:
            d.execute_script("window.focus(); document.body && document.body.focus && document.body.focus();")
            d.find_element("tag name", "body").send_keys(Keys.END)
            time.sleep(0.2)
        except Exception:
            pass
        try:
            d.execute_cdp_cmd(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseWheel",
                    "x": 400,
                    "y": 400,
                    "deltaY": max(3000, int(result.get("viewport") or 1000) * 5),
                    "deltaX": 0,
                },
            )
        except Exception:
            pass
    return result


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
    CLEANUP_EVERY = max(0, _env_int("POSTS_DOM_CLEANUP_EVERY", 10))
    DOM_KEEP = max(30, min(_env_int("POSTS_DOM_KEEP", keep_last or 80), 120))
    posts_scroll_cfg = dict(scroll_until_stable_cfg or {})
    if not _env_bool("POSTS_RESPECT_GLOBAL_MAX_SCROLLS", False):
        posts_scroll_cfg.pop("max_scrolls", None)
    env_overrides = {
        "POSTS_MAX_SCROLLS": "max_scrolls",
        "POSTS_STABLE_ROUNDS": "stable_rounds",
        "POSTS_SCROLL_PAUSE_SECONDS": "scroll_pause_seconds",
        "POSTS_SETTLE_PAUSE_SECONDS": "settle_pause_seconds",
    }
    for env_key, cfg_key in env_overrides.items():
        env_value = _get_env(env_key)
        if env_value is not None:
            posts_scroll_cfg[cfg_key] = env_value

    resolved_scroll_cfg = normalize_scroll_until_stable_cfg(
        posts_scroll_cfg,
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
    logger.info(
        "[SCROLL] posts config max_scrolls=%d stable_rounds=%d pause=%.2fs settle=%.2fs mode=%s step_viewports=%.2f",
        max_scrolls,
        stable_rounds_required,
        scroll_pause_seconds,
        settle_pause_seconds,
        _env_str("POSTS_SCROLL_MODE", "adaptive"),
        max(1.0, _env_float("POSTS_SCROLL_STEP_VIEWPORTS", 2.5)),
    )

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
            scroll_state = _scroll_posts_viewport(d)
            logger.info(
                "[SCROLL] #%d move mode=%s scroller=%s y=%s->%s target=%s height=%s remaining=%s->%s viewport=%s",
                i,
                scroll_state.get("mode"),
                scroll_state.get("scroller"),
                scroll_state.get("beforeY"),
                scroll_state.get("afterY"),
                scroll_state.get("targetY"),
                scroll_state.get("height"),
                scroll_state.get("remainingBefore"),
                scroll_state.get("remainingAfter"),
                scroll_state.get("viewport"),
            )
        except Exception as e:
            logger.warning("[SCROLL] execute_script error: %s", e)
            break

        if scroll_pause_seconds > 0:
            time.sleep(scroll_pause_seconds)

        if CLEANUP_EVERY > 0 and i > 0 and (i % CLEANUP_EVERY == 0):
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
