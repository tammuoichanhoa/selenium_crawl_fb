# Facebook Selenium Crawler

Dự án này là công cụ cào dữ liệu Facebook bằng **Selenium**. Hiện có 2 luồng chạy:
1. **Crawler đa luồng** dùng `configs/config.json` + selector modules (hỗ trợ tải selector từ endpoint).
2. **Extraction đơn luồng** dùng `configs/config.yml` với danh sách field (YAML).

Tool hỗ trợ xoay User-Agent, proxy, login bằng cookie hoặc Chrome profile.

## 📌 Luồng Hoạt Động (Workflow)

Hệ thống có 2 nhánh chính:

1. **Khởi tạo và Đăng nhập (`run_login.py`)**: 
   - Script này dùng để đăng nhập Facebook thông qua Local Chrome Driver.
   - Bạn có thể điền Cookie vào `.env` để tool tự động tiêm cookie, hoặc chọn đăng nhập bằng profile hiện có.
   - Khi đăng nhập thành công bằng Cookies, hệ thống sẽ backup thư mục profile để tái dùng cho các worker sau này.
   
2. **Cào Dữ Liệu đa luồng (`run_crawler.py`)**:
   - Trình phân phối sẽ nạp danh sách URL từ file `data/pages.txt`.
   - Các URL được chia nhỏ thành các batch dựa trên số `MAX_WORKERS` (tối đa luồng song song).
   - Mỗi Worker sẽ được cấp một cổng Debug (Debugger Port), sử dụng một bộ User-Agent, Proxy và Profile được chỉ định riêng biệt.
   - Trình duyệt sẽ được tự động mở (headless hoặc không ảo), truy cập trang, đợi load xong và tuân theo các **Selector** (XPath/CSS) trong file `configs/config.json` để nhặt dữ liệu (như tên, số follower, số theo dõi, bio, email, ...).
   - Kết quả xuất ra file `.json`, mặc định: `outputs/crawl_results.json`.


---

## 🛠 Yêu Cầu Hệ Thống

