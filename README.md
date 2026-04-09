# Facebook Crawl Pipeline

Project này crawl Facebook bằng Selenium theo luồng task queue hoặc test trực tiếp theo UID/URL. Code hiện tại tập trung vào 4 phần chính:

- [main.py](/home/baoanh/Desktop/Workplace/selenium_crawl_fb/main.py): entrypoint dequeue task, gom item theo account cookie, suy luận module `profile` hoặc `page`, rồi phát việc cho worker.
- [scripts/crawler.py](/home/baoanh/Desktop/Workplace/selenium_crawl_fb/scripts/crawler.py): worker Selenium, login, nhận diện entity type, gọi scraper chi tiết và crawl post theo scroll loop.
- [src/fbprofile/browser/get_page_info.py](/home/baoanh/Desktop/Workplace/selenium_crawl_fb/src/fbprofile/browser/get_page_info.py): bóc tách dữ liệu chi tiết của Facebook Page.
- [src/fbprofile/browser/stable_scroll.py](/home/baoanh/Desktop/Workplace/selenium_crawl_fb/src/fbprofile/browser/stable_scroll.py): cơ chế scroll đến khi ổn định, dùng lại cho photos, followers và luồng crawl post.

## Luồng chạy hiện tại

1. `main.py` nhận task từ API dequeue hoặc từ `--test-uid`.
2. Mỗi item được gắn `selector_module` phù hợp (`profile` hoặc `page`).
3. `_crawl_from_uids()` tạo danh sách target, nạp config, user-agent, proxy, cookies, profile dir, port debug và chia batch theo số worker.
4. Mỗi worker trong `scripts/crawler.py` tạo Chrome đã login, mở từng URL rồi:
   - xác định đây là `profile` hay `page` nếu chưa biết trước,
   - gọi scraper chuyên biệt để lưu `profile_info.json` hoặc `page_info.json`,
   - crawl dữ liệu selector cơ bản,
   - chạy `go_to_date()` rồi `crawl_scroll_loop()` để thu post GraphQL vào `posts_all.ndjson`.
5. Kết quả cuối cùng được gộp lại thành JSON response và có thể post event về service ngoài.

## Thành phần chính

### `main.py`

`main.py` là entrypoint nên dùng nếu bạn chạy theo queue hoặc muốn test nhanh một UID.

Chức năng chính:

- Gọi dequeue API qua `scripts.dequeue_task.run_curl()`.
- Parse payload task và chuẩn hóa item crawl.
- Gom item theo account để gắn đúng cookie.
- Precheck UID trước khi mở Selenium nếu `UID_PREFLIGHT_ENABLED=1`.
- Chia việc cho nhiều worker qua `ThreadPoolExecutor`.
- Xuất JSON ra stdout, file `--out`, và bắn event completion nếu có `task_id`.

Ví dụ:

```bash
python main.py --api-key <API_KEY>
python main.py --test-uid cambongda --selector-module page --out outputs/result.json
```

Các flag đang dùng:

- `--api-key`: token cho dequeue API.
- `--max-workers`: override số worker.
- `--selector-module`: ép module `profile` hoặc `page`.
- `--out`: ghi kết quả tổng ra file JSON.
- `--events-url`: override endpoint nhận event.
- `--test-uid`: bỏ qua queue, crawl trực tiếp một UID/slug.

### `scripts/crawler.py`

File này chịu trách nhiệm chạy worker Selenium thực tế.

Các điểm quan trọng:

- `crawl_urls_batch()` là hàm worker chính.
- Worker lấy `debug_port` từ pool, dựng driver đã login bằng `create_logged_in_driver()`, rồi tuần tự xử lý từng URL trong batch.
- Nếu chưa biết loại entity, worker fallback bằng DOM:
  - có nút `Add Friend` hoặc `Thêm bạn bè` thì coi là `profile`,
  - ngược lại coi là `page`.
- Với `profile`, worker gọi `scrape_full_profile_info(...)`.
- Với `page`, worker gọi `scrape_full_page_info(...)`.
- Sau phần profile/page info, worker vẫn chạy `crawl_page(...)` để lấy thêm các field theo selector module.
- Cuối cùng worker dùng `crawl_scroll_loop()` để hút post và lưu NDJSON.

Output mỗi target hiện nằm dưới thư mục:

```text
database/profile/page/<page_name>/
```

Các file thường gặp:

- `profile_info.json` hoặc `page_info.json`
- `posts_all.ndjson`
- `checkpoint.json`
- `raw_dump_posts/`

## Scraper Page

[src/fbprofile/browser/get_page_info.py](/home/baoanh/Desktop/Workplace/selenium_crawl_fb/src/fbprofile/browser/get_page_info.py) là scraper chuyên biệt cho Facebook Page.

Hàm điều phối chính:

- `scrape_full_page_info(driver, target_url, output_path, scroll_until_stable_cfg)`

Schema trả về hiện tại gồm:

```json
{
  "url": "...",
  "scanned_at": "YYYY-MM-DD HH:MM:SS",
  "basic_info": {},
  "featured_news": [],
  "introduction": {},
  "photos": [],
  "friends": [],
  "followers_list": []
}
```

Các nhóm dữ liệu:

- `get_name_followers_following_avatar()`: tên page, avatar, cover, followers/following/friends count nếu có.
- `get_page_featured_news()`: quét highlights/story collection bằng cách mở từng collection theo batch tab.
- `get_page_introduces()`: vào tab `about` và gom dữ liệu theo từng nhóm thông tin.
- `get_page_high_res_pictures()`: vào `photos`, scroll đến khi ổn định rồi mở batch tab để lấy ảnh độ phân giải cao.
- `get_page_followers()`: vào `followers`, scroll đến khi ổn định rồi trích danh sách follower gồm tên, link, avatar, subtitle.

