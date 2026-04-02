import time
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

# Import logger từ hệ thống log hiện tại
from logs.loging_config import logger
from src.utils import (
    DEFAULT_CONFIG_PATH,
    build_locator_chain,
    extract_element,
    load_config,
    normalize_elements_config,
    resolve_locator,
    validate_selector_payload,
)

PROFILE_SELECTOR_CONFIG_PATH = os.path.join("configs", "base.json")


def _resolve_profile_module(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    selectors = config.get("selectors")
    if not isinstance(selectors, dict):
        return None
    modules = selectors.get("modules")
    if not isinstance(modules, dict):
        return None
    module = modules.get("profile")
    if not isinstance(module, dict):
        return None
    return module


@lru_cache(maxsize=2)
def _load_profile_selector_context(
    config_path: str = PROFILE_SELECTOR_CONFIG_PATH,
) -> Dict[str, Any]:
    last_error: Optional[Exception] = None
    for path in (config_path, DEFAULT_CONFIG_PATH):
        try:
            config = load_config(path)
        except Exception as exc:
            last_error = exc
            continue

        module = _resolve_profile_module(config)
        if not module:
            last_error = ValueError(f"Profile selector module not found in {path}")
            continue

        validate_selector_payload(module)
        elements_cfg = normalize_elements_config(module.get("elements"))
        index = {cfg.get("name"): cfg for cfg in elements_cfg if cfg.get("name")}

        default_wait_cfg = module.get("defaults", {}).get("wait")
        if not isinstance(default_wait_cfg, dict):
            default_wait_cfg = None

        crawl_cfg = config.get("crawl", {})
        timeout = crawl_cfg.get("element_timeout", 10)
        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            timeout = 10

        debug_cfg = None
        selectors_cfg = config.get("selectors")
        if isinstance(selectors_cfg, dict):
            debug_cfg = selectors_cfg.get("debug")
            if not isinstance(debug_cfg, dict):
                debug_cfg = None

        return {
            "elements": index,
            "default_wait": default_wait_cfg,
            "timeout": timeout,
            "debug": debug_cfg,
        }

    if last_error:
        logger.warning("[PROFILE] Không thể tải selector config: %s", last_error)
    return {}


def _extract_profile_field(driver, ctx: Dict[str, Any], name: str) -> Optional[str]:
    elements = ctx.get("elements") or {}
    element_cfg = elements.get(name)
    if not isinstance(element_cfg, dict):
        return None
    try:
        return extract_element(
            driver,
            element_cfg,
            ctx.get("timeout", 10),
            ctx.get("default_wait"),
            ctx.get("debug"),
        )
    except TimeoutException:
        return None
    except Exception as exc:
        logger.warning("[PROFILE] Lỗi selector '%s': %s", name, exc)
        return None


def _find_profile_elements(driver, ctx: Dict[str, Any], name: str):
    elements = ctx.get("elements") or {}
    element_cfg = elements.get(name)
    if not isinstance(element_cfg, dict):
        return []
    for locator_cfg in build_locator_chain(element_cfg):
        try:
            by, selector = resolve_locator(locator_cfg)
            found = driver.find_elements(by, selector)
            if found:
                return found
        except Exception:
            continue
    return []


def _find_profile_element(driver, ctx: Dict[str, Any], name: str):
    elements = ctx.get("elements") or {}
    element_cfg = elements.get(name)
    if not isinstance(element_cfg, dict):
        return None
    for locator_cfg in build_locator_chain(element_cfg):
        try:
            by, selector = resolve_locator(locator_cfg)
            return driver.find_element(by, selector)
        except Exception:
            continue
    return None


def _find_profile_child_element(parent, ctx: Dict[str, Any], name: str):
    elements = ctx.get("elements") or {}
    element_cfg = elements.get(name)
    if not isinstance(element_cfg, dict):
        return None
    for locator_cfg in build_locator_chain(element_cfg):
        try:
            by, selector = resolve_locator(locator_cfg)
            return parent.find_element(by, selector)
        except Exception:
            continue
    return None

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
        "friends": "0",      # Thêm trường này
        "avatar_url": None,
        "cover_photo": None
    }
    
    try:
        selector_ctx = _load_profile_selector_context()

        if selector_ctx.get("elements"):
            info["name"] = _extract_profile_field(driver, selector_ctx, "profile.name")
            info["avatar_url"] = _extract_profile_field(driver, selector_ctx, "profile.avatar")
            info["cover_photo"] = _extract_profile_field(driver, selector_ctx, "profile.cover")
            info["friends"] = _extract_profile_field(driver, selector_ctx, "profile.friends_count") or info["friends"]
            info["followers"] = _extract_profile_field(driver, selector_ctx, "profile.followers_count") or info["followers"]
            info["following"] = _extract_profile_field(driver, selector_ctx, "profile.following_count") or info["following"]

        wait = WebDriverWait(driver, 10)
        
        # 1. Tên (Giữ nguyên)
        if not info["name"]:
            try:
                name_element = wait.until(EC.presence_of_element_located((By.TAG_NAME, "h1")))
                info["name"] = name_element.text.strip()
            except:
                logger.warning("[PROFILE] Không tìm thấy tên user.")

        # 2. Avatar (CẬP NHẬT MỚI DỰA TRÊN HTML BẠN GỬI)
        if not info["avatar_url"]:
            try:
                # Tìm thẻ <image> nằm trong <svg role="img">
                # Thuộc tính preserveAspectRatio="xMidYMid slice" rất đặc trưng cho avatar FB
                avatar_xpath = "//*[name()='svg'][@role='img']//*[name()='image']"
                
                # Lấy tất cả các element khớp (thường avatar là cái to nhất hoặc đầu tiên)
                imgs = driver.find_elements(By.XPATH, avatar_xpath)
                
                for img in imgs:
                    # Ưu tiên lấy xlink:href
                    src = img.get_attribute("xlink:href")
                    if not src:
                        src = img.get_attribute("href")
                    
                    # Link avatar thường chứa 'fbcdn' và không phải là icon nhỏ (thường icon nhỏ là .png hoặc svg base64)
                    if src and "fbcdn" in src:
                        info["avatar_url"] = src
                        break # Lấy được cái đầu tiên hợp lệ thì dừng
            except Exception as e:
                logger.warning(f"[PROFILE] Lỗi lấy Avatar: {e}")

        # 3. Số lượng Bạn bè (CẬP NHẬT MỚI)
        if info["friends"] == "0":
            try:
                # Tìm thẻ <a> có href chứa chữ 'friends'
                # HTML: <a href=".../friends/"><strong>324</strong> người bạn</a>
                friend_xpath = "//a[contains(@href, 'friends')]//strong"
                friend_element = driver.find_element(By.XPATH, friend_xpath)
                info["friends"] = friend_element.text.strip()
            except:
                # Fallback: Đôi khi nó hiện "xxx người theo dõi" ở chỗ bạn bè nếu không công khai bạn bè
                pass

        # 4. Followers (Người theo dõi - Giữ nguyên logic cũ nhưng thêm try-except lỏng hơn)
        if info["followers"] == "0":
            try:
                followers_element = driver.find_element(By.XPATH, "//a[contains(@href, 'followers')]//strong")
                info["followers"] = followers_element.text.strip()
            except:
                pass

        # 5. Following (Đang theo dõi - Giữ nguyên)
        if info["following"] == "0":
            try:
                following_element = driver.find_element(By.XPATH, "//a[contains(@href, 'following')]//strong")
                info["following"] = following_element.text.strip()
            except:
                pass

        # 6. Ảnh bìa (Giữ nguyên)
        if not info["cover_photo"]:
            try:
                cover_element = driver.find_element(By.XPATH, "//img[@data-imgperflogname='profileCoverPhoto']")
                info["cover_photo"] = cover_element.get_attribute("src")
            except:
                pass

    except Exception as e:
        logger.error(f"[PROFILE] Lỗi lấy Basic Info: {e}")
        
    return info

