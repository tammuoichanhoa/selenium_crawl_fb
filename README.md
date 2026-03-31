# Facebook Selenium Crawler

Dự án này là một công cụ cào dữ liệu (crawler) trên nền tảng Facebook sử dụng thư viện **Selenium** kết hợp với hệ thống trích xuất dữ liệu dựa trên **Selector JSON** đa cấu hình. Công cụ hỗ trợ chạy tự động, đa luồng (multi-threading), gán proxy tĩnh/động, xoay vòng User-Agent, và quản lý đăng nhập thông qua Cookie hoặc Chrome Profile.

## 📌 Luồng Hoạt Động (Workflow)

Hệ thống được chia làm hai giai đoạn (script) hoàn toàn tách biệt:

1. **Khởi tạo và Đăng nhập (`run_login.py`)**: 
   - Script này dùng để đăng nhập Facebook thông qua Local Chrome Driver.
   - Bạn có thể điền Cookie vào file môi trường (`.env`) để tool tự động tiêm cookie, hoặc thiết lập đăng nhập bằng thẻ (`profile`) hiện có.
   - Khi đăng nhập thành công thông qua Cookies, hệ thống sẽ tự động sao lưu (backup) thư mục `profile` đó. Folder profile này sẽ được dùng để vượt qua lớp bảo mật (Checkpoint) cho các luồng Worker sau này.
   
2. **Cào Dữ Liệu (`run_crawler.py`)**:
   - Trình phân phối sẽ nạp danh sách URL từ file `data/pages.txt`.
   - Các URL được chia nhỏ thành các batch dựa trên số `MAX_WORKERS` (tối đa luồng song song).
   - Mỗi Worker sẽ được cấp một cổng Debug (Debugger Port), sử dụng một bộ User-Agent, Proxy và Profile được chỉ định riêng biệt.
   - Trình duyệt sẽ được tự động mở (headless hoặc không ảo), truy cập trang, đợi load xong và tuân theo các **Selector** (XPath/CSS) trong file `configs/config.json` để nhặt dữ liệu (như tên, số follower, số theo dõi, bio, email, ...).
   - Kết quả xuất ra file đuôi `.json`, ví dụ: `data/crawl_results.json`.

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
    mkdir -p data configs chrome_profile
    ```

---

## ⚙️ Hướng Dẫn Cấu Hình (Configurations)

Phần cấu hình của Tool bao gồm 2 file chính là `.env` và `configs/config.json`.

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

# Giả lập thiết bị (Fingerprint)
HEADLESS=true                    # Chạy ngầm (true) hoặc hiện UI (false)
USER_AGENTS_FILE=data/user_agents.txt
USER_AGENT_ROTATION=true
PROXY_ROTATION=false
# PROXY=http://user:pass@127.0.0.1:3128

# Bật gỡ lỗi Selector (Selector Debug)
SELECTOR_DEBUG=1
SELECTOR_LOG_CONFIG=1
SELECTOR_CAPTURE=1
SELECTOR_CAPTURE_DIR=./debug_artifacts
```

### 2. Dữ Liệu Đầu Vào (`data/`)
- **`data/pages.txt`**: Khai báo danh sách URL sẽ đi cào (Mỗi URL 1 dòng). Dùng ký tự `#` để comment dòng.
- **`data/user_agents.txt`**: Khai báo danh sách user agents (mỗi user agent 1 dòng).

### 3. Khai Báo Selectors (`configs/config.json`)
File này chịu trách nhiệm trỏ chính xác vào cấu trúc HTML của FB để cào dữ liệu ra. Thiết kế hỗ trợ *modules*.

```json
{
  "login": {
    "method": "cookies",
    "headless": false,
    "profile_dir": "./chrome_profile"
  },
  "crawl": {
    "pages_file": "data/pages.txt",
    "output_file": "data/crawl_results.json",
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

---

## 🏃 Chạy Ứng Dụng

### Bước 1: Login khởi tạo session (Chỉ Cần Làm 1 Lần)
Đầu tiên, chạy tập lệnh Login để nhúng cookie / khởi tạo thư mục profile gốc.

```bash
python run_login.py
```
> **Giải thích**: Script sẽ đọc `.env` (lấy `COOKIES` và `PROFILE_DIR`), mở trình duyệt thật lên. Nếu phương thức `login_method=cookies`, script tiêm cookie thẳng vào trang và tải lại để xác nhận. Cuối cùng, nó sẽ *Back up* folder profile `chrome_profile` để tái dùng khi Crawler chạy đa luồng. Hãy ấn `Enter` trên Terminal để tắt khi đã thấy login mượt.

### Bước 2: Chạy Crawler trích xuất Dữ Liệu
Hãy đảm bảo file `data/pages.txt` đã có chứa các URL của Facebook (ví dụ link Profile, Group).

```bash
python run_crawler.py
```
> **Kết quả**: Console sẽ in ra tiến trình của các worker. Cuối cùng dữ liệu được dump vào `data/crawl_results.json` theo định dạng mảng (Array) ứng với từng trang ở `pages.txt`.

#### 🚩 Các Command Line Flags (Khởi chạy bằng tay tham số cấu hình)
`run_crawler.py` hỗ trợ các cờ (flags) để đè lên cấu hình mặc định (ghi đè `.env` và `config.json`):

1. **`--max-workers <num>`**: Cưỡng chế chạy với số luồng tự định.
   ```bash
   python run_crawler.py --max-workers 5
   ```

2. **`--selector-module <module_name>`**: Chọn module muốn cào trong JSON (ví dụ `'page'`, `'profile'`, `'group'`).
   ```bash
   python run_crawler.py --selector-module profile
   ```

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
