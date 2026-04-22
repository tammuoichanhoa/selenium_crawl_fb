from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from logs.loging_config import logger
from src.fbprofile.browser.selector_posts import extract_best_selector_post

try:
    from .get_graphql_response import extract_media_page
except ImportError:
    from groups.get_graphql_response import extract_media_page  # type: ignore

from .group_media_common import extract_group_id_from_url


def build_group_info_document(
    *,
    group_url: str,
    first_page: dict[str, Any],
    scanned_at: str | None = None,
    photo_limit: int | None = None,
) -> dict[str, Any]:
    media_page = extract_media_page(first_page)
    group_id = media_page.get("group_id") or extract_group_id_from_url(group_url)

    reference_token = media_page.get("reference_token")
    if not reference_token and group_id:
        reference_token = f"g.{group_id}"

    return {
        "url": group_url,
        "scanned_at": scanned_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "basic_info": {
            "group_id": group_id,
            "reference_token": reference_token,
            "entity_type": "facebook_group",
            "name": media_page.get("group_name"),
            "members": None,
            "cover_photo": None,
        },
        "members": [],
        "posts": [],
    }


def _dedupe_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []

    for value in values:
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)

    return ordered


def extract_media_ids(response: dict[str, Any]) -> list[str]:
    page = extract_media_page(response)
    photo_ids = (photo.get("id") for photo in page.get("photos", []))
    return _dedupe_strings(photo_ids)


def extract_media_photos(response: dict[str, Any]) -> list[dict[str, Any]]:
    page = extract_media_page(response)
    photos = page.get("photos", [])
    if not isinstance(photos, list):
        return []
    return [dict(photo) for photo in photos if isinstance(photo, dict)]


def build_media_post_urls(media_id: str) -> list[str]:
    media_id = (media_id or "").strip()
    if not media_id:
        return []

    return [
        # f"https://www.facebook.com/photo?fbid={media_id}&set=pcb.{media_id}",
        # f"https://www.facebook.com/photo?fbid={media_id}&set=a.{media_id}",
        f"https://www.facebook.com/{media_id}",

    ]


def extract_post_from_media_url(
    driver,
    *,
    group_url: str,
    media_id: str,
    media_url: str,
    wait_seconds: float = 2.0,
) -> dict[str, Any] | None:
    media_url = (media_url or "").strip()
    if not media_url:
        return None

    try:
        driver.get(media_url)
    except Exception as exc:
        logger.warning("[GROUP_MEDIA] Failed to open media url %s: %s", media_url, exc)
        return None

    if wait_seconds > 0:
        try:
            wait = WebDriverWait(driver, wait_seconds)
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='main']")))
            wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "img[data-visualcompletion='media-vc-image']"))
            )
        except TimeoutException as exc:
            logger.warning("[GROUP_MEDIA] Timeout waiting media DOM at %s: %s", media_url, exc)
            return None

    # Trang facebook.com/photo?fbid=... là photo viewer page (HTML phức tạp).
    # Trong DOM của nó có thể xuất hiện nhiều “post-container”/article do Facebook render thêm các khối kiểu feed
    # (post chính, phần liên quan, gợi ý, wrapper UI, virtualized containers…).
    # Nên cần chọn "best post" theo preferred_photo_id để tránh bắt nhầm.
    # selector_source_url = (getattr(driver, "current_url", "") or media_url).strip()
    post = extract_best_selector_post(
        driver,
        group_url,
        preferred_photo_id=media_id,
        require_preferred_match=False,
    )

    if not post:
        return None

    post["media_id"] = media_id
    post["media_url"] = media_url
    if not (post.get("source_id") or "").strip():
        post["source_id"] = extract_group_id_from_url(group_url)
    return post


def _post_dedupe_key(post: dict[str, Any], media_id: str | None = None) -> str:
    dedupe_key = post.get("id") or post.get("rid") or post.get("link") or media_id or ""
    if not isinstance(dedupe_key, str):
        dedupe_key = str(dedupe_key)
    return dedupe_key.strip()


