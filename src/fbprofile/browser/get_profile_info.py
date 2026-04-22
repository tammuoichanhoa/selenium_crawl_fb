import json
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from logs.loging_config import logger
from src.utils.selectors import resolve_locator, validate_selector_payload
from .intro_filter import clean_intro_text
from .stable_scroll import scroll_until_stable


PROFILE_SELECTOR_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "configs" / "modules" / "profile.json"
)


@lru_cache(maxsize=1)
def _load_profile_selector_config() -> dict:
    """Load profile selectors from local JSON config."""
    with open(PROFILE_SELECTOR_CONFIG_PATH, "r", encoding="utf-8") as config_file:
        return validate_selector_payload(json.load(config_file))


def _get_selector_entry(selector_name: str) -> dict | None:
    """Return one selector entry from config."""
    return _load_profile_selector_config().get("elements", {}).get(selector_name)


def _build_locator_chain(selector_name: str, **format_kwargs) -> list[dict]:
    """Build primary + fallback locators for a selector key."""
    selector_cfg = _get_selector_entry(selector_name)
    if not isinstance(selector_cfg, dict):
        logger.warning(f"[PROFILE] Chưa cấu hình selector: {selector_name}")
        return []

    locators = []
    primary = selector_cfg.get("primary")
    if isinstance(primary, dict):
        locators.append(dict(primary))

    fallbacks = selector_cfg.get("fallbacks")
    if isinstance(fallbacks, list):
        locators.extend(dict(fallback) for fallback in fallbacks if isinstance(fallback, dict))

    if format_kwargs:
        for locator in locators:
            value = locator.get("value") or locator.get("selector")
            if isinstance(value, str):
                locator["value"] = value.format(**format_kwargs)

    return locators


def _resolve_wait(selector_name: str, timeout: int | None = None) -> tuple[str, float]:
    """Resolve wait mode + timeout from selector config."""
    selector_cfg = _get_selector_entry(selector_name) or {}
    wait_cfg = selector_cfg.get("wait")
    wait_cfg = wait_cfg if isinstance(wait_cfg, dict) else {}

    state = str(wait_cfg.get("state") or "presence").strip().lower()
    if state not in {"presence", "visible", "clickable"}:
        state = "presence"

    timeout_ms = wait_cfg.get("timeout_ms")
    try:
        seconds = float(timeout_ms) / 1000 if timeout_ms is not None else float(timeout or 5)
    except (TypeError, ValueError):
        seconds = float(timeout or 5)

    if timeout is not None:
        seconds = float(timeout)

    return state, seconds


def _find_elements_by_selector(driver, selector_name: str, timeout: int | None = None, **format_kwargs):
    """Find Selenium elements using selector config and fallback chain."""
    locators = _build_locator_chain(selector_name, **format_kwargs)
    if not locators:
        return []

    wait_state, wait_seconds = _resolve_wait(selector_name, timeout=timeout)
    condition_map = {
        "presence": EC.presence_of_element_located,
        "visible": EC.visibility_of_element_located,
        "clickable": EC.element_to_be_clickable,
    }
    condition = condition_map[wait_state]

    for locator in locators:
        try:
            by, value = resolve_locator(locator)
            WebDriverWait(driver, wait_seconds).until(condition((by, value)))
            elements = driver.find_elements(by, value)
            if elements:
                return elements
        except Exception:
            continue

    return []


def _find_child_element(root, selector_name: str, **format_kwargs):
    """Find a child element from an existing Selenium node using config."""
    for locator in _build_locator_chain(selector_name, **format_kwargs):
        try:
            by, value = resolve_locator(locator)
            return root.find_element(by, value)
        except Exception:
            continue
    return None


def _find_elements_by_selector_no_wait(driver, selector_name: str, **format_kwargs):
    """Find Selenium elements without waiting; useful for progress counting while scrolling."""
    for locator in _build_locator_chain(selector_name, **format_kwargs):
        try:
            by, value = resolve_locator(locator)
            elements = driver.find_elements(by, value)
            if elements:
                return elements
        except Exception:
            continue
    return []