# ==========================================
# 2. FEATURED NEWS (Tin nổi bật / Highlights)
# ==========================================
def get_profile_featured_news(driver, target_url, timeout: int = 5):
    """Lấy dữ liệu từ mục 'Đáng chú ý' (Highlights)."""
    featured_data = []
    wait = WebDriverWait(driver, timeout)
    selector_ctx = _load_profile_selector_context()

    try:
        if target_url not in driver.current_url:
            driver.get(target_url)
            time.sleep(3)

        logger.info("[PROFILE] Đang tìm các bộ sưu tập đáng chú ý...")
        
        collection_links = []
        try:
            elements = _find_profile_elements(driver, selector_ctx, "profile.featured.collection_link")
            if not elements:
                elements = wait.until(EC.presence_of_all_elements_located(
                    (By.XPATH, "//a[contains(@href, 'source=profile_highlight')]")
                ))
            for el in elements:
                url = el.get_attribute("href")
                title = el.text.strip()
                if not title:
                    try:
                        title_el = _find_profile_child_element(el, selector_ctx, "profile.featured.title_clamp")
                        if title_el:
                            title = title_el.text
                        else:
                            title = el.find_element(By.XPATH, ".//span[contains(@style, '-webkit-line-clamp')]").text
                    except:
                        title = "Không tên"
                
                if url and url not in [x['url'] for x in collection_links]:
                    collection_links.append({"url": url, "title": title})
        except TimeoutException:
            logger.info("[PROFILE] Không tìm thấy mục Đáng chú ý nào.")
            return []

        logger.info(f"[PROFILE] --> Tìm thấy {len(collection_links)} bộ sưu tập.")

        for collection in collection_links:
            logger.info(f"[PROFILE] Đang quét Highlight: {collection['title']}")
            driver.get(collection['url'])
            time.sleep(4)

            # Xử lý nút "Nhấp để xem tin"
            try:
                overlay_wait = WebDriverWait(driver, 5)
                btn = _find_profile_element(driver, selector_ctx, "profile.featured.view_button")
                if btn is None:
                    view_btn_xpath = "//span[contains(text(), 'Nhấp để xem tin')]"
                    btn = overlay_wait.until(EC.element_to_be_clickable((By.XPATH, view_btn_xpath)))
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(3)
            except TimeoutException:
                pass
            except Exception as e:
                logger.warning(f"[PROFILE] ! Cảnh báo nút xem tin: {e}")

            collection_media = []
            visited_urls = set()

            while True:
                try:
                    media_src = None
                    media_type = "unknown"

                    try:
                        video_element = _find_profile_element(driver, selector_ctx, "profile.featured.story_video")
                        if video_element is None:
                            video_element = driver.find_element(By.TAG_NAME, "video")
                        media_src = video_element.get_attribute("src")
                        media_type = "video"
                    except:
                        try:
                            img_element = _find_profile_element(driver, selector_ctx, "profile.featured.story_image")
                            if img_element is None:
                                img_element = driver.find_element(By.XPATH, "//div[contains(@data-id, 'story-viewer')]//img")
                            media_src = img_element.get_attribute("src")
                            media_type = "image"
                        except:
                            pass

                    if media_src and media_src not in visited_urls:
                        visited_urls.add(media_src)
                        collection_media.append({"type": media_type, "src": media_src})

                    # Click Next
                    try:
                        next_btn = _find_profile_element(driver, selector_ctx, "profile.featured.next_button")
                        if next_btn is None:
                            next_xpath = "//div[@aria-label='Thẻ tiếp theo'][@role='button']"
                            next_btn = driver.find_element(By.XPATH, next_xpath)
                        driver.execute_script("arguments[0].click();", next_btn)
                        time.sleep(2.5)
                    except:
                        break # Hết story
                
                except Exception:
                    break
            
            featured_data.append({
                "collection_title": collection['title'],
                "collection_url": collection['url'],
                "media_items": collection_media
            })

    except Exception as e:
        logger.error(f"[PROFILE] Lỗi Featured News: {str(e)}")

    return featured_data

