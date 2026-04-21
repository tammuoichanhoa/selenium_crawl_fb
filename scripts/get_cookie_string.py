import json
with open("/home/baoanh/Desktop/crawler/selenium_crawl_fb/groups/cookies.txt","r",encoding="utf-8") as f:
    data = json.load(f)
cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in data)
with open("cookie_string.txt","w",encoding="utf-8") as f:
    f.write(cookie_str)
print("Saved to cookie_string.txt")