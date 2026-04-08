import time
import json
from pathlib import Path
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

# Import logger từ hệ thống log hiện tại
from logs.loging_config import logger

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
    """Lấy thông tin Giới thiệu (About) cho Fanpage."""
    current_url = driver.current_url
    target_about = f"{target_url}/about" if "profile.php" not in target_url else f"{target_url}&sk=about"
    
    if target_about not in current_url:
        driver.get(target_about)
        time.sleep(3)
    
    data = {}
    wait = WebDriverWait(driver, timeout)

    tabs_mapping = {
        "contact_basic": ["Thông tin liên hệ và cơ bản", "Contact and basic info", "Thông tin liên hệ", "Contact info"],
        "privacy_legal": ["Quyền riêng tư và thông tin pháp lý", "Privacy and legal info", "Quyền riêng tư"],
        "profile_transparency": ["Tính minh bạch của Trang", "Tính minh bạch", "Page transparency"],
        "work_education": ["Công việc và học vấn", "Work and education"],
        "places": ["Nơi từng sống", "Places Lived", "Địa điểm"],
        "family": ["Gia đình và các mối quan hệ", "Family and relationships"],
        "life_events": ["Sự kiện trong đời", "Life events", "Life updates"]
    }

    logger.info("[PAGE] Đang quét thông tin Giới thiệu Fanpage...")

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
                pass # Không có tab này thì tiếp tục quét tab hiện tại (có thể đang ở default)

            # Trên fanpage, các mục info thường đi kèm với thẻ img làm icon (class x1b0d499)
            # Nội dung nằm ở thẻ div kế tiếp
            row_xpath = "//img[contains(@class, 'x1b0d499') or @height='24']/parent::div/following-sibling::div"
            rows = driver.find_elements(By.XPATH, row_xpath)
            
            for row in rows:
                text_content = row.text.strip()
                if text_content and "Không có" not in text_content and "để hiển thị" not in text_content:
                    clean_text = text_content.replace("\n", " - ")
                    if clean_text not in data[key]:
                        data[key].append(clean_text)

        except Exception as e:
            logger.debug(f"[PAGE] Lỗi tại tab {key}: {e}")
            continue

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
# 5. FOLLOWERS / FOLLOWING / MUTUAL (Scroll vô hạn)
# ==========================================

def _extract_people_from_list_page(driver) -> list:
    """
    Hàm nội bộ: Trích xuất danh sách người từ trang list (followers/following/mutual).
    Tìm các thẻ chứa avatar + tên + link.
    """
    people_list = []
    seen_urls = set()

    # Selector chính: mỗi row người dùng thường là div có aria-label chứa link
    # Fallback: tìm tất cả link dạng /người-dùng trong danh sách
    try:
        # Tìm container của từng user: div chứa thẻ a có href profile
        rows = driver.find_elements(
            By.XPATH,
            "//div[contains(@class, 'x1iyjqo2') and contains(@class, 'xv54qhq')]"
        )

        if not rows:
            # Fallback: thử tìm các link profile trong danh sách
            rows = driver.find_elements(
                By.XPATH,
                "//div[@role='listitem'] | //li[.//a[contains(@href,'facebook.com')]]"
            )

        for row in rows:
            try:
                person = {"name": None, "page_url": None, "avatar_url": None, "subtitle": ""}

                # Tên & Link
                try:
                    link_el = row.find_element(By.XPATH, ".//a[@role='link']")
                    person["name"] = link_el.text.strip()
                    person["page_url"] = link_el.get_attribute("href")
                except:
                    try:
                        link_el = row.find_element(By.XPATH, ".//a[contains(@href,'facebook.com')]")
                        person["name"] = link_el.text.strip()
                        person["page_url"] = link_el.get_attribute("href")
                    except:
                        continue

                if not person["name"] or not person["page_url"]:
                    continue
                if person["page_url"] in seen_urls:
                    continue
                seen_urls.add(person["page_url"])

                # Subtitle ("Có X bạn chung", "Đang theo dõi bạn", v.v.)
                try:
                    sub_el = row.find_element(
                        By.XPATH,
                        ".//div[contains(@class,'x1gslohp')] | .//span[contains(@class,'x1gslohp')]"
                    )
                    person["subtitle"] = sub_el.text.strip()
                except:
                    pass

                # Avatar
                try:
                    avt_el = row.find_element(By.XPATH, "./preceding-sibling::div//img")
                    person["avatar_url"] = avt_el.get_attribute("src")
                except:
                    try:
                        avt_el = row.find_element(By.XPATH, ".//img")
                        person["avatar_url"] = avt_el.get_attribute("src")
                    except:
                        pass

                people_list.append(person)
            except:
                continue

    except Exception as e:
        logger.debug(f"[PAGE] _extract_people_from_list_page error: {e}")

    return people_list