def _count_unique_selector_values(
    driver,
    selector_name: str,
    *,
    attr: str = "href",
    **format_kwargs,
) -> int:
    values = set()
    for element in _find_elements_by_selector_no_wait(driver, selector_name, **format_kwargs):
        try:
            value = element.get_attribute(attr) if attr else element.text
        except Exception:
            continue
        value = value.strip() if isinstance(value, str) else value
        if value:
            values.add(value)
    return len(values)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _count_profile_friend_links_fast(driver) -> int:
    """Count friend profile links in one browser-side pass."""
    script = """
        const xpath = "//div[contains(@class, 'x1iyjqo2') and contains(@class, 'xv54qhq')]//a[@role='link' and @href]";
        const snapshot = document.evaluate(xpath, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        const values = new Set();
        for (let i = 0; i < snapshot.snapshotLength; i += 1) {
            const href = snapshot.snapshotItem(i).getAttribute("href") || "";
            if (href) values.add(href);
        }
        return values.size;
    """
    try:
        return int(driver.execute_script(script) or 0)
    except Exception:
        return _count_unique_selector_values(
            driver,
            "profile.friends.link",
            attr="href",
        )


def _extract_profile_friends_fast(driver) -> list[dict]:
    """Extract friend cards in one JS call to avoid thousands of Selenium round trips."""
    script = """
        const cardXpath = "//div[contains(@class, 'x1iyjqo2') and contains(@class, 'xv54qhq')]";
        const snapshot = document.evaluate(cardXpath, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        const rows = [];
        const seen = new Set();
        for (let i = 0; i < snapshot.snapshotLength; i += 1) {
            const card = snapshot.snapshotItem(i);
            const link = card.querySelector("a[role='link'][href]");
            if (!link) continue;
            const href = link.getAttribute("href") || "";
            const name = (link.innerText || link.textContent || "").trim();
            if (!href || !name || seen.has(href)) continue;
            seen.add(href);
            const subtitleEl = card.querySelector("div.x1gslohp");
            const avatarEl =
                card.querySelector("img") ||
                (card.previousElementSibling ? card.previousElementSibling.querySelector("img") : null);
            rows.push({
                name,
                profile_url: href,
                avatar_url: avatarEl ? avatarEl.getAttribute("src") : null,
                subtitle: subtitleEl ? (subtitleEl.innerText || subtitleEl.textContent || "").trim() : "",
            });
        }
        return rows;
    """
    try:
        rows = driver.execute_script(script) or []
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    cleaned = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        profile_url = row.get("profile_url")
        name = row.get("name")
        if not profile_url or not name or profile_url in seen:
            continue
        seen.add(profile_url)
        cleaned.append(
            {
                "name": name,
                "profile_url": profile_url,
                "avatar_url": row.get("avatar_url"),
                "subtitle": row.get("subtitle") or "",
            }
        )
    return cleaned


def _collect_visible_photo_links_fast(driver) -> set[str]:
    """Collect loaded photo links in one browser-side pass."""
    script = """
        const selectors = [
          "a[href*='photo.php']",
          "a[href*='/photo/']",
          "a[href*='fbid=']",
          "a[href*='/photos/']"
        ].join(",");
        const values = new Set();
        for (const a of document.querySelectorAll(selectors)) {
          let href = a.href || a.getAttribute("href") || "";
          if (!href) continue;
          try {
            const url = new URL(href, location.href);
            url.searchParams.delete("__cft__");
            url.searchParams.delete("__tn__");
            href = url.href;
          } catch (_) {}
          if (
            href.includes("photo.php") ||
            href.includes("/photo/") ||
            href.includes("fbid=") ||
            href.includes("/photos/")
          ) {
            values.add(href);
          }
        }
        return Array.from(values);
    """
    try:
        links = driver.execute_script(script) or []
    except Exception:
        links = []
    return {link for link in links if isinstance(link, str) and link}