Điểm đáng lưu ý:

- Hàm `scrape_full_page_info()` luôn trả về `full_data`, kể cả khi có lỗi giữa chừng.
- Nếu truyền `output_path`, dữ liệu sẽ được ghi ra file JSON sau khi crawl.
- Hiện schema vẫn còn key `friends` dù page thực tế dùng `followers_list`; nếu muốn đồng nhất schema thì nên dọn tiếp.

## Stable Scroll

Cơ chế scroll mới nằm ở [src/fbprofile/browser/stable_scroll.py](/home/baoanh/Desktop/Workplace/selenium_crawl_fb/src/fbprofile/browser/stable_scroll.py).

Ý tưởng:

- Không dừng theo số vòng đứng yên hard-code như trước.
- Mỗi vòng sẽ theo dõi đồng thời:
  - `document.body.scrollHeight`
  - số item đã thu được qua callback `get_progress_count()`
- Nếu cả 2 cùng không tăng trong đủ `stable_rounds`, hàm xem trang đã ổn định và dừng.

Default hiện tại:

```python
{
    "max_scrolls": 50,
    "stable_rounds": 3,
    "scroll_pause_seconds": 1.5,
    "settle_pause_seconds": 0.5,
}
```

Trong [configs/base.json](/home/baoanh/Desktop/Workplace/selenium_crawl_fb/configs/base.json), crawler đang override thành:

```json
"scroll_until_stable": {
  "max_scrolls": 80,
  "stable_rounds": 4,
  "scroll_pause_seconds": 2.0,
  "settle_pause_seconds": 0.5
}
```

Cấu hình này được truyền từ `main.py` hoặc `scripts/crawler.py` xuống các hàm:

- `get_page_high_res_pictures()`
- `get_page_followers()`
- `get_profile_high_res_pictures()`
- `get_profile_friends()`
- `crawl_scroll_loop()`

Riêng `crawl_scroll_loop()` trong [src/fbprofile/browser/scroll.py](/home/baoanh/Desktop/Workplace/selenium_crawl_fb/src/fbprofile/browser/scroll.py) dùng cùng nguyên tắc ổn định nhưng áp vào số lượng post mới đã ghi vào `seen_ids`.

## Cấu hình tối thiểu

### Python và thư viện

- Python 3.10+
- Google Chrome
- Chromedriver tương thích

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### `.env`

Các biến quan trọng nhất:

```env
API_KEY=
COOKIES=c_user=...; xs=...;
LOGIN_METHOD=cookies
PROFILE_DIR=./chrome_profile

HEADLESS=false
MAX_WORKERS=3
USER_AGENTS_FILE=data/user_agents.txt
PROXIES_FILE=data/proxies.txt

UID_PREFLIGHT_ENABLED=1
UID_PREFLIGHT_TIMEOUT=6

# Optional service URLs
# SERVICE_ROOT_URL=https://...
# DEQUEUE_URL=https://.../tasks/dequeue?social_type=facebook&version=1.0
# EVENTS_URL=https://.../events
```

### `configs/base.json`

File này điều khiển:

- login mode
- số worker
- wait timing
- cấu hình `scroll_until_stable`
- selector module directory qua `configs/modules`

## Cách chạy

### 1. Test một target cụ thể

```bash
python main.py --test-uid cambongda --selector-module page
python main.py --test-uid zuck --selector-module profile
```

### 2. Chạy theo queue

```bash
python main.py --api-key <API_KEY>
```

### 3. Chạy crawler trực tiếp không qua queue

Nếu cần debug riêng worker/path crawl:

```bash
python scripts/crawler.py --selector-module page
```

Lưu ý: `scripts/crawler.py` đọc danh sách từ `pages.txt` theo cấu hình trong [configs/base.json](/home/baoanh/Desktop/Workplace/selenium_crawl_fb/configs/base.json), còn `main.py` ưu tiên task dequeue hoặc `--test-uid`.

## Ghi chú triển khai

- Selector module nằm trong thư mục [configs/modules](/home/baoanh/Desktop/Workplace/selenium_crawl_fb/configs/modules).
- `profile.json` hiện đầy đủ hơn `page.json`; phần page detail thực tế đang lấy chủ yếu từ scraper chuyên biệt trong `get_page_info.py`.
- Worker có hỗ trợ quay vòng `user-agent`, proxy, cookies và profile dir.
- Nếu queue trả về nhiều account khác nhau, `main.py` sẽ group task theo account cookie trước khi crawl.
- Post result được in ra stdout theo schema `{"count": ..., "items": [...]}`.

## File nên đọc trước khi sửa code

- [main.py](/home/baoanh/Desktop/Workplace/selenium_crawl_fb/main.py)
- [scripts/crawler.py](/home/baoanh/Desktop/Workplace/selenium_crawl_fb/scripts/crawler.py)
- [src/fbprofile/browser/get_page_info.py](/home/baoanh/Desktop/Workplace/selenium_crawl_fb/src/fbprofile/browser/get_page_info.py)
- [src/fbprofile/browser/get_profile_info.py](/home/baoanh/Desktop/Workplace/selenium_crawl_fb/src/fbprofile/browser/get_profile_info.py)
- [src/fbprofile/browser/stable_scroll.py](/home/baoanh/Desktop/Workplace/selenium_crawl_fb/src/fbprofile/browser/stable_scroll.py)
- [src/fbprofile/browser/scroll.py](/home/baoanh/Desktop/Workplace/selenium_crawl_fb/src/fbprofile/browser/scroll.py)
