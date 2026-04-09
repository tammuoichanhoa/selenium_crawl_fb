# Facebook Crawl Pipeline

Dự án này là công cụ tự động cào dữ liệu Facebook (Profile, Page, Group...) bằng **Selenium**. Hệ thống được thiết kế để kết nối trực tiếp với API hàng đợi (Queue) để nhận nhiệm vụ, thực hiện thu thập thông tin chuyên sâu (Deep Crawl) song song đa luồng, và gửi kết quả về máy chủ.

Công cụ hỗ trợ mạnh mẽ việc xoay vòng User-Agent, proxy, gỡ lỗi cổng, và sử dụng Cookies hoặc Chrome Profile để duy trì phiên đăng nhập.

`main.py` là entrypoint nên dùng nếu bạn chạy theo queue hoặc muốn test nhanh một UID.

Chức năng chính:

* **Hệ điều hành:** Khuyến nghị Linux, macOS hoặc Windows.
* **Ngôn ngữ:** Python 3.10 trở lên.
* **Trình duyệt:** Bắt buộc cài đặt [Google Chrome](https://www.google.com/chrome/).
* **Driver:** Hệ thống có cơ chế tự tìm và tích hợp WebDriver phù hợp với bản Chrome hiện tại qua cơ chế nội bộ của Selenium Manager.

Ví dụ:

## 🚀 Hướng Dẫn Cài Đặt (Setup)

1. **Clone mã nguồn (nếu chưa có):**
    ```bash
    git clone <repository_url>
    cd facebook_crawler
    ```

2. **Cài đặt thư viện Python:**
    Tạo môi trường ảo hóa (`venv` hoặc `conda`) để tránh va chạm thư viện hệ thống:
    ```bash
    python -m venv venv
    
    # Kích hoạt môi trường (trên Windows)
    venv\Scripts\activate
    
    # Kích hoạt môi trường (trên Linux/macOS)
    source venv/bin/activate
    
    # Cài đặt file requirements
    pip install -r requirements.txt
    ```

3. **Cấu hình môi trường (`.env`):**
    Copy file mẫu `.env.example` sang `.env` (hoặc tạo file mới ` .env ` ở thư mục gốc) chứa các thiết lập cốt lõi:
    ```env
    # Mẫu .env khởi tạo
    API_KEY=your_api_token_here
    COOKIES=c_user=...;xs=...;fr=...;
    LOGIN_METHOD=cookies
    PROFILE_DIR=./chrome_profile
    MAX_WORKERS=3
    HEADLESS=false # Đặt true nếu chạy trên server ẩn UI
    ```

---

## 🏃 Cách Chạy Ứng Dụng (How to run)

Điểm bắt đầu (Entry point) chính của tool là file `main.py`. Tool được thiết kế theo mô hình vòng lặp tự động: **Xin nhiệm vụ -> Cào dữ liệu -> Trả kết quả**.

### Chạy trực tiếp qua API (Môi trường Thực tế)
```bash
python main.py
```
> **Luồng tác vụ:** Tool sẽ đọc `API_KEY` từ biến môi trường (hoặc `.env`), tự động đẩy yêu cầu lấy công việc từ máy chủ. Sau cài cào xong, hệ thống ngầm đóng gói kết quả và POST trả `event_type: complete` về máy chủ.

### Chạy để Test 1 UID/Fanpage cụ thể (Bỏ qua cấu hình Queue)
Nếu bạn chỉ muốn kiểm thử 1 fanpage (ví dụ trang `cambongda`) mà không gọi qua API lấy lệnh ngoài:
```bash
python main.py --test-uid cambongda --selector-module page
```

Trong [configs/base.json](/home/baoanh/Desktop/Workplace/selenium_crawl_fb/configs/base.json), crawler đang override thành:

## 🚩 Các Cờ Tham Số (Flags) cho `main.py`

| Flag | Chức năng chi tiết | Tùy chọn đi kèm ví dụ |
| :--- | :--- | :--- |
| `--test-uid` | Khởi chạy Crawler chỉ tập trung cào duy nhất Account UID (hoặc URL rút gọn) này để test. Bỏ qua luồng gọi lấy tác vụ từ Hàng đợi API. | `--test-uid cambongda` |
| `--max-workers` | Chèn đè (Override) lượng luồng Webdriver song song đang cố định gốc. Phù hợp nếu máy mạnh muốn chạy đa trình duyệt. | `--max-workers 3` |
| `--selector-module` | Ép công cụ dùng bộ logic cạo của cấu hình cho trước (có thể là `page`, `profile`, `group`) thay vì Tool tự dự đoán. | `--selector-module page` |
| `--api-key` | Token để xác thực lấy Việc / Nộp Việc qua API. Truyền vào đây làm biến ưu tiên. | `--api-key abcxyz...` |
| `--events-url` | Chỉ định URL của hệ thống nhận/lắng nghe báo cáo tiến độ và tiếp nhận Database thu thập được. | `--events-url https://api/...` |
| `--out` | Đường dẫn file lưu cứng lại JSON toàn bộ Output để kiểm thử độc lập ở Local. | `--out crawl_results.json` |

## Ghi chú triển khai

## 🔄 Luồng Gọi API và Xử Lý Dữ Liệu (API Flow)

Quá trình crawler diễn ra theo các bước giao tiếp API khép kín sau đây:

1. **Dequeue Task (Nhận tác vụ API):**
   - File `main.py` tiến hành đóng gói Header (chứa `Authorization: Bearer <API_KEY>`) để gọi lên đường dẫn Queue System.
   - Nhận về danh sách JSON chứa các `task_id` và đối tượng (`UID/URL`) cần cào. 

2. **Khởi tạo và Phân bổ Chạy Đa Luồng:**
   - Số lượng UID mục tiêu được chia nhét vào các bộ Batch.
   - Luồng nền `ThreadPoolExecutor` mở ra theo số lượng `--max-workers`. Mỗi một luồng chiếm dụng 1 cổng debug (Port), gắn Cookie và nạp Proxy mở sẵn màn hình Chrome.

3. **Thu thập dữ liệu chuyên sâu (Deep Crawling Workflow):**
   - Trình duyệt truy cập tận gốc mục tiêu.
   - **Page Info:** Quét sâu HTML: Lấy Tên, Lượt theo dõi, Avatar, Cover. Đặc biệt lấy dải ảnh High-Res (Độ phân giải siêu cao) bằng kỹ thuật *Nhân bản Tab ẩn (Tab Batching)*. Thu thập mục giới thiệu, tin đáng chú ý. Kết quả tạm lưu cache vào `database/profile/page/<tên>/profile_info.json`.
   - **Post Data:** Cuộn chuột lặp liên tục dải Timeline, cào toàn bộ nội dung Post từng giây, đóng dồn vào file Database cục bộ `posts_all.ndjson`. Mỗi mốc cuộn lại lưu Checkpoint bảo hiểm (tránh hỏng data).
   - **Đóng gói Kết quả:** Hệ thống đọc 2 file Database `profile_info` và toàn bộ nội dung file `posts_all.ndjson` gộp chung lại vào một biến từ điển Python (`page_data`).

4. **Trả kết quả (Submit / Post Event):**
   - `main.py` gọi hàm `_post_event()` để nhồi toàn bộ gói dữ liệu JSON `page_data` vô thuộc tính Payload của HTTP POST Request và bắn sang cho `--events-url`. Header vẫn tuân thủ kèm khóa `API_KEY`. Tác vụ hoàn tất vòng đời.

---

## 🛑 Bắt Lỗi & Xử Lý Sự Cố Thường Gặp (Troubleshooting)

1. **Lỗi `[WinError 10061] Connection refused` hoặc `Max retries exceeded with url`**:
   - *Nguyên nhân:* Cửa sổ Chrome bị tắt cưỡng bức bằng tay (dấu X màu đỏ), hoặc máy tính thiếu RAM dẫn tới trình duyệt "Crash" khiến ống thông tin giữa Selenium và hệ điều hành bị sập đứt đoạn.
   - *Cách xử lý:* Nếu máy yếu, giảm thông số `--max-workers 1`. Đừng bao giờ tắt thủ công cửa sổ Chrome khi log màn hình đang chạy chữ `[worker 1]`.

2. **Lỗi Không Login được Facebook (Tạch tài khoản / Trắng xóa Cookie)**:
   - *Nguyên nhân:* Chuỗi Cookie do bạn gắn trong tệp `.env` đã vi phạm ngày hết hạn, hoặc bị Facebook đá phiên.
   - *Cách xử lý:* Hãy trích xuất Cookie tươi lại từ Extension và đút vào `.env`. (Có thể dùng profile session để lưu lâu năm).

3. **Công cụ thu thập bài viết bị hẫng hoặc không thu bài nào**:
   - *Nguyên nhân:* Gặp Pop-up làm mờ nền màn hình yêu cầu Đăng nhập của Facebook cản trở tầm nhìn thẻ Div của DOM, và làm kẹt thao tác Cuộn (Scroll) chuỗi timeline.
   - *Cách khắc phục:* Đảm bảo trạng thái tài khoản Cookie cung cấp phải ở trạng thái sống khỏe và log-in trên trình duyệt. Mở thử trình duyệt ẩn lên trực quan xem Fanpage có đang khóa Vùng Địa Lý quốc gia với Account đang Crawler hay không.