def _largest_fbcdn_image_fast(driver) -> str | None:
    """Return the largest loaded fbcdn image using one JS pass."""
    script = """
        let best = null;
        let bestArea = 0;
        for (const img of document.querySelectorAll("img[src*='fbcdn.net']")) {
          const src = img.currentSrc || img.src || img.getAttribute("src") || "";
          const width = Number(img.naturalWidth || img.width || 0);
          const height = Number(img.naturalHeight || img.height || 0);
          const area = width * height;
          if (src && area > bestArea) {
            best = src;
            bestArea = area;
          }
        }
        return best;
    """
    try:
        value = driver.execute_script(script)
    except Exception:
        return None
    return value if isinstance(value, str) and value else None


def _extract_text_from_selector(driver, selector_name: str, timeout: int | None = None, **format_kwargs) -> str | None:
    """Extract stripped text from the first matched element."""
    elements = _find_elements_by_selector(driver, selector_name, timeout=timeout, **format_kwargs)
    if not elements:
        return None
    return elements[0].text.strip()


def _extract_attr_from_selector(
    driver,
    selector_name: str,
    attr: str,
    timeout: int | None = None,
    **format_kwargs,
) -> str | None:
    """Extract an attribute from the first matched element."""
    elements = _find_elements_by_selector(driver, selector_name, timeout=timeout, **format_kwargs)
    if not elements:
        return None
    return elements[0].get_attribute(attr)


def _collect_texts_from_selector(driver, selector_name: str, timeout: int | None = None, **format_kwargs) -> list[str]:
    """Collect unique, cleaned texts from matched elements."""
    values = []

    for element in _find_elements_by_selector(driver, selector_name, timeout=timeout, **format_kwargs):
        text_value = element.text.strip()
        clean_text = clean_intro_text(text_value, separator=" ")
        if not clean_text:
            continue

        if clean_text not in values:
            values.append(clean_text)

    return values

# ==========================================
# 1. BASIC INFO (Tên, Avatar, Follower)
# ==========================================
def get_name_followers_following_avatar(driver):
    """
    Lấy thông tin cơ bản: Tên, Followers, Following, Avatar, Cover và SỐ LƯỢNG BẠN BÈ.
    """
    info = {
        "name": None,
        "followers": "0",
        "following": "0",
        "friends": "0",
        "avatar_url": None,
        "cover_photo": None,
    }

    try:
        try:
            info["name"] = _extract_text_from_selector(driver, "profile.name", timeout=10)
        except Exception:
            logger.warning("[PROFILE] Không tìm thấy tên user.")

        try:
            avatar_imgs = _find_elements_by_selector(driver, "profile.avatar", timeout=10)
            for avatar_img in avatar_imgs:
                avatar_url = avatar_img.get_attribute("xlink:href") or avatar_img.get_attribute("href")
                if avatar_url and "fbcdn" in avatar_url:
                    info["avatar_url"] = avatar_url
                    break

        except Exception as exc:
            logger.warning(f"[PROFILE] Lỗi lấy Avatar: {exc}")


        try:
            friends = _extract_text_from_selector(driver, "profile.friends_count", timeout=8)
            if friends:
                info["friends"] = friends
        except Exception:
            pass

        try:
            followers = _extract_text_from_selector(driver, "profile.followers_count", timeout=8)
            if followers:
                info["followers"] = followers
        except Exception:
            pass

        try:
            following = _extract_text_from_selector(driver, "profile.following_count", timeout=8)
            if following:
                info["following"] = following
        except Exception:
            pass

        try:
            cover_photo = _extract_attr_from_selector(driver, "profile.cover", "src", timeout=10)
            if cover_photo:
                info["cover_photo"] = cover_photo
        except Exception as exc:
            logger.warning(f"[PROFILE] Lỗi lấy ảnh bìa: {exc}")

    except Exception as exc:
        logger.error(f"[PROFILE] Lỗi lấy Basic Info: {exc}")

    return info

