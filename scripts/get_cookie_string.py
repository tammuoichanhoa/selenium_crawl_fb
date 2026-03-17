import json
with open("/home/baoanh/Downloads/Telegram Desktop/crawler_2_3_26/cookies.txt","r",encoding="utf-8") as f:
    data = json.load(f)
cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in data)
with open("cookie_string.txt","w",encoding="utf-8") as f:
    f.write(cookie_str)
print("Saved to cookie_string.txt")