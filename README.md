# Facebook Auto Crawler

Dự án này là công cụ tự động cào dữ liệu Facebook (Profile, Page, Group...) bằng **Selenium**. Hệ thống được thiết kế theo kiến trúc phi tập trung, kết nối với API phân phối nghiệp vụ (Queue) để nhận linh hoạt UID xử lý, tiến hành phân giải và thu thập thông tin chuyên sâu (Deep Crawl) song song đa luồng, và tự động liên kết gởi kết quả về máy chủ.

Công cụ tối ưu hóa hiệu suất với cơ chế ẩn danh luân phiên, gỡ lỗi cổng linh hoạt, hỗ trợ lưu và sử dụng Cookies hoặc Chrome Profile để duy trì phiên đăng nhập bền vững. Hệ thống hiện tại có khả năng tự động nội suy (infer) cấu hình để quét Profile hoặc Page nhanh với độ chính xác tuyệt đối mà không cần chỉ định thủ công.

---

## 🚀 Hướng Dẫn Cài Đặt (Setup)

1. **Cài đặt thư viện Python:**
    Khuyến nghị tạo môi trường ảo hóa (`venv`) từ Python 3.10 trở lên để tránh va chạm thư viện:
    ```bash
    python -m venv venv
    
    # 1. Kích hoạt môi trường (trên Windows)
    venv\Scripts\activate
    
    # Kích hoạt môi trường (trên Linux/macOS)
    source venv/bin/activate
    
    # 2. Cài đặt các requirements
    pip install -r requirements.txt
    ```

2. **Cấu hình biến môi trường (`.env`):**
    Toàn bộ cấu hình hệ thống giờ được quản lý tập trung ở file `.env`. 
    Tiến hành copy `env` mẫu hoặc tự tạo một file `.env` cực gọn ngay trong thư mục clone:
    ```env
    # API & Endpoints
    API_KEY=your_api_token_here
    DEQUEUE_URL=https://<your-host>/tasks/dequeue?social_type=facebook&version=1.0
    EVENTS_URL=https://<your-host>/events
    
    # Định dạng Đăng nhập
    LOGIN_METHOD=Profile # Hỗ trợ: Profile, cookies, anonymous
    
    # Thư mục chứa Chrome profile và giới hạn luồng Worker
    PROFILE_DIRS=chrome_profile_1,chrome_profile_2,chrome_profile_3
    MAX_WORKERS=3
    
    # Phụ trợ ẩn hiển Chrome
    HEADLESS=false
    ```

---

## 🏃 Cách Chạy Ứng Dụng (How to run)

Entrypoint chính và duy nhất của toàn bộ hệ thống dự án nằm tại **`main.py`**. Cơ chế hoạt động: **Xin API tác vụ -> Phân phát đa luồng Cào dữ liệu -> POST kết quả về server**.

### 👉 Chạy Đầy Đủ API (Môi trường Thực tế)
Khởi chạy lệnh cơ bản để máy con (worker) tự động thực thi chuỗi tuần hoàn:
```bash
python main.py
```
> **Luồng tác vụ:** Khởi động, đọc biến môi trường để nạp URL. Mở `curl` liên lạc API `DEQUEUE_URL` để lấy UID tác vụ, đưa lên ThreadPoolExecutor lấy dữ liệu rồi gom báo cáo về đường dẫn `EVENTS_URL`. Sự kiện sẽ có log `event_type: complete` lúc xử lý thành công.

### 👉 Chạy Test Độc Lập 1 Account/Fanpage
Nếu phát triển nhanh, bạn muốn nhảy vọt gọi qua API nhận lệnh mà chỉ cần crawler thử trang `cambongda`:
```bash
python main.py --test-uid cambongda
```
> Nhờ tính năng tự động suy luận loại URL, tool sẽ đọc HTML hoặc Regex tự suy đoán "cambongda" là Fanpage/Profile và chọn bộ Module lấy bộ chọn (Selector) tương ứng mà không cầnbạn báo trước.

---

## 🚩 Các Cờ Tùy Chỉnh (CLI Flags)
Vì mọi thứ đã được nhúng trong `.env`, số lượng options tham số dòng lệnh cực kì tinh gọn:

| Flag | Chức năng chi tiết | Ví dụ đi kèm |
| :--- | :--- | :--- |
| `--test-uid` | Khởi chạy quét nhanh chuyên biệt 1 cục UID/URL, bỏ qua Queue Request. | `--test-uid zuck` |
| `--max-workers` | Định khung chèn đè lượng luồng Webdriver đang chạy song song, thay cho config. | `--max-workers 3` |
| `--api-key` | Token thủ công để bảo mật hệ thống khi Submit kết quả API. | `--api-key abcxyz` |
| `--out` | Đường dẫn file lưu cứng JSON Output toàn bộ dữ liệu cào ra. | `--out backup.json` |

---

## 🔄 Phác Tọa Core Workflow (Quy trình nội bộ)

Quá trình crawler diễn ra theo các bước giao tiếp API khép kín sau đây:

1. **Nhận Tác Vụ:** `main.py` kết hợp biến `DEQUEUE_URL` và Header gởi Request (Authorization). Trích mã JSON tìm kiếm công việc.
2. **Cơ Chế Suy Luận (Inference Engine):** Các hàm trong `task_flow.py` được khởi động để đánh giá nội dung tải xuống, quét HTML và đoán định thực thể Facebook (Người Dùng hay Fanpage) chính xác 100%. Tự động kết nối Selector đúng hệ.
3. **Deep Crawling Timeline:** Bóc tách Profile/Về Info và cuộn timeline lặp liên tục để bắt triệt để nội dung các Posts. Đóng băng checkpoint thường xuyên để lưu trạng thái file `.json/.ndjson` để lỡ Crash thì không mất trắng bài cũ.
4. **Đồng Bộ & Trả (Submit Events):** Hệ thống gom góp toàn bộ Object vào biến Payload gạch qua Event URL và báo cáo đóng Job.

---

## 🛑 Bắt Lỗi & Gỡ Rối Nhanh (Troubleshooting)

1. **Lỗi `[WinError 10061] Connection refused` hoặc `Max retries exceeded`**:
   - *Nguyên nhân:* Cửa sổ Chrome bị tắt cưỡng bức bằng tay, hoặc RAM quá tải trình duyệt Crash.
   - *Cách xử lý:* Giảm bớt số lượng Profile khai báo hoặc kéo `--max-workers 1`. Đừng đóng Chrome chừng nào Terminal log đang chạy.
   
2. **Facebook Block / Trắng tinh Cookie**:
   - *Nguyên nhân:* Trình điều khiển báo Cookie hết hạn, login văng Auth.
   - *Cách xử lý:* Dùng tiện ích Trình duyệt xuất lại Cookie Json gán vào `.env` hoặc cấp lại Profile thư mục mới sạch sẽ và login bằng tay lại một lần.

3. **Cào không được nội dung bài Post (Scroll cứng đơ)**:
   - *Nguyên nhân:* Bị FB gài Popup Đăng nhập Overlay đè che mất màn hình, làm tính toán chiều cao trang bị treo. 
   - *Khắc phục:* Login thật sống khỏe tài khoản trước khi thực thi, hoặc chọn `--test-uid` check trước trạng thái chặn địa lý VPN của tool.