# ==========================================
# 2. FEATURED NEWS (Tin nổi bật / Highlights)
# ==========================================
def get_profile_featured_news(driver, target_url, timeout: int = 5):
    """Lấy dữ liệu từ mục 'Đáng chú ý' (Highlights)."""
    featured_data = []

    try:
        if target_url not in driver.current_url:
            driver.get(target_url)
            time.sleep(3)

        logger.info("[PROFILE] Đang tìm các bộ sưu tập đáng chú ý...")

        collection_links = []
        try:
            elements = _find_elements_by_selector(driver, "profile.featured.collection_link", timeout=timeout)
            if not elements:
                raise TimeoutException("highlight collection links not found")

            for el in elements:
                url = el.get_attribute("href")
                title = el.text.strip()
                if not title:
                    title_element = _find_child_element(el, "profile.featured.title_clamp")
                    title = title_element.text.strip() if title_element else "Không tên"

                if url and url not in [item["url"] for item in collection_links]:
                    collection_links.append({"url": url, "title": title})
        except TimeoutException:
            logger.info("[PROFILE] Không tìm thấy mục Đáng chú ý nào.")
            return []

        logger.info(f"[PROFILE] --> Tìm thấy {len(collection_links)} bộ sưu tập.")

        for collection in collection_links:
            logger.info(f"[PROFILE] Đang quét Highlight: {collection['title']}")
            driver.get(collection["url"])
            time.sleep(4)

            try:
                buttons = _find_elements_by_selector(driver, "profile.featured.view_button", timeout=5)
                if buttons:
                    driver.execute_script("arguments[0].click();", buttons[0])
                    time.sleep(3)
            except TimeoutException:
                pass
            except Exception as exc:
                logger.warning(f"[PROFILE] ! Cảnh báo nút xem tin: {exc}")

            collection_media = []
            visited_urls = set()

            while True:
                try:
                    media_src = None
                    media_type = "unknown"

                    try:
                        media_src = _extract_attr_from_selector(
                            driver,
                            "profile.featured.story_video",
                            "src",
                            timeout=2,
                        )
                        if media_src:
                            media_type = "video"
                    except Exception:
                        pass

                    if not media_src:
                        try:
                            media_src = _extract_attr_from_selector(
                                driver,
                                "profile.featured.story_image",
                                "src",
                                timeout=2,
                            )
                            if media_src:
                                media_type = "image"
                        except Exception:
                            pass

                    if media_src and media_src not in visited_urls:
                        visited_urls.add(media_src)
                        collection_media.append({"type": media_type, "src": media_src})

                    try:
                        buttons = _find_elements_by_selector(driver, "profile.featured.next_button", timeout=2)
                        if not buttons:
                            break
                        driver.execute_script("arguments[0].click();", buttons[0])
                        time.sleep(2.5)
                    except Exception:
                        break

                except Exception:
                    break

            featured_data.append(
                {
                    "collection_title": collection["title"],
                    "collection_url": collection["url"],
                    "media_items": collection_media,
                }
            )

    except Exception as exc:
        logger.error(f"[PROFILE] Lỗi Featured News: {str(exc)}")

    return featured_data


# ==========================================
# 3. INTRODUCES (Giới thiệu / About)
# ==========================================
def _clean_section_item_text(text: str) -> str | None:
    """Normalize one about-section item and drop empty/placeholder rows."""
    text = text.strip()
    if not text or "Không có" in text or "để hiển thị" in text:
        return None
    return text.replace("\n", " - ")


def _get_section_items(driver, section_name: str) -> list[str]:
    """Read list items under a named About section header."""
    xpath = f"""
    //h2[.//span[contains(normalize-space(),"{section_name}")]]
    /following-sibling::div[@role='list'][1]
    //div[@role='listitem']
    """
    items = []

    for element in driver.find_elements(By.XPATH, xpath):
        clean_text = _clean_section_item_text(element.text)
        if clean_text and clean_text not in items:
            items.append(clean_text)

    return items