def _scroll_and_collect_all(driver, list_url: str, label: str,
                             max_scroll: int = 0,
                             scroll_pause: float = 2.5) -> list:
    """
    Hàm nội bộ: Điều hướng tới list_url, scroll vô hạn (hoặc đến max_scroll)
    rồi trả về toàn bộ danh sách người đã thu thập.

    :param max_scroll: Số lần scroll tối đa (0 = không giới hạn – scroll đến khi hết).
    :param scroll_pause: Giây nghỉ giữa mỗi lần scroll.
    """
    logger.info(f"[PAGE] Đang truy cập: {list_url}")
    driver.get(list_url)
    time.sleep(3)

    collected = []
    seen_urls = set()
    stall_count = 0
    scroll_count = 0

    last_height = driver.execute_script("return document.body.scrollHeight")

    while True:
        # --- Thu thập người từ DOM hiện tại ---
        batch = _extract_people_from_list_page(driver)
        new_added = 0
        for person in batch:
            if person["page_url"] and person["page_url"] not in seen_urls:
                seen_urls.add(person["page_url"])
                collected.append(person)
                new_added += 1

        logger.info(f"[PAGE] [{label}] Scroll #{scroll_count} → +{new_added} mới, tổng: {len(collected)}")

        # --- Scroll xuống cuối ---
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(scroll_pause)
        scroll_count += 1

        new_height = driver.execute_script("return document.body.scrollHeight")

        if new_height == last_height:
            stall_count += 1
            if stall_count >= 3:  # 3 lần liên tiếp không tải thêm → dừng
                logger.info(f"[PAGE] [{label}] Đã cuộn đến cuối danh sách ({len(collected)} người).")
                break
        else:
            stall_count = 0
            last_height = new_height

        # Giới hạn tuỳ chọn
        if max_scroll and scroll_count >= max_scroll:
            logger.info(f"[PAGE] [{label}] Đã đạt giới hạn scroll ({max_scroll}). Dừng.")
            break

    # Thu thập lần cuối sau scroll
    final_batch = _extract_people_from_list_page(driver)
    for person in final_batch:
        if person["page_url"] and person["page_url"] not in seen_urls:
            seen_urls.add(person["page_url"])
            collected.append(person)

    return collected


def get_all_followers(driver, target_url: str,
                      max_scroll: int = 0,
                      scroll_pause: float = 2.5) -> list:
    """
    Lấy TẤT CẢ người Followers của page/profile.
    URL tương ứng: {target_url}/followers

    :param max_scroll: Giới hạn số lần scroll (0 = không giới hạn).
    :param scroll_pause: Giây nghỉ giữa các lần scroll.
    :return: Danh sách dict {name, page_url, avatar_url, subtitle}.
    """
    list_url = (
        f"{target_url}/followers"
        if "profile.php" not in target_url
        else f"{target_url}&sk=followers"
    )
    try:
        result = _scroll_and_collect_all(driver, list_url, "FOLLOWERS", max_scroll, scroll_pause)
        logger.info(f"[PAGE] ✅ get_all_followers: {len(result)} người")
        return result
    except Exception as e:
        logger.error(f"[PAGE] Lỗi get_all_followers: {e}")
        return []


def get_all_following(driver, target_url: str,
                      max_scroll: int = 0,
                      scroll_pause: float = 2.5) -> list:
    """
    Lấy TẤT CẢ người mà page/profile đang Following.
    URL tương ứng: {target_url}/following

    :param max_scroll: Giới hạn số lần scroll (0 = không giới hạn).
    :param scroll_pause: Giây nghỉ giữa các lần scroll.
    :return: Danh sách dict {name, page_url, avatar_url, subtitle}.
    """
    list_url = (
        f"{target_url}/following"
        if "profile.php" not in target_url
        else f"{target_url}&sk=following"
    )
    try:
        result = _scroll_and_collect_all(driver, list_url, "FOLLOWING", max_scroll, scroll_pause)
        logger.info(f"[PAGE] ✅ get_all_following: {len(result)} người")
        return result
    except Exception as e:
        logger.error(f"[PAGE] Lỗi get_all_following: {e}")
        return []