# ==========================================
# 3. INTRODUCES (Giới thiệu / About)
# ==========================================
def get_profile_introduces(driver, target_url, timeout: int = 2) -> dict:
    """Lấy thông tin Giới thiệu (About)."""
    current_url = driver.current_url
    target_about = f"{target_url}/about" if "profile.php" not in target_url else f"{target_url}&sk=about"
    
    if target_about not in current_url:
        driver.get(target_about)
        time.sleep(3)
    
    data = {}
    wait = WebDriverWait(driver, timeout)
    selector_ctx = _load_profile_selector_context()

    tabs_mapping = {
        "overview": ["Tổng quan", "Overview"],
        "work_education": ["Công việc và học vấn", "Work and education"],
        "places": ["Nơi từng sống", "Places Lived"],
        "contact_basic": ["Thông tin liên hệ và cơ bản", "Contact and basic info"],
        "family": ["Gia đình và các mối quan hệ", "Family and relationships"],
        "details": ["Chi tiết về", "Details about"],
        "life_events": ["Sự kiện trong đời", "Life events"]
    }

    logger.info("[PROFILE] Đang quét thông tin Giới thiệu...")

    for key, keywords in tabs_mapping.items():
        data[key] = []
        try:
            xpath_parts = [f"contains(text(), '{kw}')" for kw in keywords]
            xpath_condition = " or ".join(xpath_parts)
            xpath_tab = f"//a[@role='tab']//span[{xpath_condition}]"
            
            try:
                tab_element = wait.until(EC.presence_of_element_located((By.XPATH, xpath_tab)))
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", tab_element)
                driver.execute_script("arguments[0].click();", tab_element)
                time.sleep(2)
            except TimeoutException:
                continue

            # Xử lý riêng cho tab "details"
            if key == "details":
                sections = _find_profile_elements(driver, selector_ctx, "profile.about.details.section")
                if not sections:
                    sections = driver.find_elements(By.XPATH, "//div[@class='x1iyjqo2']//div[@class='xieb3on x1gslohp']")
                for sec in sections:
                    try:
                        header_el = sec.find_element(By.TAG_NAME, "h2")
                        header = header_el.text.strip()
                        content_div = sec.find_element(By.XPATH, "./following-sibling::div[contains(@class, 'xat24cr')]")
                        content_text = content_div.text.strip()
                        if "Không có" not in content_text:
                            data[key].append(f"{header}: {content_text}")
                    except:
                        continue
            else:
                rows = _find_profile_elements(driver, selector_ctx, "profile.about.rows_primary")
                if not rows:
                    rows = driver.find_elements(By.XPATH, "//div[contains(@class, 'x13faqbe')]")
                if not rows:
                    rows = _find_profile_elements(driver, selector_ctx, "profile.about.rows_fallback")
                    if not rows:
                        rows = driver.find_elements(By.XPATH, "//div[@class='x1iyjqo2']//div[@class='x1gslohp']")
                
                for row in rows:
                    text_content = row.text.strip()
                    if text_content and "Không có" not in text_content and "để hiển thị" not in text_content:
                        clean_text = text_content.replace("\n", " ")
                        if clean_text not in data[key]:
                            data[key].append(clean_text)

        except Exception:
            continue

    return data