def _extract_intro_sections(driver, timeout: int = 5) -> dict:
    """Collect intro data split by visible section names."""
    data = {
        "personal_info": [],
        "contact_info": [],
    }

    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='main']"))
        )
    except TimeoutException:
        logger.info("[PAGE] Không tải được nội dung trang giới thiệu.")
        return data

    try:
        data["personal_info"] = _get_section_items(driver, "Thông tin cá nhân")
        logger.info(f"[PAGE] Tìm thấy {len(data['personal_info'])} item trong section thông tin cá nhân.")
    except Exception as exc:
        logger.debug(f"[PAGE] Lỗi khi lấy mục 'Thông tin cá nhân': {exc}")

    try:
        data["contact_info"] = _get_section_items(driver, "Thông tin liên hệ")
        logger.info(f"[PAGE] Tìm thấy {len(data['contact_info'])} item trong section thông tin liên hệ.")
    except Exception as exc:
        logger.debug(f"[PAGE] Lỗi khi lấy mục 'Thông tin liên hệ': {exc}")

    if data["personal_info"] or data["contact_info"]:
        return data

    # Fallback for layouts where the section heading is not accessible.
    try:
        items = driver.find_elements(By.CSS_SELECTOR, "div[aria-labelledby] div[role='listitem']")
        for item in items:
            clean_text = _clean_section_item_text(item.text)
            if clean_text and clean_text not in data["personal_info"]:
                data["personal_info"].append(clean_text)
        if data["personal_info"]:
            logger.info(
                f"[PAGE] Fallback: gom {len(data['personal_info'])} item vào section thông tin cá nhân."
            )
    except Exception as exc:
        logger.debug(f"[PAGE] Lỗi fallback khi lấy thông tin giới thiệu: {exc}")

    return data


# def _get_profile_introduces(driver, target_url, timeout: int = 5) -> dict:
#     """Lấy thông tin Giới thiệu và tách theo từng section."""
#     current_url = driver.current_url
#     target_about = f"{target_url}/" if "profile.php" not in target_url else f"{target_url}&sk=about"
    
#     if target_about not in current_url:
#         driver.get(target_about)
#         time.sleep(3)

#     logger.info("[PAGE] Đang quét thông tin Giới thiệu Fanpage...")
#     return _extract_intro_sections(driver, timeout=timeout)

def get_profile_introduces(driver, target_url, timeout: int = 5) -> dict:
    """Lấy thông tin giới thiệu từ trang chủ và tách theo từng section."""
    current_url = driver.current_url
    target_home = target_url.rstrip("/")
    
    if target_home not in current_url:
        driver.get(target_home)
        time.sleep(3)

    logger.info("[PAGE] Đang quét thông tin Giới thiệu Profile...")
    return _extract_intro_sections(driver, timeout=timeout)
# ==========================================
# 4. PHOTOS (Ảnh)
# ==========================================
def get_profile_pictures(driver, target_url, timeout: int = 20) -> list:
    """Lấy danh sách Ảnh."""
    image_urls = []

    try:
        target_photos = f"{target_url}/photos" if "profile.php" not in target_url else f"{target_url}&sk=photos"
        driver.get(target_photos)
        time.sleep(3)

        logger.info("[PROFILE] Đang quét danh sách ảnh...")
        try:
            img_elements = _find_elements_by_selector(driver, "profile.photos.thumb", timeout=timeout)
            if not img_elements:
                raise TimeoutException("profile photos not found")

            driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
            time.sleep(2)

            img_elements = _find_elements_by_selector(driver, "profile.photos.thumb", timeout=timeout)
            for img in img_elements:
                src = img.get_attribute("src")
                if src and "fbcdn.net" in src:
                    image_urls.append(src)
        except Exception:
            logger.info("[PROFILE] Không tìm thấy ảnh nào.")

    except Exception as exc:
        logger.error(f"[PROFILE] Lỗi lấy ảnh: {str(exc)}")

    return list(set(image_urls))

