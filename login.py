import os
import logging

from utils import (
    backup_profile_folder,
    create_local_driver,
    get_facebook_login_debug_state,
    load_env_file,
    login_facebook_with_cookies,
    select_working_proxy,
    setup_logging,
    terminate_chrome_process,
    verify_facebook_login_state,
)

logger = logging.getLogger(__name__)


def main() -> None:
    setup_logging()
    env = load_env_file(".env")
    cookies_raw = env.get("COOKIES", "")
    user_agent = env.get("USER_AGENT", "")
    chrome_binary = env.get("CHROME_BINARY", "").strip() or None
    login_method = env.get("LOGIN_METHOD", "cookies").strip().lower()
    profile_dir = env.get("PROFILE_DIR", "").strip() or os.path.join(
        os.getcwd(), "chrome_profile"
    )
    proxies_file = env.get("PROXIES_FILE", "proxies.txt").strip() or "proxies.txt"
    proxy = select_working_proxy(env.get("PROXY"), proxies_file)

    # Tạo thư mục profile nếu chưa có
    os.makedirs(profile_dir, exist_ok=True)
    abs_profile_dir = os.path.abspath(profile_dir)

    # Chọn một port để Debugger nối vào (bạn có thể đổi số này nếu bị trùng)
    DEBUG_PORT = 9222

    # Gọi hàm khởi tạo Driver mới
    driver = create_local_driver(
        profile_path=abs_profile_dir,
        port=DEBUG_PORT,
        headless=False,
        chrome_binary_path=chrome_binary,
        proxy=proxy or None,
        user_agent=user_agent or None,
    )

    try:
        if login_method == "cookies":
            ok = login_facebook_with_cookies(driver, cookies_raw)
            if ok:
                logger.info("Facebook login by cookies: SUCCESS")
                try:
                    archive_path = backup_profile_folder(abs_profile_dir)
                    logger.info("Profile folder saved to: %s", archive_path)
                except Exception as err:
                    logger.warning("Failed to backup profile folder: %s", err)
            else:
                logger.warning("Facebook login by cookies: FAILED")
                logger.info("Debug info: %s", get_facebook_login_debug_state(driver))

        elif login_method == "profile":
            ok = verify_facebook_login_state(driver)
            if ok:
                logger.info("Facebook login via profile folder: SUCCESS")
            else:
                logger.warning("Facebook login via profile folder: FAILED")
                logger.info("Debug info: %s", get_facebook_login_debug_state(driver))
        else:
            raise ValueError(
                "LOGIN_METHOD must be either 'cookies' or 'profile' (default 'cookies')."
            )

        input("Nhấn Enter để đóng trình duyệt...")
    finally:
        driver.quit()
        terminate_chrome_process(driver)


if __name__ == "__main__":
    main()
