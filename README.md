# Hướng dẫn chạy đăng nhập và crawler

Tài liệu này tóm tắt các bước để chuẩn bị môi trường, đăng nhập Facebook bằng Selenium (`login.py`) và chạy trình thu thập dữ liệu (`crawler.py`).

## 1. Yêu cầu hệ thống
- Python 3.10+ và `pip`.
- Google Chrome đã cài đặt sẵn cùng phiên bản ChromeDriver tương thích trong `PATH`.
- Thư viện Python trong `requirements.txt`: cài đặt bằng `pip install -r requirements.txt` (nên thực hiện trong virtualenv).

## 2. Chuẩn bị cấu hình chung
1. **Tạo file `.env`** (ở cùng thư mục với mã nguồn) với các biến sau:
   - `COOKIES`: chuỗi cookie Facebook theo định dạng `name=value; name2=value2`. Bắt buộc khi dùng `LOGIN_METHOD=cookies`.
   - `USER_AGENT`: (khuyến nghị) User-Agent giống trình duyệt thật để giảm checkpoint.
   - `LOGIN_METHOD`: `cookies` hoặc `profile`. Nếu bỏ trống sẽ dùng giá trị trong `config.json` (mặc định `cookies`).
   - `PROFILE_DIR`: đường dẫn thư mục profile khi đăng nhập một phiên. Nếu dùng nhiều profile, khai báo trong `PROFILE_DIRS` (danh sách cách nhau bởi dấu phẩy hoặc xuống dòng).
   - `HEADLESS`: `true/false` để bật chế độ không giao diện khi chạy `crawler.py`. `login.py` luôn mở UI để bạn kiểm tra.
   - `PROXY`: proxy ưu tiên dạng `http://user:pass@host:port`. Có thể liệt kê thêm trong `PROXIES_FILE` (mặc định `proxies.txt`).
   - `MAX_WORKERS`: số luồng crawler tối đa (sẽ được giới hạn tự động tùy `LOGIN_METHOD` và số profile).

2. **Tùy chỉnh `config.json`**:
   - Khối `login`: cấu hình mặc định cho `login.py` (`method`, `headless`, `profile_dir`).
   - Khối `crawl`:
     - `pages_file`: file chứa danh sách URL (mặc định `pages.txt`).
     - `profile_dirs`: danh sách thư mục Chrome profile dùng song song cho crawler khi đăng nhập bằng profile.
     - `selectors`: cấu hình selector (metadata + `elements`) theo chuẩn hoá.
     - `elements`: danh sách trường cần trích xuất (legacy, chỉ dùng khi chưa có `selectors`).
     - `output_file`: nơi lưu kết quả JSON (mặc định `crawl_results.json`).

3. **Kiểm tra dữ liệu đầu vào**:
   - `pages.txt`: mỗi dòng một URL Facebook, bỏ dòng trống hoặc comment bằng `#`.
   - `proxies.txt`: (tùy chọn) một proxy mỗi dòng.

## 3. Chạy script đăng nhập (`login.py`)
1. Đảm bảo `.env` và cấu hình proxy/profile đã sẵn sàng.
2. Kích hoạt virtualenv (nếu có) rồi chạy:
   ```bash
   python login.py
   ```
3. Script sẽ:
   - Tạo ChromeDriver với proxy, user-agent và thư mục profile theo `.env`/`config.json`.
   - Nếu `LOGIN_METHOD=cookies`: nạp cookie rồi kiểm tra trạng thái đăng nhập.
   - Nếu `LOGIN_METHOD=profile`: chỉ mở Chrome bằng profile đã có session và xác thực đăng nhập.
   - Khi đăng nhập thành công bằng cookies, thư mục profile sẽ được nén lưu vào `profiles/profile_backup_YYYYMMDD_HHMMSS.zip` (xem `utils.backup_profile_folder`).
4. Quan sát terminal để biết trạng thái. Đóng trình duyệt sau khi xác nhận bằng cách nhấn `Enter` trong console.

## 4. Chạy crawler (`crawler.py`)
1. Cập nhật `config.json` và `pages.txt` cho đúng nhóm trang cần thu thập.
2. Kiểm tra rằng mỗi profile trong `crawl.profile_dirs` đã đăng nhập sẵn (hoặc dùng cookies).
3. Chạy:
   ```bash
   python crawler.py
   ```
4. Ứng dụng sẽ:
   - Đọc `.env`, `config.json`, chọn proxy đang hoạt động (`utils.select_working_proxy`).
   - Tạo tối đa `MAX_WORKERS` luồng (tự giới hạn khi chỉ có một profile hoặc dùng cookies).
   - Với mỗi URL, truy cập, chờ `wait_after_load` giây, rồi trích xuất các phần tử theo `selectors` trong `config.json` (hoặc `crawl.elements` nếu chưa cấu hình selectors) bằng Selenium.
   - Lưu kết quả giữ nguyên thứ tự URL vào `crawl_results.json` (hoặc file bạn cấu hình).
5. Kiểm tra log trong terminal để biết worker nào thất bại, các lỗi sẽ được ghi cùng từng bản ghi (`error` field) trong file JSON.

## 5. Gợi ý xử lý sự cố
- **Checkpoint / đăng nhập thất bại**: kiểm tra chuỗi cookie, bật giao diện (tắt headless), dùng IP/residential proxy ổn định hơn.
- **`pages.txt is empty`**: đảm bảo file có ít nhất một URL và không chỉ có comment.
- **`Unable to verify Facebook login`**: xem thông tin debug in ra từ `get_facebook_login_debug_state`, cập nhật cookie hoặc profile.
- **ChromeDriver version mismatch**: cập nhật ChromeDriver để trùng phiên bản Chrome đang cài.

Giữ README này cập nhật khi bạn thay đổi cấu trúc `.env`, `config.json` hoặc thêm script mới để mọi người có thể chạy `login.py` và `crawler.py` dễ dàng.
