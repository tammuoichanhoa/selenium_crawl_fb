import json
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from logs.loging_config import logger
from src.utils.selectors import resolve_locator, validate_selector_payload
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
    ignored_tokens = ("Không có", "để hiển thị")

    for element in _find_elements_by_selector(driver, selector_name, timeout=timeout, **format_kwargs):
        text_value = element.text.strip()
        if not text_value:
            continue

        clean_text = text_value.replace("\n", " ")
        if any(token in clean_text for token in ignored_tokens):
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
            for img in _find_elements_by_selector(driver, "profile.avatar", timeout=10):
                src = img.get_attribute("xlink:href") or img.get_attribute("href") or img.get_attribute("src")
                if src and "fbcdn" in src:
                    info["avatar_url"] = src
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
            cover_photo = _extract_attr_from_selector(driver, "profile.cover", "src", timeout=8)
            if cover_photo:
                info["cover_photo"] = cover_photo
        except Exception:
            pass

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
def get_profile_introduces(driver, target_url, timeout: int = 2) -> dict:
    """Lấy thông tin introduction trực tiếp từ trang profile hiện tại."""
    _ = target_url

    data = {
        "jobs": [],
        "education": [],
        "places": [],
        "contact_basic": [],
        "bio": None,
        "company": None,
        "gender": None,
    }
    logger.info("[PROFILE] Đang quét thông tin Giới thiệu...")

    list_selector_mapping = {
        "jobs": "profile.jobs",
        "education": "profile.education",
        "places": "profile.address",
        "contact_basic": "profile.contact",
    }

    scalar_selector_mapping = {
        "bio": "profile.bio",
        "company": "profile.company",
        "gender": "profile.gender",
    }

    for key, selector_name in list_selector_mapping.items():
        try:
            data[key] = _collect_texts_from_selector(driver, selector_name, timeout=timeout)
        except Exception as exc:
            logger.warning(f"[PROFILE] Lỗi lấy introduction.{key} từ {selector_name}: {exc}")

    for key, selector_name in scalar_selector_mapping.items():
        try:
            texts = _collect_texts_from_selector(driver, selector_name, timeout=timeout)
            if texts:
                data[key] = texts[0]
        except Exception as exc:
            logger.warning(f"[PROFILE] Lỗi lấy introduction.{key} từ {selector_name}: {exc}")

    return data


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
        scroll_until_stable(
            driver,
            get_progress_count=lambda: _count_unique_selector_values(
                driver,
                "profile.friends.link",
                attr="href",
            ),
            log_prefix="[PROFILE][FRIENDS]",
            config=scroll_until_stable_cfg,
            defaults={
                "max_scrolls": 50,
                "stable_rounds": 3,
                "scroll_pause_seconds": 2.0,
                "settle_pause_seconds": 0.5,
            },
        )

        logger.info("[PROFILE] Đang trích xuất dữ liệu bạn bè...")
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
    include_friends = True

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


def get_profile_high_res_pictures(
    driver,
    target_url,
    timeout=5,
    max_photos=None,
    scroll_until_stable_cfg: Dict[str, Any] | None = None,
):
    high_res_images = []
    photos_url = f"{target_url}/photos" if "profile.php" not in target_url else f"{target_url}&sk=photos"

    driver.get(photos_url)
    time.sleep(3)

    scroll_until_stable(
        driver,
        get_progress_count=lambda: _count_unique_selector_values(
            driver,
            "profile.photos.link",
            attr="href",
        ),
        log_prefix="[PROFILE][PHOTOS]",
        config=scroll_until_stable_cfg,
        defaults={
            "max_scrolls": 80,
            "stable_rounds": 3,
            "scroll_pause_seconds": 2.0,
            "settle_pause_seconds": 0.5,
        },
    )

    photo_links = set()
    photo_elements = _find_elements_by_selector(driver, "profile.photos.link", timeout=timeout)

    for el in photo_elements:
        href = el.get_attribute("href")
        if href:
            photo_links.add(href)

    photo_links = list(photo_links)
    if max_photos:
        photo_links = photo_links[:max_photos]

    for link in photo_links:
        try:
            driver.get(link)
            time.sleep(2)

            imgs = _find_elements_by_selector(driver, "profile.photos.highres_img", timeout=timeout)
            if not imgs:
                raise TimeoutException("high resolution image not found")

            max_img = None
            max_area = 0

            for img in imgs:
                try:
                    width = int(img.get_attribute("naturalWidth") or 0)
                    height = int(img.get_attribute("naturalHeight") or 0)
                    if width * height > max_area:
                        max_area = width * height
                        max_img = img
                except Exception:
                    continue

            if max_img:
                src = max_img.get_attribute("src")
                if src:
                    high_res_images.append(src)

            time.sleep(1.5)

        except Exception:
            continue

    return list(set(high_res_images))