# ==========================================
# 4. PHOTOS (Ảnh)
# ==========================================
def get_profile_pictures(driver, target_url, timeout: int = 20) -> list:
    """Lấy danh sách Ảnh."""
    image_urls = []
    wait = WebDriverWait(driver, timeout)
    selector_ctx = _load_profile_selector_context()

    try:
        target_photos = f"{target_url}/photos" if "profile.php" not in target_url else f"{target_url}&sk=photos"
        driver.get(target_photos)
        time.sleep(3)
        
        logger.info("[PROFILE] Đang quét danh sách ảnh...")
        xpath_images = "//a[contains(@href, 'photo.php')]//img"
        try:
            try:
                element = _find_profile_element(driver, selector_ctx, "profile.photos.thumb")
                if element is None:
                    wait.until(EC.presence_of_element_located((By.XPATH, xpath_images)))
            except Exception:
                wait.until(EC.presence_of_element_located((By.XPATH, xpath_images)))
            # Cuộn một chút để load thêm ảnh
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
            time.sleep(2)
            
            img_elements = _find_profile_elements(driver, selector_ctx, "profile.photos.thumb")
            if not img_elements:
                img_elements = driver.find_elements(By.XPATH, xpath_images)
            for img in img_elements:
                src = img.get_attribute("src")
                if src and "fbcdn.net" in src:
                    image_urls.append(src)
        except:
            logger.info("[PROFILE] Không tìm thấy ảnh nào.")
                
    except Exception as e:
        logger.error(f"[PROFILE] Lỗi lấy ảnh: {str(e)}")

    return list(set(image_urls))