def _merge_post_photo(post: dict[str, Any], photo: dict[str, Any], media_url: str) -> None:
    photos = post.setdefault("photos", [])
    if not isinstance(photos, list):
        photos = []
        post["photos"] = photos

    photo_id = (photo.get("id") or "").strip() if isinstance(photo.get("id"), str) else ""
    for existing in photos:
        if not isinstance(existing, dict):
            continue
        existing_id = existing.get("id")
        if isinstance(existing_id, str) and existing_id.strip() == photo_id:
            return

    # photo_payload = dict(photo)
        # chỉ lấy các field cần thiết
    photo_payload = {
        "id": photo.get("id"),
        "uri": photo.get("uri"),
        "caption": photo.get("caption"),
        "width": photo.get("width"),
        "height": photo.get("height"),
        "media_url": media_url
    }
    photo_payload["media_url"] = media_url
    photos.append(photo_payload)

    # if photo_id and not isinstance(post.get("primary_photo_id"), str):
    #     post["primary_photo_id"] = photo_id


def extract_post_from_media_id(
    driver,
    *,
    group_url: str,
    media_id: str,
    wait_seconds: float = 2.0,
) -> dict[str, Any] | None:
    """
    Thử trích xuất (crawl/parse) bài viết Facebook tương ứng với `media_id`.

    Cách làm:
    - `build_media_post_urls(media_id)` có thể trả về nhiều URL “ứng viên” cho cùng 1 media_id
      (do Facebook có nhiều dạng URL/route khác nhau).
    - Hàm sẽ thử lần lượt từng URL bằng `extract_post_from_media_url(...)`.
    - Nếu tìm được post mà `post["id"]` khớp chính xác với `media_id` -> trả về ngay (kết quả chắc chắn nhất).
    - Nếu không có cái nào khớp hoàn toàn, vẫn giữ lại post hợp lệ đầu tiên làm `fallback_post`
      (phòng trường hợp parser không lấy ra đúng id nhưng nội dung vẫn là bài cần).
    - Nếu tất cả URL đều thất bại -> trả về None.
    """
    fallback_post: dict[str, Any] | None = None

    # Thử nhiều biến thể URL cho cùng một media_id.
    for media_url in build_media_post_urls(media_id):
        post = extract_post_from_media_url(
            driver,
            group_url=group_url,
            media_id=media_id,
            media_url=media_url,
            wait_seconds=wait_seconds,
        )
        if not post:
            continue

        # Ưu tiên kết quả mà id trích xuất khớp media_id (đúng “target” nhất).
        if (post.get("id") or "").strip() == media_id:
            return post

        # Nếu chưa có fallback, giữ lại kết quả hợp lệ đầu tiên để trả về nếu không tìm được match exact.
        if fallback_post is None:
            fallback_post = post

    # Không có match exact -> trả về fallback (nếu có), còn không -> None.
    return fallback_post