def get_all_followers_mutual(driver, target_url: str,
                             max_scroll: int = 0,
                             scroll_pause: float = 2.5) -> list:
    """
    Lấy TẤT CẢ người Followers Mutual (người theo dõi cùng – bạn chung).
    URL tương ứng: {target_url}/followers_mutual

    :param max_scroll: Giới hạn số lần scroll (0 = không giới hạn).
    :param scroll_pause: Giây nghỉ giữa các lần scroll.
    :return: Danh sách dict {name, page_url, avatar_url, subtitle}.
    """
    list_url = (
        f"{target_url}/followers_mutual"
        if "profile.php" not in target_url
        else f"{target_url}&sk=followers_mutual"
    )
    try:
        result = _scroll_and_collect_all(driver, list_url, "FOLLOWERS_MUTUAL", max_scroll, scroll_pause)
        logger.info(f"[PAGE] ✅ get_all_followers_mutual: {len(result)} người")
        return result
    except Exception as e:
        logger.error(f"[PAGE] Lỗi get_all_followers_mutual: {e}")
        return []


# ==========================================
# 6. PAGE INTRO INFO (Thông tin cơ bản từ section Intro)
# ==========================================
def get_page_intro_info(driver, target_url: str, timeout: int = 8) -> dict:
    """
    Lấy thông tin cơ bản từ section Intro trên sidebar trang chủ Page.
    Dựa trên phân tích HTML thực tế của thoibao.de, các trường gồm:
    - description  : Mô tả ngắn của Page
    - category     : Loại trang (vd: "Page · Community")
    - phone        : Số điện thoại
    - email        : Email liên hệ
    - website      : Website URL
    - hours        : Giờ mở cửa (vd: "Always open")
    - rating       : Đánh giá (vd: "28% recommend (497 reviews)")
    - location     : Địa chỉ / Thành phố (nếu có)
    - founded      : Năm thành lập (nếu có)
    - impression_count: Số người đã đánh giá Trang (nếu có)
    """
    data = {
        "description": None,
        "category": None,
        "phone": None,
        "email": None,
        "website": None,
        "hours": None,
        "rating": None,
        "location": None,
        "founded": None,
    }

    if target_url not in driver.current_url:
        driver.get(target_url)
        time.sleep(3)

    wait = WebDriverWait(driver, timeout)
    logger.info("[PAGE] Đang lấy thông tin Intro...")

    try:
        # 1. Description: span class x2b8uid trong div.x2b8uid
        try:
            desc_el = driver.find_element(
                By.XPATH,
                "//div[contains(@class,'x2b8uid')]//span[contains(@dir,'auto')]"
            )
            data["description"] = desc_el.text.strip()
        except:
            pass

        # 2. Category: thẻ <strong> chứa "Page" kết hợp với text kế bên
        # Dựa trên HTML: <strong>Page</strong> · Community
        try:
            cat_el = driver.find_element(
                By.XPATH,
                "//strong[contains(.,'Page')]/.."
            )
            data["category"] = cat_el.text.strip().replace("\n", " ")
        except:
            try:
                # Fallback: tìm span chứa category kế icon x1b0d499
                cat_els = driver.find_elements(
                    By.XPATH,
                    "//img[contains(@class,'x1b0d499')]/../../..//span[contains(@dir,'auto')]"
                )
                for el in cat_els:
                    txt = el.text.strip()
                    if txt and "Page" in txt:
                        data["category"] = txt
                        break
            except:
                pass

        # 3. Lấy TẤT CẢ các hàng thông tin (mỗi hàng có icon + nội dung)
        # Structure: div chứa img.x1b0d499 (icon) + div kế tiếp (nội dung)
        info_rows = driver.find_elements(
            By.XPATH,
            "//img[contains(@class,'x1b0d499') and (@height='20' or @height='24')]"
            "/ancestor::div[contains(@class,'x1nhvcw1') or contains(@class,'x1qjc9v5')]"
        )

        for row in info_rows:
            try:
                row_text = row.text.strip()
                if not row_text:
                    continue

                # Phone: bắt đầu bằng +
                if row_text.startswith("+") and not data["phone"]:
                    data["phone"] = row_text

                # Email: có @
                elif "@" in row_text and not data["email"]:
                    data["email"] = row_text

                # Website: có http hoặc www, không phải facebook
                elif ("http" in row_text or row_text.startswith("www")) \
                        and "facebook" not in row_text and not data["website"]:
                    # Ưu tiên lấy href
                    try:
                        link_el = row.find_element(By.XPATH, ".//a[@href]")  
                        data["website"] = link_el.get_attribute("href")
                    except:
                        data["website"] = row_text

                # Giờ mở cửa: có từ khoá open/closed/giờ
                elif any(k in row_text.lower() for k in [
                    "open", "closed", "giờ", "mở", "đóng", "always"
                ]) and not data["hours"]:
                    data["hours"] = row_text

                # Rating: có % hoặc reviews
                elif ("%" in row_text or "review" in row_text.lower()) and not data["rating"]:
                    data["rating"] = row_text

            except:
                continue

        # 4. Location (địa chỉ): tìm span có icon địa điểm hoặc text có từ khoá
        try:
            loc_els = driver.find_elements(
                By.XPATH,
                "//span[contains(text(),'Germany') or contains(text(),'Đức') "
                "or contains(text(),'Berlin') or contains(text(),'city') "
                "or contains(text(),'thành phố')]"
            )
            if loc_els:
                data["location"] = loc_els[0].text.strip()
        except:
            pass

        # 5. Founded / Năm thành lập
        try:
            founded_els = driver.find_elements(
                By.XPATH,
                "//span[contains(text(),'Founded') or contains(text(),'Thành lập') "
                "or contains(text(),'Created')]/following-sibling::span"
            )
            if founded_els:
                data["founded"] = founded_els[0].text.strip()
            else:
                # Thử tìm text chứa năm 4 chữ số đứng riêng
                year_els = driver.find_elements(
                    By.XPATH,
                    "//span[contains(@dir,'auto') and string-length(normalize-space())=4 "
                    "and translate(normalize-space(), '0123456789', '')='']"
                )
                if year_els:
                    data["founded"] = year_els[0].text.strip()
        except:
            pass

    except Exception as e:
        logger.error(f"[PAGE] Lỗi get_page_intro_info: {e}")

    logger.info(f"[PAGE] ✅ Intro info: {data}")
    return data