# ==========================================
# 5. FRIENDS (Bạn bè)
# ==========================================
def get_profile_friends(driver, target_url, timeout: int = 5) -> list:
    """Lấy danh sách Bạn bè (có cuộn trang)."""
    friends_list = []
    
    selector_ctx = _load_profile_selector_context()
    try:
        target_friends = f"{target_url}/friends" if "profile.php" not in target_url else f"{target_url}&sk=friends"
            
        logger.info(f"[PROFILE] Đang truy cập danh sách bạn bè: {target_friends}")
        driver.get(target_friends)
        time.sleep(3)

        logger.info("[PROFILE] Đang cuộn trang danh sách bạn bè (Max 3 lần scroll)...")
        # Giới hạn scroll để tránh treo tool quá lâu
        last_height = driver.execute_script("return document.body.scrollHeight")
        for _ in range(3): 
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2.5)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

        logger.info("[PROFILE] Đang trích xuất dữ liệu bạn bè...")
        info_divs = _find_profile_elements(driver, selector_ctx, "profile.friends.card")
        if not info_divs:
            info_divs = driver.find_elements(By.XPATH, "//div[contains(@class, 'x1iyjqo2') and contains(@class, 'xv54qhq')]")

        for info in info_divs:
            try:
                friend_data = {"name": None, "profile_url": None, "avatar_url": None, "subtitle": ""}
                
                # Tên & Link
                try:
                    link_element = _find_profile_child_element(info, selector_ctx, "profile.friends.link")
                    if link_element is None:
                        link_element = info.find_element(By.XPATH, ".//a[@role='link']")
                    friend_data["name"] = link_element.text.strip()
                    friend_data["profile_url"] = link_element.get_attribute("href")
                except: continue

                # Subtitle
                try:
                    sub_el = _find_profile_child_element(info, selector_ctx, "profile.friends.subtitle")
                    if sub_el is None:
                        sub_el = info.find_element(By.XPATH, ".//div[contains(@class, 'x1gslohp')]")
                    friend_data["subtitle"] = sub_el.text.strip()
                except: pass

                # Avatar
                try:
                    avt_el = _find_profile_child_element(info, selector_ctx, "profile.friends.avatar")
                    if avt_el is None:
                        avt_el = info.find_element(By.XPATH, "./preceding-sibling::div//img")
                    friend_data["avatar_url"] = avt_el.get_attribute("src")
                except: pass

                if friend_data["name"]:
                    friends_list.append(friend_data)
            except: continue

    except Exception as e:
        logger.error(f"[PROFILE] Lỗi lấy bạn bè: {str(e)}")

    return friends_list

# ==========================================
# MAIN ORCHESTRATOR
# ==========================================
def scrape_full_profile_info(driver, target_url: str, output_path: Path = None) -> dict:
    """
    Hàm chính điều phối việc lấy TOÀN BỘ thông tin profile và trả về dict, lưu file nếu output_path được cung cấp.
    """
    logger.info(f"--- BẮT ĐẦU QUÉT INFO PROFILE (FULL): {target_url} ---")
    
    full_data = {
        "url": target_url,
        "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "basic_info": {},
        "featured_news": [],
        "introduction": {},
        "photos": [],
        "friends": []
    }

    try:
        # 1. Basic Info (Trang chủ)
        if target_url not in driver.current_url:
            driver.get(target_url)
            time.sleep(3)
        full_data["basic_info"] = get_name_followers_following_avatar(driver)
        logger.info("[PROFILE] ✅ Xong Basic Info")

        # 2. Featured News (Highlights) - Chạy luôn
        # Lưu ý: Hàm này tốn thời gian vì phải click xem từng story
        full_data["featured_news"] = get_profile_featured_news(driver, target_url)
        logger.info(f"[PROFILE] ✅ Xong Highlights ({len(full_data['featured_news'])} bộ)")

        # 3. Introduction (About)
        full_data["introduction"] = get_profile_introduces(driver, target_url)
        logger.info("[PROFILE] ✅ Xong Introduction")

        # 4. Photos
        # full_data["photos"] = get_profile_pictures(driver, target_url)
        full_data["photos"] = get_profile_high_res_pictures(driver, target_url)
        logger.info(f"[PROFILE] ✅ Xong Photos ({len(full_data['photos'])} ảnh)")

        # 5. Friends
        full_data["friends"] = get_profile_friends(driver, target_url)
        logger.info(f"[PROFILE] ✅ Xong Friends ({len(full_data['friends'])} người)")

    except Exception as e:
        logger.error(f"[PROFILE] ❌ Lỗi nghiêm trọng khi quét profile: {e}")
    finally:
        # Quan trọng: Dù thành công hay thất bại, lưu file lại nếu có output_path
        if output_path:
            try:
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(full_data, f, ensure_ascii=False, indent=4)
                logger.info(f"[PROFILE] 💾 Đã lưu FULL info vào: {output_path}")
            except Exception as save_err:
                logger.error(f"[PROFILE] Không thể lưu file: {save_err}")
        
        return full_data