def get_profile_high_res_pictures(
    driver,
    target_url,
    timeout=5,
    max_photos=None,
    batch_size=8,
    scroll_until_stable_cfg: Dict[str, Any] | None = None,
):
    high_res_images = set()
    photos_url = f"{target_url}/photos" if "profile.php" not in target_url else f"{target_url}&sk=photos"

    driver.get(photos_url)
    time.sleep(3)

    photo_links: set[str] = set()

    def collect_count() -> int:
        photo_links.update(_collect_visible_photo_links_fast(driver))
        return len(photo_links)

    photos_scroll_cfg = dict(scroll_until_stable_cfg or {})
    for cfg_key in (
        "max_scrolls",
        "stable_rounds",
        "max_items",
        "max_seconds",
        "min_new_items",
        "slow_rounds",
        "scroll_pause_seconds",
        "settle_pause_seconds",
    ):
        photos_scroll_cfg.pop(cfg_key, None)
    env_overrides = {
        "PROFILE_PHOTOS_MAX_SCROLLS": "max_scrolls",
        "PROFILE_PHOTOS_STABLE_ROUNDS": "stable_rounds",
        "PROFILE_PHOTOS_MAX_ITEMS": "max_items",
        "PROFILE_PHOTOS_MAX_SECONDS": "max_seconds",
        "PROFILE_PHOTOS_MIN_NEW_ITEMS": "min_new_items",
        "PROFILE_PHOTOS_SLOW_ROUNDS": "slow_rounds",
    }
    for env_key, cfg_key in env_overrides.items():
        if env_key in os.environ:
            photos_scroll_cfg[cfg_key] = os.environ.get(env_key)

    scroll_result = scroll_until_stable(
        driver,
        get_progress_count=collect_count,
        log_prefix="[PROFILE][PHOTOS]",
        config=photos_scroll_cfg,
        defaults={
            "max_scrolls": _env_int("PROFILE_PHOTOS_MAX_SCROLLS", 120),
            "stable_rounds": 3,
            "scroll_pause_seconds": _env_float("PROFILE_PHOTOS_SCROLL_PAUSE_SECONDS", 1.2),
            "settle_pause_seconds": _env_float("PROFILE_PHOTOS_SETTLE_PAUSE_SECONDS", 0.3),
            "max_items": _env_int("PROFILE_PHOTOS_MAX_ITEMS", 0),
            "max_seconds": _env_float("PROFILE_PHOTOS_MAX_SECONDS", 0.0),
            "min_new_items": _env_int("PROFILE_PHOTOS_MIN_NEW_ITEMS", 0),
            "slow_rounds": _env_int("PROFILE_PHOTOS_SLOW_ROUNDS", 0),
        },
    )
    collect_count()

    photo_links = sorted(photo_links)
    if max_photos:
        photo_links = photo_links[:max_photos]

    logger.info(
        "[PROFILE][PHOTOS] Collected %s photo link(s) after %s scroll(s)",
        len(photo_links),
        scroll_result.get("iterations"),
    )
    if not photo_links:
        return []

    try:
        main_window = driver.current_window_handle
    except Exception:
        return []

    batch_size = max(1, _env_int("PROFILE_PHOTOS_BATCH_SIZE", batch_size or 8))
    wait = WebDriverWait(driver, timeout)

    for i in range(0, len(photo_links), batch_size):
        batch = photo_links[i:i + batch_size]
        logger.info("[PROFILE][PHOTOS] Resolving high-res batch %d-%d", i + 1, i + len(batch))

        try:
            for link in batch:
                try:
                    driver.execute_script("window.open(arguments[0], '_blank');", link)
                except Exception as exc:
                    logger.debug("[PROFILE][PHOTOS] open tab failed for %s: %s", link, exc)

            time.sleep(1.0)

            for window in list(driver.window_handles):
                if window == main_window:
                    continue
                try:
                    driver.switch_to.window(window)
                    try:
                        wait.until(EC.presence_of_element_located((By.XPATH, "//img[contains(@src,'fbcdn.net')]")))
                    except Exception:
                        pass

                    src = _largest_fbcdn_image_fast(driver)
                    if src:
                        high_res_images.add(src)
                except Exception as exc:
                    logger.debug("[PROFILE][PHOTOS] high-res parse failed: %s", exc)
                finally:
                    try:
                        driver.close()
                    except Exception:
                        pass

            driver.switch_to.window(main_window)
            time.sleep(0.5)

        except Exception as exc:
            logger.debug("[PROFILE][PHOTOS] batch failed: %s", exc)
            try:
                driver.switch_to.window(main_window)
            except Exception:
                pass

    return sorted(high_res_images)
