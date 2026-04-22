from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
import time

# ====== CONFIG ======
URL = "https://www.facebook.com/tran.thanh.ne/posts/pfbid0PnRwubuP9UMUzR2iZXeNrq7nnTeyzbxRPdH1NmALT8vNG1rDykzEHmo1pVHDSQT9l"

# dùng profile chrome đã login sẵn
options = webdriver.ChromeOptions()
options.add_argument("--user-data-dir=./chrome_profile")

driver = webdriver.Chrome(options=options)
driver.get(URL)

time.sleep(5)

# ====== FUNCTION ======

def get_comments():
    # lấy tất cả comment bằng role="article"
    comments = driver.find_elements(By.XPATH, '//div[@role="article"]')
    return comments


def keep_last_3(comments):
    if len(comments) <= 3:
        return
    
    remove_list = comments[3:-3]

    for cmt in remove_list:
        try:
            driver.execute_script("""
                arguments[0].parentNode.removeChild(arguments[0]);
            """, cmt)
        except:
            pass


def scroll_load():
    driver.execute_script("window.scrollBy(0, 1000);")
    time.sleep(2)


# ====== MAIN LOOP ======

seen = set()

while True:
    comments = get_comments()

    print("Total comments:", len(comments))

    # in nội dung comment mới
    for cmt in comments:
        try:
            text = cmt.text
            if text not in seen:
                seen.add(text)
                print("NEW:", text[:100])
        except:
            pass

    # giữ lại 3 comment cuối
    keep_last_3(comments)

    # scroll để load thêm
    scroll_load()

    time.sleep(3)