def append_posts_from_media_ids(
    driver,
    *,
    result: dict[str, Any],
    group_url: str,
    media_photos: Iterable[dict[str, Any]],
    wait_seconds: float = 2.0,
) -> dict[str, Any]:
    """
    Ghép (append/merge) các bài post lấy từ `media_id` vào `result["posts"]`.

    Ý tưởng chính:
    - `media_photos` thường là danh sách ảnh (mỗi ảnh có `id` = media_id).
    - Với mỗi `media_id`, hàm sẽ:
      1) Tạo URL bài viết từ media_id (để lưu tham chiếu nếu cần).
      2) Gọi `extract_post_from_media_id(...)` để crawl/parse ra thông tin post.
      3) Tạo `dedupe_key` (khóa chống trùng) để biết post này đã có trong result chưa.
      4) Nếu chưa có: thêm post mới vào `posts`, đồng thời gắn ảnh (photo) vào post.
      5) Nếu đã có: chỉ merge thêm ảnh (photo) vào post hiện có.

    Lưu ý:
    - Dedupe diễn ra theo `_post_dedupe_key(...)` để nhiều media_id/ảnh cùng trỏ về một post
      không bị thêm trùng.
    """
    # Đảm bảo result có key "posts" là một list để append vào.
    posts = result.setdefault("posts", [])

    # Map: dedupe_key -> dict post hiện có trong result
    # (giúp lookup O(1) thay vì phải duyệt lại list mỗi lần).
    post_index_by_key: dict[str, dict[str, Any]] = {}

    # Index các post đã có sẵn trong result để dedupe về sau.
    for current in posts:
        if not isinstance(current, dict):
            continue


        # Tạo khóa dedupe cho post hiện tại.
        dedupe_key = _post_dedupe_key(current)
        if dedupe_key:
            post_index_by_key[dedupe_key] = current

    # Chống xử lý trùng media_id trong media_photos.
    seen_media_ids: set[str] = set()

    # Duyệt từng ảnh trong media_photos để lấy media_id và kéo post tương ứng.
    for photo in media_photos:
        if not isinstance(photo, dict):
            continue

        media_id = photo.get("id")
        if not isinstance(media_id, str):
            continue

        media_id = media_id.strip()
        # Bỏ qua media_id rỗng hoặc đã xử lý rồi.
        if not media_id or media_id in seen_media_ids:
            continue
        seen_media_ids.add(media_id)

        # Tạo URL bài viết từ media_id (nếu build được).
        media_urls = build_media_post_urls(media_id)
        default_media_url = media_urls[0] if media_urls else ""

        # Crawl/parse ra post từ media_id.
        post = extract_post_from_media_id(
            driver,
            group_url=group_url,
            media_id=media_id,
            wait_seconds=wait_seconds,
        )
        if not post:
            continue
        
        media_url = (post.get("media_url") or default_media_url or "").strip()
        
        # Tạo khóa dedupe cho post vừa crawl được.
        # (thường dùng nội dung định danh của post; có thể fallback thêm media_id).
        dedupe_key = _post_dedupe_key(post, media_id=media_id)
        if not dedupe_key:
            continue

        # Nếu post này chưa tồn tại trong result: tạo bản sao, chuẩn hoá, gắn photo rồi append.
        existing_post = post_index_by_key.get(dedupe_key)
        if existing_post is None:
            post.setdefault("photos", [])              # đảm bảo có mảng photos để merge
            _merge_post_photo(post, photo, media_url)  # thêm ảnh hiện tại vào post
            posts.append(post)                         # đưa vào kết quả
            post_index_by_key[dedupe_key] = post       # cập nhật index
            continue


        _merge_post_photo(existing_post, photo, media_url)

    return result



def crawl_group_media_posts(
    driver,
    *,
    group_url: str,
    first_page: dict[str, Any],
    next_pages: Optional[Iterable[dict[str, Any]]] = None,
    scanned_at: str | None = None,
    photo_limit: int | None = None,
    wait_seconds: float = 2.0,
) -> dict[str, Any]:
    
    result = build_group_info_document(
        group_url=group_url,
        first_page=first_page,
        scanned_at=scanned_at,
        photo_limit=photo_limit,
    )
    # Lấy danh sách các media (photo, ...) từ các pages
    all_media_photos = extract_media_photos(first_page)
    for page in next_pages or []:
        all_media_photos.extend(extract_media_photos(page))

    # Loại bỏ post bị trùng lặp do xuất hiện ở nhiều media (TH 1 bài viết có nhiều media)
    deduped_media_photos: list[dict[str, Any]] = []
    seen_photo_ids: set[str] = set()
    for photo in all_media_photos:
        if not isinstance(photo, dict):
            continue
        photo_id = photo.get("id")
        if not isinstance(photo_id, str):
            continue
        photo_id = photo_id.strip()
        if not photo_id or photo_id in seen_photo_ids:
            continue
        seen_photo_ids.add(photo_id)
        deduped_media_photos.append(photo)

    if isinstance(photo_limit, int) and photo_limit >= 0:
        deduped_media_photos = deduped_media_photos[:photo_limit]

    append_posts_from_media_ids(
        driver,
        result=result,
        group_url=group_url,
        media_photos=deduped_media_photos,
        wait_seconds=wait_seconds,
    )
    return result