# ==========================================
# 5. FRIENDS (Bạn bè)
# ==========================================
def get_profile_friends(
    driver,
    target_url,
    timeout: int = 5,
    scroll_until_stable_cfg: Dict[str, Any] | None = None,
) -> list:
    """Lấy danh sách Bạn bè (có cuộn trang)."""
    friends_list = []
    seen_profile_urls = set()

    try:
        target_friends = f"{target_url}/friends" if "profile.php" not in target_url else f"{target_url}&sk=friends"

        logger.info(f"[PROFILE] Đang truy cập danh sách bạn bè: {target_friends}")
        driver.get(target_friends)
        time.sleep(3)

        logger.info("[PROFILE] Đang cuộn danh sách bạn bè đến khi ổn định...")
        friends_scroll_cfg = dict(scroll_until_stable_cfg or {})
        for cfg_key in (
            "max_scrolls",
            "stable_rounds",
            "max_items",
            "max_seconds",
            "min_new_items",
            "slow_rounds",
            "scroll_pause_seconds",
            "settle_pause_seconds",
        ):
            friends_scroll_cfg.pop(cfg_key, None)
        env_overrides = {
            "PROFILE_FRIENDS_MAX_SCROLLS": "max_scrolls",
            "PROFILE_FRIENDS_STABLE_ROUNDS": "stable_rounds",
            "PROFILE_FRIENDS_MAX_ITEMS": "max_items",
            "PROFILE_FRIENDS_MAX_SECONDS": "max_seconds",
            "PROFILE_FRIENDS_MIN_NEW_ITEMS": "min_new_items",
            "PROFILE_FRIENDS_SLOW_ROUNDS": "slow_rounds",
        }
        for env_key, cfg_key in env_overrides.items():
            if env_key in os.environ:
                friends_scroll_cfg[cfg_key] = os.environ.get(env_key)

        scroll_result = scroll_until_stable(
            driver,
            get_progress_count=lambda: _count_profile_friend_links_fast(driver),
            log_prefix="[PROFILE][FRIENDS]",
            config=friends_scroll_cfg,
            defaults={
                "max_scrolls": _env_int("PROFILE_FRIENDS_MAX_SCROLLS", 30),
                "stable_rounds": 3,
                "scroll_pause_seconds": _env_float("PROFILE_FRIENDS_SCROLL_PAUSE_SECONDS", 1.2),
                "settle_pause_seconds": _env_float("PROFILE_FRIENDS_SETTLE_PAUSE_SECONDS", 0.3),
                "max_items": _env_int("PROFILE_FRIENDS_MAX_ITEMS", 0),
                "max_seconds": _env_float("PROFILE_FRIENDS_MAX_SECONDS", 0.0),
                "min_new_items": _env_int("PROFILE_FRIENDS_MIN_NEW_ITEMS", 0),
                "slow_rounds": _env_int("PROFILE_FRIENDS_SLOW_ROUNDS", 0),
            },
        )

        logger.info("[PROFILE] Đang trích xuất dữ liệu bạn bè...")
        fast_friends = _extract_profile_friends_fast(driver)
        if fast_friends:
            logger.info(
                "[PROFILE][FRIENDS] Fast extracted %s friend(s) after %s scroll(s)",
                len(fast_friends),
                scroll_result.get("iterations"),
            )
            return fast_friends

        info_divs = _find_elements_by_selector(driver, "profile.friends.card", timeout=timeout)

        for info in info_divs:
            try:
                friend_data = {"name": None, "profile_url": None, "avatar_url": None, "subtitle": ""}

                try:
                    link_element = _find_child_element(info, "profile.friends.link")
                    if link_element is None:
                        continue
                    friend_data["name"] = link_element.text.strip()
                    friend_data["profile_url"] = link_element.get_attribute("href")
                except Exception:
                    continue

                if not friend_data["profile_url"] or friend_data["profile_url"] in seen_profile_urls:
                    continue
                seen_profile_urls.add(friend_data["profile_url"])

                try:
                    sub_el = _find_child_element(info, "profile.friends.subtitle")
                    if sub_el is not None:
                        friend_data["subtitle"] = sub_el.text.strip()
                except Exception:
                    pass

                try:
                    avt_el = _find_child_element(info, "profile.friends.avatar")
                    if avt_el is not None:
                        friend_data["avatar_url"] = avt_el.get_attribute("src")
                except Exception:
                    pass

                if friend_data["name"]:
                    friends_list.append(friend_data)
            except Exception:
                continue

    except Exception as exc:
        logger.error(f"[PROFILE] Lỗi lấy bạn bè: {str(exc)}")

    return friends_list

