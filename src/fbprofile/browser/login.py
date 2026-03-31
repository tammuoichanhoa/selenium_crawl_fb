import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

def fb_login(driver, username, password):
    try:
        print(">>> Bắt đầu quy trình đăng nhập...")
        driver.get('https://www.facebook.com/')

        # 1. Điền thông tin và bấm Đăng nhập (Login form chuẩn)
        try:
            WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.ID, "email"))).send_keys(username)
            driver.find_element(By.ID, "pass").send_keys(password)
            driver.find_element(By.NAME, "login").click()
            print("   -> Đã bấm nút Đăng nhập lần 1.")
        except TimeoutException:
            # Trường hợp cookie còn sống, nó vào thẳng trang chủ hoặc trang checkpoint luôn mà không qua form login
            print("   -> Không thấy form đăng nhập, kiểm tra xem có phải đang ở Checkpoint không...")

        # --- XỬ LÝ CAPTCHA / CHECKPOINT ---
        # Kiểm tra xem có bị dính Captcha (Iframe) không
        try:
            print(">>> Đang quét iframe Captcha (Chờ 10s)...")
            
            # Tìm iframe (Google Recaptcha)
            captcha_iframe = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "iframe[src*='recaptcha'], iframe[title='reCAPTCHA']"))
            )
            
            print("   -> Đã tìm thấy Iframe Captcha. Đang switch vào...")
            driver.switch_to.frame(captcha_iframe)
            
            # --- TÌM NÚT CHECKBOX TRONG IFRAME ---
            try:
                # Cách 1: Dùng ID chuẩn của Google (recaptcha-anchor)
                checkbox = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "recaptcha-anchor"))
                )
            except TimeoutException:
                # Cách 2: Dùng XPath cụ thể mà bạn cung cấp (fallback)
                print("   -> Không thấy ID chuẩn, thử dùng XPath của bạn...")
                user_xpath = "/html/body/div[2]/div[3]/div[1]/div/div/span/div[1]"
                checkbox = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, user_xpath))
                )

            # Click Checkbox
            if checkbox.get_attribute("aria-checked") == "true":
                print("   -> Captcha đã được tích xanh từ trước.")
            else:
                checkbox.click()
                print("   -> Đã CLICK vào ô 'I'm not a robot'.")
            
            # Switch lại khung hình chính để bấm nút Gửi
            driver.switch_to.default_content()
            
            print(">>> Dừng 20 giây để bạn chọn hình (nếu Google hỏi)...")
            time.sleep(20) # Thời gian chờ người dùng thao tác tay
            
            # Sau khi Captcha xong, ở trang Checkpoint phải bấm nút "Tiếp tục" (Continue)
            # Khác với nút "login" ở trang chủ
            try:
                print("   -> Đang tìm nút Tiếp tục/Submit của Checkpoint...")
                # Thường là button type="submit" hoặc name="submit"
                submit_btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit'], [name='submit'], button[id*='checkpoint']")
                submit_btn.click()
                print("   -> Đã bấm nút Tiếp tục.")
            except Exception:
                # Nếu không thấy nút submit checkpoint, thử bấm lại nút login thường (phòng hờ)
                try:
                    driver.find_element(By.NAME, "login").click()
                except:
                    pass

        except TimeoutException:
            driver.switch_to.default_content()
            print(">>> LOG: Không phát hiện Captcha (hoặc đã vượt qua).")
        # -------------------------------------

        # 3. Kiểm tra kết quả cuối cùng (Vào được trang chủ chưa)
        try:
            # Chờ Feed tin tức xuất hiện
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'div[role="feed"], div[aria-label="Trang chủ"], div[aria-label="Facebook"], div[data-pagelet="FeedUnit"]'))
            )
            print(">>> ĐĂNG NHẬP THÀNH CÔNG!")
            return True
        except TimeoutException:
            print(">>> ĐĂNG NHẬP THẤT BẠI (Vẫn kẹt ở Login hoặc Checkpoint).")
            return False

    except Exception as e:
        print(f"Lỗi hệ thống: {str(e)}")
        try:
            driver.switch_to.default_content()
        except:
            pass
        return False