* **Hệ điều hành:** Linux, macOS, Windows.
* **Ngôn ngữ:** Python 3.10 trở lên.
* **Trình duyệt:** Cần cài đặt sẵn [Google Chrome](https://www.google.com/chrome/).
* **Driver:** `chromedriver` tương thích cực khít với phiên bản Google Chrome hiện có trên máy (Script sẽ linh hoạt sử dụng cơ chế nội bộ nếu cần).

---

## 🚀 Hướng Dẫn Cài Đặt Khởi Tạo

1. **Clone mã nguồn (nếu có):**
    ```bash
    git clone <repository_url>
    cd facebook_crawler
    ```

2. **Cài đặt thư viện Python:**
    Nên sử dụng môi trường ảo hóa (`venv` hoặc `conda`).
    ```bash
    python -m venv venv
    source venv/bin/activate  # Trên Linux/macOS
    # Trên Windows: venv\\Scripts\\activate
    pip install -r requirements.txt
    ```

3. **Tạo các thư mục bắt buộc (nếu chưa có):**
    ```bash
    mkdir -p data configs outputs chrome_profile chrome_profiles
    ```

---

## ⚙️ Hướng Dẫn Cấu Hình (Configurations)

Phần cấu hình bao gồm `.env`, `configs/config.json` (crawler đa luồng) và `configs/config.yml` (extraction đơn luồng).

### 1. File `.env`
Tạo một file `.env` ở thư mục gốc của dự án. File này chứa các thiết lập môi trường hệ thống mạng và xác thực:

```env
# Xác thực (Dùng cho quá trình login)
COOKIES=c_user=...;xs=...;fr=...;
LOGIN_METHOD=cookies             # Chọn 'cookies' hoặc 'profile'
PROFILE_DIR=./chrome_profile     # Thư mục lưu dữ liệu session/profile

# Worker (Luồng chạy song song)
MAX_WORKERS=3
PORT_RANGE_MIN=8000
PORT_RANGE_MAX=9999
PORT_POOL_SIZE=100

# Giả lập thiết bị (Fingerprint)
HEADLESS=true                    # Chạy ngầm (true) hoặc hiện UI (false)
USER_AGENTS_FILE=data/user_agents.txt
PROXIES_FILE=data/proxies.txt
# PROXY=http://user:pass@127.0.0.1:3128

# Chrome binary (tuỳ chọn)
# CHROME_BINARY=/path/to/chrome
# CHROME_BINARY_WIN_PATH=C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe

# Override URL (tuỳ chọn)
# FB_HOME_URL=https://www.facebook.com/
# FB_LOCALE_URL=https://www.facebook.com/?locale=vi_VN

# Bật gỡ lỗi Selector (Selector Debug)
SELECTOR_DEBUG=1
SELECTOR_LOG_CONFIG=1
SELECTOR_CAPTURE=1
SELECTOR_CAPTURE_DIR=./debug_artifacts

# Selector remote (tuỳ chọn)
# SELECTOR_ENDPOINT=https://<your-endpoint>/configs/auto-node
# SELECTOR_CACHE_DIR=./selector_cache
# SELECTOR_SITE=facebook
# SELECTOR_ENV=prod
# SELECTOR_MODULE=page
# SELECTOR_PAGE=about
# SELECTOR_AUTO_DOWNLOAD=1
```

### 2. Dữ Liệu Đầu Vào (`data/`)
- **`data/pages.txt`**: Khai báo danh sách URL sẽ đi cào (Mỗi URL 1 dòng). Dùng ký tự `#` để comment dòng.
- **`data/user_agents.txt`**: Khai báo danh sách user agents (mỗi user agent 1 dòng).
- **`data/proxies.txt`**: (Tuỳ chọn) danh sách proxy.
- **`data/cookies.txt`**: (Tuỳ chọn cho `run_extraction.py`) file JSON cookies export từ browser/extension.

### 3. Khai Báo Selectors cho crawler đa luồng (`configs/config.json`)
File này chịu trách nhiệm trỏ đúng cấu trúc HTML của FB để cào dữ liệu. Thiết kế hỗ trợ *modules*.

```json
{
  "login": {
    "method": "cookies",
    "headless": false,
    "profile_dir": "./chrome_profile"
  },
  "crawl": {
    "pages_file": "data/pages.txt",
    "output_file": "outputs/crawl_results.json",
    "max_workers": 3,
    "wait_after_load": 3,
    "element_timeout": 15
  },
  "selectors": {
    "modules": {
      "profile": {
        "site": "facebook",
        "module": "profile",
        "elements": {
          "profile.name": {
            "primary": { "type": "tagName", "value": "h1", "priority": 6 },
            "fallbacks": [
              { "type": "xpath", "value": "//h1", "priority": 5 }
            ],
            "attribute": "text"
          }
        }
      }
    }
  }
}
```
*Lưu ý: Bạn có thể chọn target của module nào được chạy trong `run_crawler.py` (ví dụ chạy module Profile thay vì Page).*

### 4. Khai Báo Fields cho extraction đơn luồng (`configs/config.yml`)
File này định nghĩa danh sách fields cần trích xuất (YAML).

```yaml
login:
  profile_path: chrome_profiles/facebook
  profile_name: Default
  cookies_file: data/cookies.txt
  user_agents_file: data/user_agents.txt
  proxies_file: data/proxies.txt

crawl:
  pages_file: data/pages.txt
  output_file: outputs/pages_data.json
  page_wait_seconds: 4
  field_timeout: 25

fields:
  - name: page_name
    by: xpath
    selector: "(//h1[contains(@class,'html-h1')])[1]"
    attribute: text
    wait_until: visible
```

---

## 🏃 Chạy Ứng Dụng

### Test thử Login khởi tạo session (Chỉ Cần Làm 1 Lần)
Đầu tiên, chạy tập lệnh Login để nhúng cookie / khởi tạo thư mục profile gốc.

```bash
python run_login.py
```
> **Giải thích**: Script sẽ đọc `.env` (lấy `COOKIES` và `PROFILE_DIR`), mở trình duyệt thật lên. Nếu phương thức `login_method=cookies`, script tiêm cookie thẳng vào trang và tải lại để xác nhận. Cuối cùng, nó sẽ *Back up* folder profile `chrome_profile` để tái dùng khi Crawler chạy đa luồng. Hãy ấn `Enter` trên Terminal để tắt khi đã thấy login mượt.

### Test thử Chạy Crawler đa luồng
Hãy đảm bảo file `data/pages.txt` đã có chứa các URL của Facebook (ví dụ link Profile, Group).

```bash
python run_crawler.py
```
> **Kết quả**: Console sẽ in ra tiến trình của các worker. Cuối cùng dữ liệu được dump vào `outputs/crawl_results.json` theo định dạng mảng (Array) ứng với từng trang ở `pages.txt`.

#### 🚩 Các Command Line Flags (Khởi chạy bằng tay tham số cấu hình)
`run_crawler.py` hỗ trợ các cờ (flags) để đè lên cấu hình mặc định (ghi đè `.env` và `configs/config.json`):

1. **`--max-workers <num>`**: Cưỡng chế chạy với số luồng tự định.
   ```bash
   python run_crawler.py --max-workers 5
   ```

2. **`--selector-module <module_name>`**: Chọn module muốn cào trong JSON (ví dụ `'page'`, `'profile'`, `'group'`).
   ```bash
   python run_crawler.py --selector-module profile
   ```

### Dequeue và crawl theo UID từ hàng đợi
Script `scripts/dequeue_and_crawl.py` dùng để gọi API hàng đợi, nhận danh sách UID và chạy crawler theo cơ chế `fbprofile`.

```bash
python scripts/dequeue_and_crawl.py --api-key <YOUR_API_KEY>
```

**Lưu ý quan trọng:** Hiện script đang bật test mode mặc định bằng cách gán `args.test_uid = "cambongda"` trong `scripts/dequeue_and_crawl.py`. Nếu muốn dùng queue thật, hãy bỏ hoặc comment dòng này.

**Tùy chọn thường dùng:**
1. **`--api-key <key>`**: API key (hoặc set `API_KEY` trong môi trường).
2. **`--max-workers <num>`**: Override số luồng crawl.
3. **`--selector-module <module_name>`**: Ép module selector (ví dụ `profile`, `page`).
4. **`--test-uid <uid>`**: Chạy test với 1 UID, không gọi queue.
5. **`--out <path>`**: Lưu kết quả JSON ra file.
6. **`--events-url <url>`**: Endpoint nhận event hoàn thành (mặc định dùng trong script).

### Bước 3: Chạy Extraction đơn luồng (YAML)
```bash
python run_extraction.py
```
> **Kết quả**: JSON được lưu vào `outputs/pages_data.json` (hoặc file bạn override).

#### 🚩 Flags cho `run_extraction.py`
1. **`--config <path>`**: Chọn file YAML (mặc định `configs/config.yml`).
2. **`--pages-file <path>`**: Override danh sách URL.
3. **`--output-file <path>`**: Override JSON output.
4. **`--profile-path <path>`**, **`--profile-name <name>`**: Override profile.
5. **`--cookies-file <path>`**: Override cookies JSON. Nếu file không tồn tại sẽ fallback sang `COOKIES` trong `.env`.
6. **`--user-agent <ua>`**, **`--proxy <url>`**, **`--user-agents-file <path>`**, **`--proxies-file <path>`**.

---

## 🛑 Bắt Lỗi & Xử Lý Sự Cố (Troubleshooting)

1. **Lỗi `Unable to verify Facebook login` (Tạch Checkpoint / Rớt Cookie)**:
   - *Nguyên nhân:* Chuỗi `COOKIES` bị chết (expired) hoặc IP của bạn bị FB hất văng do bất thường.
   - *Giải pháp:* Lấy lại Cookie mới, xóa nội dung trong thư mục `PROFILE_DIR` và chạy lại `python run_login.py`. Đảm bảo Proxy đang sống.

2. **Lỗi `Failed to capture '<element>' on <url>` (Lỗi Không Tìm Thấy Phần Tử)**:
   - *Nguyên nhân:* Facebook thay đổi giao diện/cấu trúc code, hoặc do tốc độ mạng quá chậm nên timeout (`element_timeout = 15s`).
   - *Giải pháp:* Bật `HEADLESS=false` trong môi trường `.env` hoặc `config.json` để mắt nhìn thấy giao diện trình duyệt trực tiếp -> vào DevTools dò XPath/CSS mới cập nhật lại mảng `fallbacks` của thẻ đó trong `configs/config.json`.

3. **Lỗi Crash / Treo Luồng (Port in use)**:
   - *Nguyên nhân:* Port debug của Selenium bị gác kiếm chưa kịp close ở lần chạy trước.
   - *Giải pháp:* Kiểm tra hệ thống, kill tất cả tiến trình Google Chrome (`killall chrome` hoặc `taskkill /F /IM chrome.exe`). Mở rộng `PORT_RANGE_MAX` trong `.env`.

4. **Lỗi `Missing file: .../configs/pages.txt` khi chạy `run_extraction.py`**:
   - *Nguyên nhân:* `pages_file` đang trỏ tương đối theo `configs/`.
   - *Giải pháp:* Để `pages_file: data/pages.txt` trong `configs/config.yml` hoặc dùng `--pages-file`.