def get_profile_high_res_pictures(driver, target_url, timeout=5, max_photos=None, batch_size=5):
    """
    Lấy link ảnh High Res của Profile bằng cách mở nhiều tab cùng lúc (Batching).
    Đã tích hợp cuộn lấy toàn bộ ảnh và fix lỗi trình duyệt không chịu tải tab ngầm.
    """
    wait = WebDriverWait(driver, timeout)
    high_res_images = set()

    photos_url = (f"{target_url}/photos" if "profile.php" not in target_url else f"{target_url}&sk=photos")

    driver.get(photos_url)
    time.sleep(3)

    # 1. Auto scroll để load TẤT CẢ ảnh
    try:
        logger.info("[PROFILE] Đang cuộn trang để lấy toàn bộ ảnh...")
    except NameError:
        pass # Phòng trường hợp bạn chưa import logger ở file này
        
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            try:
                logger.info("[PROFILE] Đã cuộn đến cuối danh sách ảnh.")
            except NameError:
                pass
            break
        last_height = new_height

    # 2. Lấy danh sách link photo.php
    photo_links = set()
    selector_ctx = _load_profile_selector_context()
    photo_elements = _find_profile_elements(driver, selector_ctx, "profile.photos.link")
    if not photo_elements:
        photo_elements = driver.find_elements(By.XPATH, "//a[contains(@href,'photo.php')]")

    for el in photo_elements:
        href = el.get_attribute("href")
        if href:
            photo_links.add(href)

    photo_links = list(photo_links)
    if max_photos:
        photo_links = photo_links[:max_photos]

    main_window = driver.current_window_handle

    # 3. Duyệt link theo từng cụm (batch)
    for i in range(0, len(photo_links), batch_size):
        batch = photo_links[i:i + batch_size]
        try:
            logger.info(f"[PROFILE] Đang xử lý cụm ảnh từ {i+1} đến {i + len(batch)}...")
        except NameError:
            pass

        # --- BƯỚC A: MỞ TẤT CẢ CÁC TAB ---
        for link in batch:
            driver.execute_script("window.open(arguments[0], '_blank');", link)
        
        time.sleep(1) # Đợi 1 giây để browser khởi tạo xong các tab

        # --- BƯỚC B: "ĐÁNH THỨC" TẤT CẢ CÁC TAB ---
        for window in driver.window_handles:
            if window != main_window:
                driver.switch_to.window(window)
                # Chỉ switch qua để kích hoạt active load

        # --- BƯỚC C: CHỜ ĐỢI ĐỒNG LOẠT ---
        # Cùng một lúc, các tab đang tự load. Cho nghỉ 4 giây.
        time.sleep(4)

        # --- BƯỚC D: THU THẬP DATA VÀ ĐÓNG TAB ---
        for window in driver.window_handles:
            if window != main_window:
                driver.switch_to.window(window)
                try:
                    fast_wait = WebDriverWait(driver, 2) 
                    try:
                        element = _find_profile_element(driver, selector_ctx, "profile.photos.highres_img")
                        if element is None:
                            fast_wait.until(EC.presence_of_element_located((By.XPATH, "//img[contains(@src,'fbcdn.net')]")))
                    except Exception:
                        fast_wait.until(EC.presence_of_element_located((By.XPATH, "//img[contains(@src,'fbcdn.net')]")))

                    imgs = _find_profile_elements(driver, selector_ctx, "profile.photos.highres_img")
                    if not imgs:
                        imgs = driver.find_elements(By.XPATH, "//img[contains(@src,'fbcdn.net')]")

                    max_img = None
                    max_area = 0

                    for img in imgs:
                        try:
                            w = int(img.get_attribute("naturalWidth") or 0)
                            h = int(img.get_attribute("naturalHeight") or 0)
                            if w * h > max_area:
                                max_area = w * h
                                max_img = img
                        except:
                            continue

                    if max_img:
                        src = max_img.get_attribute("src")
                        if src:
                            high_res_images.add(src)
                except Exception as e:
                    try:
                        logger.debug(f"[PROFILE] Lỗi khi lấy ảnh trong tab: {e}")
                    except NameError:
                        pass
                finally:
                    # Lấy xong đóng tab ngay
                    driver.close()

        # Quay lại tab chính chuẩn bị cho cụm tiếp theo
        driver.switch_to.window(main_window)
        time.sleep(1.5)

    return list(high_res_images)
