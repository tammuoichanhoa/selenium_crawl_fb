import time
import json
import os
from pathlib import Path
from typing import Any, Dict
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.common.exceptions import NoSuchElementException
# Import logger từ hệ thống log hiện tại
from logs.loging_config import logger
from .intro_filter import clean_intro_text
from .stable_scroll import scroll_until_stable


def _count_unique_xpath_values(driver, xpath: str, attr: str = "href") -> int:
    values = set()
    for element in driver.find_elements(By.XPATH, xpath):
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


def _count_page_follower_links_fast(driver) -> int:
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
        return _count_unique_xpath_values(
            driver,
            "//div[contains(@class, 'x1iyjqo2') and contains(@class, 'xv54qhq')]//a[@role='link']",
            attr="href",
        )


def _extract_page_followers_fast(driver) -> list[dict]:
    """Extract follower cards in one JS call to avoid thousands of Selenium calls."""
    script = """
        const cardXpath = "//div[contains(@class, 'x1iyjqo2') and contains(@class, 'xv54qhq')]";
        const snapshot = document.evaluate(cardXpath, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        const rows = [];
        const seen = new Set();
        for (let i = 0; i < snapshot.snapshotLength; i += 1) {
          const card = snapshot.snapshotItem(i);
          const link = card.querySelector("a[role='link'][href]");
          if (!link) continue;
          const href = link.href || link.getAttribute("href") || "";
          const name = (link.innerText || link.textContent || "").trim();
          if (!href || !name || seen.has(href)) continue;
          seen.add(href);
          const subtitleEl = card.querySelector("div.x1gslohp");
          const avatarEl =
            card.querySelector("img") ||
            (card.previousElementSibling ? card.previousElementSibling.querySelector("img") : null);
          rows.push({
            name,
            page_url: href,
            avatar_url: avatarEl ? (avatarEl.currentSrc || avatarEl.src || avatarEl.getAttribute("src")) : null,
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
        page_url = row.get("page_url")
        name = row.get("name")
        if not page_url or not name or page_url in seen:
            continue
        seen.add(page_url)
        cleaned.append(
            {
                "name": name,
                "page_url": page_url,
                "avatar_url": row.get("avatar_url"),
                "subtitle": row.get("subtitle") or "",
            }
        )
    return cleaned

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
        wait = WebDriverWait(driver, 10)
        
        # 1. Tên (Giữ nguyên)
        try:
            name_element = wait.until(EC.presence_of_element_located((By.TAG_NAME, "h1")))
            info["name"] = name_element.text.strip()
        except:
            logger.warning("[PAGE] Không tìm thấy tên user.")

        # 2. Avatar (CẬP NHẬT MỚI DỰA TRÊN HTML BẠN GỬI)
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
            logger.warning(f"[PAGE] Lỗi lấy Avatar: {e}")

        # 3. Số lượng Bạn bè (CẬP NHẬT MỚI)
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
        try:
            followers_element = driver.find_element(By.XPATH, "//a[contains(@href, 'followers')]//strong")
            info["followers"] = followers_element.text.strip()
        except:
            pass

        # 5. Following (Đang theo dõi - Giữ nguyên)
        try:
            following_element = driver.find_element(By.XPATH, "//a[contains(@href, 'following')]//strong")
            info["following"] = following_element.text.strip()
        except:
            pass

        # 6. Ảnh bìa (Giữ nguyên)
        try:
            cover_element = driver.find_element(By.XPATH, "//img[@data-imgperflogname='PAGECoverPhoto']")
            info["cover_photo"] = cover_element.get_attribute("src")
        except:
            pass

    except Exception as e:
        logger.error(f"[PAGE] Lỗi lấy Basic Info: {e}")
        
    return info

# ==========================================
# 2. FEATURED NEWS (Tin nổi bật / Highlights)
# ==========================================
def get_page_featured_news(driver, target_url, timeout: int = 5, batch_size: int = 3):
    """
    Lấy dữ liệu từ mục 'Đáng chú ý' (Highlights) bằng cơ chế Tab Batching.
    Sử dụng phím điều hướng để vượt qua các overlay che nút bấm.
    """
    featured_data = []
    wait = WebDriverWait(driver, timeout)

    try:
        if target_url not in driver.current_url:
            driver.get(target_url)
            time.sleep(3)

        try:
            logger.info("[PAGE] Đang tìm các bộ sưu tập đáng chú ý...")
        except NameError:
            pass
        
        collection_links = []
        try:
            elements = wait.until(EC.presence_of_all_elements_located(
                (By.XPATH, "//a[contains(@href, 'source=page_highlight')]")
            ))
            for el in elements:
                url = el.get_attribute("href")
                title = el.text.strip()
                if not title:
                    try:
                        title = el.find_element(By.XPATH, ".//span[contains(@style, '-webkit-line-clamp')]").text
                    except:
                        title = "Không tên"
                
                if url and url not in [x['url'] for x in collection_links]:
                    collection_links.append({"url": url, "title": title})
        except TimeoutException:
            try:
                logger.info("[PAGE] Không tìm thấy mục Đáng chú ý nào.")
            except NameError:
                pass
            return []

        try:
            logger.info(f"[PAGE] --> Tìm thấy {len(collection_links)} bộ sưu tập.")
        except NameError:
            pass

        main_window = driver.current_window_handle

        # QUÉT THEO CỤM (BATCHING) THAY VÌ ĐA LUỒNG THUẦN TÚY
        for i in range(0, len(collection_links), batch_size):
            batch = collection_links[i:i + batch_size]
            
            # 1. Mở tất cả URL trong batch bằng tab mới
            for collection in batch:
                driver.execute_script("window.open(arguments[0], '_blank');", collection['url'])
            
            time.sleep(2) # Đợi các tab khởi tạo

            # 2. Đi qua từng tab để bóc tách dữ liệu
            for window in driver.window_handles:
                if window != main_window:
                    driver.switch_to.window(window)
                    
                    # Tìm thông tin title/url của tab hiện tại để lưu
                    current_tab_url = driver.current_url
                    matched_collection = next((c for c in batch if c['url'] in current_tab_url), {"title": "Không rõ", "url": current_tab_url})
                    
                    collection_media = []
                    visited_urls = set()

                    # Xử lý nút "Nhấp để xem tin" nếu nó hiện ra (dùng JS Click phá overlay)
                    try:
                        view_btn_xpath = "//*[contains(text(), 'Nhấp để xem tin') or contains(text(), 'Click to view')]"
                        overlay_wait = WebDriverWait(driver, 3)
                        btn = overlay_wait.until(EC.presence_of_element_located((By.XPATH, view_btn_xpath)))
                        driver.execute_script("arguments[0].click();", btn)
                        time.sleep(1)
                    except:
                        pass # Không có thì bỏ qua luôn, không sao

                    # Bắt đầu quét Story
                    while True:
                        try:
                            # Đợi thẻ img hoặc video của story xuất hiện
                            WebDriverWait(driver, 3).until(
                                EC.presence_of_element_located((By.XPATH, "//div[contains(@data-id, 'story-viewer')]//img | //video"))
                            )
                            
                            media_src = None
                            media_type = "unknown"

                            try:
                                video_element = driver.find_element(By.TAG_NAME, "video")
                                media_src = video_element.get_attribute("src")
                                media_type = "video"
                            except:
                                try:
                                    img_element = driver.find_element(By.XPATH, "//div[contains(@data-id, 'story-viewer')]//img")
                                    media_src = img_element.get_attribute("src")
                                    media_type = "image"
                                except:
                                    pass

                            if media_src and media_src not in visited_urls:
                                visited_urls.add(media_src)
                                collection_media.append({"type": media_type, "src": media_src})

                            # KIỂM TRA XEM CÒN Ở TRONG STORY KHÔNG
                            # Nếu URL đổi về dạng trang chủ/page profile (mất chữ 'stories'), tức là đã hết bộ sưu tập
                            if "stories" not in driver.current_url:
                                break

                            # FIX LỖI CLICK: DÙNG PHÍM MŨI TÊN PHẢI ĐỂ NEXT TỚI STORY TIẾP THEO
                            # Cách này bất chấp mọi overlay hay class name bị đổi
                            ActionChains(driver).send_keys(Keys.ARROW_RIGHT).perform()
                            
                            # Ngủ ngắn một chút để story kịp chuyển cảnh
                            time.sleep(1.5) 
                            
                        except Exception:
                            # Nếu quá thời gian không tìm thấy gì hoặc lỗi, coi như hết story
                            break
                    
                    # Lưu data
                    if collection_media:
                        featured_data.append({
                            "collection_title": matched_collection['title'],
                            "collection_url": matched_collection['url'],
                            "media_items": collection_media
                        })
                    
                    # Quét xong tab này thì đóng lại
                    driver.close()

            # Quay về tab chính chuẩn bị cho mẻ (batch) tiếp theo
            driver.switch_to.window(main_window)
            time.sleep(1)

    except Exception as e:
        try:
            logger.error(f"[PAGE] Lỗi Featured News: {str(e)}")
        except NameError:
            print(f"[PAGE] Lỗi Featured News: {str(e)}")

    return featured_data
# ==========================================
# 3. INTRODUCES (Giới thiệu / About)
# ==========================================
def get_page_introduces(driver, target_url, timeout: int = 5) -> dict:
    """Lấy thông tin cá nhân từ trang chủ bằng cách đọc các listitem."""
    current_url = driver.current_url or ""
    target_home = target_url.rstrip("/")
    
    if target_home not in current_url:
        driver.get(target_home)
        time.sleep(3)
    
    data = {"personal_info": []}
    wait = WebDriverWait(driver, timeout)

    logger.info("[PAGE] Đang quét thông tin Giới thiệu Fanpage...")

    try:
        list_item_selector = "div[aria-labelledby] div[role='listitem']"
        try:
            wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, list_item_selector)))
        except TimeoutException:
            logger.debug("[PAGE] Không tìm thấy listitem trong section thông tin cá nhân.")

        items = driver.find_elements(By.CSS_SELECTOR, list_item_selector)
        logger.info(f"[PAGE] Tìm thấy {len(items)} item trong section thông tin cá nhân.")

        for item in items:
            clean_text = clean_intro_text(item.text, separator=" - ")
            if clean_text and clean_text not in data["personal_info"]:
                data["personal_info"].append(clean_text)

        # Fallback cho layout có icon: nội dung nằm ở div kế tiếp icon.
        row_xpath = "//img[contains(@class, 'x1b0d499') or @height='24']/parent::div/following-sibling::div"
        rows = driver.find_elements(By.XPATH, row_xpath)
        for row in rows:
            clean_text = clean_intro_text(row.text, separator=" - ")
            if clean_text and clean_text not in data["personal_info"]:
                data["personal_info"].append(clean_text)

    except Exception as e:
        logger.debug(f"[PAGE] Lỗi khi quét thông tin cá nhân: {e}")

    return data

# ==========================================
# 4. PHOTOS (Ảnh)
# ==========================================
def get_page_pictures(driver, target_url, timeout: int = 20) -> list:
    """Lấy danh sách Ảnh."""
    image_urls = []
    wait = WebDriverWait(driver, timeout)

    try:
        target_photos = f"{target_url}/photos" if "profile.php" not in target_url else f"{target_url}&sk=photos"
        driver.get(target_photos)
        time.sleep(3)
        
        logger.info("[PAGE] Đang quét danh sách ảnh...")
        xpath_images = "//a[contains(@href, 'photo.php')]//img"
        try:
            wait.until(EC.presence_of_element_located((By.XPATH, xpath_images)))
            # Cuộn một chút để load thêm ảnh
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
            time.sleep(2)
            
            img_elements = driver.find_elements(By.XPATH, xpath_images)
            for img in img_elements:
                src = img.get_attribute("src")
                if src and "fbcdn.net" in src:
                    image_urls.append(src)
        except:
            logger.info("[PAGE] Không tìm thấy ảnh nào.")
                
    except Exception as e:
        logger.error(f"[PAGE] Lỗi lấy ảnh: {str(e)}")

    return list(set(image_urls))

# ==========================================
# 5. FRIENDS (Bạn bè)
# ==========================================
def _get_page_followers_legacy(driver, target_url, timeout: int = 5, scroll_until_stable_cfg=None) -> list:
    """Lấy danh sách Người theo dõi (Followers) trên Fanpage (có cuộn trang)."""
    followers_list = []
    
    try:
        # Fanpage dùng followers thay vì friends
        target_followers = f"{target_url}/followers" if "profile.php" not in target_url else f"{target_url}&sk=followers"
            
        logger.info(f"[PAGE] Đang truy cập danh sách người theo dõi: {target_followers}")
        driver.get(target_followers)
        time.sleep(3)

        logger.info("[PAGE] Đang cuộn trang danh sách người theo dõi (Max 3 lần scroll)...")
        # Giới hạn scroll để tránh treo tool quá lâu
        last_height = driver.execute_script("return document.body.scrollHeight")
        for _ in range(3): 
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2.5)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

        logger.info("[PAGE] Đang trích xuất dữ liệu người theo dõi...")
        info_divs = driver.find_elements(By.XPATH, "//div[contains(@class, 'x1iyjqo2') and contains(@class, 'xv54qhq')]")
        info_divs_html = [info.get_attribute('outerHTML') for info in info_divs]
        with open("info_divs_html.txt", "w", encoding="utf-8") as f:
            f.write(str(info_divs_html))
        for info in info_divs_html:
            try:
                follower_data = {"name": "", "page_url": "", "avatar_url": "", "subtitle": ""}
                
                # 1. Tên & Link
                try:
                    link_element = info.find_element(By.XPATH, ".//a[@role='link']")
                    # Dùng textContent hoặc innerText để lấy dữ liệu kể cả khi element bị khuất khỏi màn hình
                    raw_name = link_element.get_attribute("textContent") 
                    follower_data["name"] = raw_name.strip() if raw_name else ""
                    follower_data["page_url"] = link_element.get_attribute("href")
                except NoSuchElementException:
                    # Nếu không có link, có thể div này không phải là user, bỏ qua luôn
                    continue

                # Nếu không lấy được tên, bỏ qua để tránh rác dữ liệu
                if not follower_data["name"]:
                    continue

                # 2. Subtitle
                try:
                    sub_el = info.find_element(By.XPATH, ".//div[contains(@class, 'x1gslohp')]")
                    raw_sub = sub_el.get_attribute("textContent")
                    follower_data["subtitle"] = raw_sub.strip() if raw_sub else ""
                except NoSuchElementException:
                    pass

                # 3. Avatar (Nên cân nhắc đổi XPath này nếu info là thẻ bao ngoài cùng)
                try:
                    # Ưu tiên tìm thẻ img ngay bên trong wrapper của người đó, thay vì dùng sibling
                    # Nếu 'info' chỉ chứa text, XPath của bạn: "./preceding-sibling::div//img"
                    avt_el = info.find_element(By.XPATH, "./preceding-sibling::div//img") 
                    follower_data["avatar_url"] = avt_el.get_attribute("src")
                except NoSuchElementException:
                    pass

                # Lưu lại kết quả
                followers_list.append(follower_data)
                logger.info(f"[PAGE] Đã trích xuất thành công: {follower_data['name']}")

            except Exception as e:
                logger.warning(f"Lỗi không xác định khi parse HTML 1 user: {e}")
                continue

    except Exception as e:
        logger.error(f"[PAGE] Lỗi lấy người theo dõi: {str(e)}")

    return followers_list


def get_page_followers(driver, target_url, timeout: int = 5, scroll_until_stable_cfg=None) -> list:
    """Collect fanpage followers with browser-side batching and stable scrolling."""
    followers_list = []

    try:
        target_followers = f"{target_url}/followers" if "profile.php" not in target_url else f"{target_url}&sk=followers"

        logger.info("[PAGE] Dang truy cap danh sach followers: %s", target_followers)
        driver.get(target_followers)
        time.sleep(3)

        followers_scroll_cfg = dict(scroll_until_stable_cfg or {})
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
            followers_scroll_cfg.pop(cfg_key, None)
        env_overrides = {
            "PAGE_FOLLOWERS_MAX_SCROLLS": "max_scrolls",
            "PAGE_FOLLOWERS_STABLE_ROUNDS": "stable_rounds",
            "PAGE_FOLLOWERS_MAX_ITEMS": "max_items",
            "PAGE_FOLLOWERS_MAX_SECONDS": "max_seconds",
            "PAGE_FOLLOWERS_MIN_NEW_ITEMS": "min_new_items",
            "PAGE_FOLLOWERS_SLOW_ROUNDS": "slow_rounds",
        }
        for env_key, cfg_key in env_overrides.items():
            if env_key in os.environ:
                followers_scroll_cfg[cfg_key] = os.environ.get(env_key)

        scroll_result = scroll_until_stable(
            driver,
            get_progress_count=lambda: _count_page_follower_links_fast(driver),
            log_prefix="[PAGE][FOLLOWERS]",
            config=followers_scroll_cfg,
            defaults={
                "max_scrolls": _env_int("PAGE_FOLLOWERS_MAX_SCROLLS", 80),
                "stable_rounds": 3,
                "scroll_pause_seconds": _env_float("PAGE_FOLLOWERS_SCROLL_PAUSE_SECONDS", 1.2),
                "settle_pause_seconds": _env_float("PAGE_FOLLOWERS_SETTLE_PAUSE_SECONDS", 0.3),
                "max_items": _env_int("PAGE_FOLLOWERS_MAX_ITEMS", 0),
                "max_seconds": _env_float("PAGE_FOLLOWERS_MAX_SECONDS", 0.0),
                "min_new_items": _env_int("PAGE_FOLLOWERS_MIN_NEW_ITEMS", 0),
                "slow_rounds": _env_int("PAGE_FOLLOWERS_SLOW_ROUNDS", 0),
            },
        )

        fast_followers = _extract_page_followers_fast(driver)
        if fast_followers:
            logger.info(
                "[PAGE][FOLLOWERS] Fast extracted %s follower(s) after %s scroll(s)",
                len(fast_followers),
                scroll_result.get("iterations"),
            )
            return fast_followers

        seen_page_urls = set()
        for info in driver.find_elements(By.XPATH, "//div[contains(@class, 'x1iyjqo2') and contains(@class, 'xv54qhq')]"):
            try:
                link_element = info.find_element(By.XPATH, ".//a[@role='link']")
                raw_name = link_element.get_attribute("textContent")
                page_url = link_element.get_attribute("href")
                name = raw_name.strip() if raw_name else ""
                if not name or not page_url or page_url in seen_page_urls:
                    continue
                seen_page_urls.add(page_url)

                follower_data = {
                    "name": name,
                    "page_url": page_url,
                    "avatar_url": "",
                    "subtitle": "",
                }

                try:
                    sub_el = info.find_element(By.XPATH, ".//div[contains(@class, 'x1gslohp')]")
                    raw_sub = sub_el.get_attribute("textContent")
                    follower_data["subtitle"] = raw_sub.strip() if raw_sub else ""
                except NoSuchElementException:
                    pass

                try:
                    avt_el = info.find_element(By.XPATH, "./preceding-sibling::div//img")
                    follower_data["avatar_url"] = avt_el.get_attribute("src")
                except NoSuchElementException:
                    pass

                followers_list.append(follower_data)
            except Exception as exc:
                logger.debug("[PAGE][FOLLOWERS] fallback skipped one card: %s", exc)

    except Exception as exc:
        logger.error("[PAGE] Loi lay followers: %s", exc)

    return followers_list

# ==========================================
# MAIN ORCHESTRATOR
# ==========================================
def scrape_full_page_info(
    driver,
    target_url: str,
    output_path: Path = None,
    scroll_until_stable_cfg=None,
) -> dict:
    """
    Hàm chính điều phối việc lấy TOÀN BỘ thông tin PAGE và trả về dict, lưu file nếu output_path được cung cấp.
    """
    logger.info(f"--- BẮT ĐẦU QUÉT INFO PAGE (FULL): {target_url} ---")
    
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
        logger.info("[PAGE] ✅ Xong Basic Info")

        # 2. Featured News (Highlights) - Chạy luôn
        # Lưu ý: Hàm này tốn thời gian vì phải click xem từng story
        full_data["featured_news"] = get_page_featured_news(driver, target_url)
        logger.info(f"[PAGE] ✅ Xong Highlights ({len(full_data['featured_news'])} bộ)")

        # 3. Introduction (About)
        full_data["introduction"] = get_page_introduces(driver, target_url)
        logger.info("[PAGE] ✅ Xong Introduction")

        # 4. Photos
        # full_data["photos"] = get_PAGE_pictures(driver, target_url)
        full_data["photos"] = get_page_high_res_pictures(
            driver,
            target_url,
            scroll_until_stable_cfg=scroll_until_stable_cfg,
        )
        logger.info(f"[PAGE] ✅ Xong Photos ({len(full_data['photos'])} ảnh)")

        # 5. Followers (thay cho Friends)
        full_data["followers_list"] = get_page_followers(
            driver,
            target_url,
            scroll_until_stable_cfg=scroll_until_stable_cfg,
        )
        logger.info(f"[PAGE] ✅ Xong Followers ({len(full_data.get('followers_list', []))} người)")

    except Exception as e:
        logger.error(f"[PAGE] ❌ Lỗi nghiêm trọng khi quét PAGE: {e}")
    finally:
        # Quan trọng: Dù thành công hay thất bại, lưu file lại nếu có output_path
        if output_path:
            try:
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(full_data, f, ensure_ascii=False, indent=4)
                logger.info(f"[PAGE] 💾 Đã lưu FULL info vào: {output_path}")
            except Exception as save_err:
                logger.error(f"[PAGE] Không thể lưu file: {save_err}")
        
        return full_data


def _get_page_high_res_pictures_legacy(
    driver,
    target_url,
    timeout=5,
    max_photos=None,
    batch_size=10,
    scroll_until_stable_cfg=None,
):
    """
    Lấy link ảnh High Res bằng cách mở nhiều tab cùng lúc (Batching).
    Đã fix lỗi trình duyệt không chịu tải ảnh ở các tab ngầm.
    """
    wait = WebDriverWait(driver, timeout)
    high_res_images = set()

    photos_url = (f"{target_url}/photos" if "profile.php" not in target_url else f"{target_url}&sk=photos")

    driver.get(photos_url)
    time.sleep(3)

    # 1. Auto scroll để load TẤT CẢ ảnh
    logger.info("[PAGE] Đang cuộn trang để lấy toàn bộ ảnh...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    
    while True:
        # Cuộn xuống cuối trang
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        
        # Đợi Facebook load thêm ảnh mới (có thể tăng lên 3s nếu mạng chậm)
        time.sleep(2) 
        
        # Tính toán lại chiều cao trang sau khi cuộn
        new_height = driver.execute_script("return document.body.scrollHeight")
        
        # Nếu chiều cao không thay đổi tức là đã chạm đáy (hết ảnh)
        if new_height == last_height:
            logger.info("[PAGE] Đã cuộn đến cuối danh sách ảnh.")
            break
            
        last_height = new_height

    # 2. Lấy danh sách link photo.php
    photo_links = set()
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
        logger.info(f"[PAGE] Đang xử lý cụm ảnh từ {i+1} đến {i + len(batch)}...")

        # --- BƯỚC A: MỞ TẤT CẢ CÁC TAB ---
        for link in batch:
            # Dùng arguments[0] an toàn hơn f-string để tránh lỗi parser với URL
            driver.execute_script("window.open(arguments[0], '_blank');", link)
        
        time.sleep(1) # Đợi 1 giây để browser khởi tạo xong các tab

        # --- BƯỚC B: "ĐÁNH THỨC" TẤT CẢ CÁC TAB ---
        # Lướt qua từng tab cực nhanh để ép trình duyệt phải load FB song song
        for window in driver.window_handles:
            if window != main_window:
                driver.switch_to.window(window)
                # Không làm gì ở đây cả, chỉ switch qua để kích hoạt active load

        # --- BƯỚC C: CHỜ ĐỢI ĐỒNG LOẠT ---
        # Cùng một lúc, cả 5 tab đang tự load ảnh. Ta cho nghỉ 3-4 giây.
        time.sleep(4)

        # --- BƯỚC D: THU THẬP DATA VÀ ĐÓNG TAB ---
        # Lúc này ảnh (fbcdn) ở các tab phần lớn đã load xong, vào lấy sẽ rất nhanh
        for window in driver.window_handles:
            if window != main_window:
                driver.switch_to.window(window)
                try:
                    # Giảm timeout xuống vì thời gian đợi chung (4s) ở trên đã gánh bớt
                    fast_wait = WebDriverWait(driver, 2) 
                    fast_wait.until(EC.presence_of_element_located((By.XPATH, "//img[contains(@src,'fbcdn.net')]")))
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
                    logger.debug(f"[PAGE] Lỗi khi lấy ảnh trong tab: {e}")
                finally:
                    # Lấy xong đóng tab ngay
                    driver.close()

        # Quay lại tab chính chuẩn bị cho cụm tiếp theo
        driver.switch_to.window(main_window)
        time.sleep(1.5)

    return list(high_res_images)


def get_page_high_res_pictures(
    driver,
    target_url,
    timeout=5,
    max_photos=None,
    batch_size=10,
    scroll_until_stable_cfg: Dict[str, Any] | None = None,
):
    """Collect page photo links incrementally, then resolve high-res images in batches."""
    wait = WebDriverWait(driver, timeout)
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
        "PAGE_PHOTOS_MAX_SCROLLS": "max_scrolls",
        "PAGE_PHOTOS_STABLE_ROUNDS": "stable_rounds",
        "PAGE_PHOTOS_MAX_ITEMS": "max_items",
        "PAGE_PHOTOS_MAX_SECONDS": "max_seconds",
        "PAGE_PHOTOS_MIN_NEW_ITEMS": "min_new_items",
        "PAGE_PHOTOS_SLOW_ROUNDS": "slow_rounds",
    }
    for env_key, cfg_key in env_overrides.items():
        if env_key in os.environ:
            photos_scroll_cfg[cfg_key] = os.environ.get(env_key)

    scroll_result = scroll_until_stable(
        driver,
        get_progress_count=collect_count,
        log_prefix="[PAGE][PHOTOS]",
        config=photos_scroll_cfg,
        defaults={
            "max_scrolls": _env_int("PAGE_PHOTOS_MAX_SCROLLS", 120),
            "stable_rounds": 3,
            "scroll_pause_seconds": _env_float("PAGE_PHOTOS_SCROLL_PAUSE_SECONDS", 1.2),
            "settle_pause_seconds": _env_float("PAGE_PHOTOS_SETTLE_PAUSE_SECONDS", 0.3),
            "max_items": _env_int("PAGE_PHOTOS_MAX_ITEMS", 0),
            "max_seconds": _env_float("PAGE_PHOTOS_MAX_SECONDS", 0.0),
            "min_new_items": _env_int("PAGE_PHOTOS_MIN_NEW_ITEMS", 0),
            "slow_rounds": _env_int("PAGE_PHOTOS_SLOW_ROUNDS", 0),
        },
    )
    collect_count()

    photo_links_list = sorted(photo_links)
    if max_photos:
        photo_links_list = photo_links_list[:max_photos]

    logger.info(
        "[PAGE][PHOTOS] Collected %s photo link(s) after %s scroll(s)",
        len(photo_links_list),
        scroll_result.get("iterations"),
    )
    if not photo_links_list:
        return []

    try:
        main_window = driver.current_window_handle
    except Exception:
        return []

    batch_size = max(1, _env_int("PAGE_PHOTOS_BATCH_SIZE", batch_size or 10))
    for i in range(0, len(photo_links_list), batch_size):
        batch = photo_links_list[i:i + batch_size]
        logger.info("[PAGE][PHOTOS] Resolving high-res batch %d-%d", i + 1, i + len(batch))

        for link in batch:
            try:
                driver.execute_script("window.open(arguments[0], '_blank');", link)
            except Exception as exc:
                logger.debug("[PAGE][PHOTOS] open tab failed for %s: %s", link, exc)

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
                logger.debug("[PAGE][PHOTOS] high-res parse failed: %s", exc)
            finally:
                try:
                    driver.close()
                except Exception:
                    pass

        try:
            driver.switch_to.window(main_window)
        except Exception:
            break
        time.sleep(0.5)

    return sorted(high_res_images)