# ==========================================
# MAIN ORCHESTRATOR
# ==========================================
def scrape_full_profile_info(
    driver,
    target_url: str,
    output_path: Path,
    scroll_until_stable_cfg: Dict[str, Any] | None = None,
):
    logger.info(f"--- BẮT ĐẦU QUÉT INFO PROFILE (FULL): {target_url} ---")

    include_basic_info = True
    include_featured_news = True
    include_introduction = True
    include_photos = True
    include_friends = _env_bool("PROFILE_INCLUDE_FRIENDS", True)

    full_data = {
        "url": target_url,
        "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "basic_info": {},
        "featured_news": [],
        "introduction": {},
        "photos": [],
        "friends": [],
    }

    try:
        if target_url not in driver.current_url:
            driver.get(target_url)
            time.sleep(3)

        if include_basic_info:
            full_data["basic_info"] = get_name_followers_following_avatar(driver)
            logger.info("[PROFILE] ✅ Xong Basic Info")

        if include_featured_news:
            full_data["featured_news"] = get_profile_featured_news(driver, target_url)
            logger.info(f"[PROFILE] ✅ Xong Highlights ({len(full_data['featured_news'])} bộ)")

        if include_introduction:
            full_data["introduction"] = get_profile_introduces(driver, target_url)
            logger.info("[PROFILE] ✅ Xong Introduction")

        if include_photos:
            full_data["photos"] = get_profile_high_res_pictures(
                driver,
                target_url,
                scroll_until_stable_cfg=scroll_until_stable_cfg,
            )
            logger.info(f"[PROFILE] ✅ Xong Photos ({len(full_data['photos'])} ảnh)")

        if include_friends:
            full_data["friends"] = get_profile_friends(
                driver,
                target_url,
                scroll_until_stable_cfg=scroll_until_stable_cfg,
            )
            logger.info(f"[PROFILE] ✅ Xong Friends ({len(full_data['friends'])} người)")

    except Exception as exc:
        logger.error(f"[PROFILE] ❌ Lỗi nghiêm trọng khi quét profile: {exc}")

    try:
        with open(output_path, "w", encoding="utf-8") as output_file:
            json.dump(full_data, output_file, ensure_ascii=False, indent=4)
        logger.info(f"[PROFILE] 💾 Đã lưu FULL info vào: {output_path}")
    except Exception as save_err:
        logger.error(f"[PROFILE] Không thể lưu file: {save_err}")

    return full_data