# ==========================================
# MAIN ORCHESTRATOR
# ==========================================
def scrape_full_page_info(driver, target_url: str, output_path: Path = None,
                          crawl_followers: bool = True,
                          crawl_following: bool = True,
                          crawl_mutual: bool = True,
                          max_scroll_people: int = 0) -> dict:
    """
    Hàm chính điều phối việc lấy TOÀN BỘ thông tin PAGE và trả về dict,
    lưu file nếu output_path được cung cấp.

    :param crawl_followers:  Có lấy danh sách followers không (default: True)
    :param crawl_following:  Có lấy danh sách following không (default: True)
    :param crawl_mutual:     Có lấy followers_mutual không (default: True)
    :param max_scroll_people: Giới hạn scroll khi lấy people lists (0 = không giới hạn)
    """
    logger.info(f"--- BẮT ĐẦU QUÉT INFO PAGE (FULL): {target_url} ---")
    
    full_data = {
        "url": target_url,
        "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "basic_info": {},
        "intro_info": {},
        "featured_news": [],
        "introduction": {},
        "photos": [],
        "followers_list": [],
        "following_list": [],
        "followers_mutual_list": [],
    }

    try:
        # 1. Basic Info (Trang chủ)
        if target_url not in driver.current_url:
            driver.get(target_url)
            time.sleep(3)
        full_data["basic_info"] = get_name_followers_following_avatar(driver)
        logger.info("[PAGE] ✅ Xong Basic Info")

        # 1b. Intro Info (Section Intro trên sidebar)
        full_data["intro_info"] = get_page_intro_info(driver, target_url)
        logger.info("[PAGE] ✅ Xong Intro Info")

        # 2. Featured News (Highlights)
        full_data["featured_news"] = get_page_featured_news(driver, target_url)
        logger.info(f"[PAGE] ✅ Xong Highlights ({len(full_data['featured_news'])} bộ)")

        # 3. Introduction (About)
        full_data["introduction"] = get_page_introduces(driver, target_url)
        logger.info("[PAGE] ✅ Xong Introduction")

        # 4. Photos
        full_data["photos"] = get_page_high_res_pictures(driver, target_url)
        logger.info(f"[PAGE] ✅ Xong Photos ({len(full_data['photos'])} ảnh)")

        # 5. Followers (scroll đến hết)
        if crawl_followers:
            full_data["followers_list"] = get_all_followers(
                driver, target_url, max_scroll=max_scroll_people
            )
            logger.info(f"[PAGE] ✅ Xong Followers ({len(full_data['followers_list'])} người)")

        # 6. Following (scroll đến hết)
        if crawl_following:
            full_data["following_list"] = get_all_following(
                driver, target_url, max_scroll=max_scroll_people
            )
            logger.info(f"[PAGE] ✅ Xong Following ({len(full_data['following_list'])} người)")

        # 7. Followers Mutual (scroll đến hết)
        if crawl_mutual:
            full_data["followers_mutual_list"] = get_all_followers_mutual(
                driver, target_url, max_scroll=max_scroll_people
            )
            logger.info(f"[PAGE] ✅ Xong Followers Mutual ({len(full_data['followers_mutual_list'])} người)")

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
def get_page_high_res_pictures(driver, target_url, timeout=5, max_photos=None, batch_size=10):
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