[1] Selenium
    → login
    → lấy cookie + fb_dtsg + lsd

[2] Request
    → dùng cookie đó gọi GraphQL
    → crawl nhanh

[3] Khi bị block
    → quay lại Selenium refresh token

💡 Lợi ích:
- nhanh như request ⚡
- ổn định như selenium 🛡️

❌ Chỉ dùng request

→ chết sớm do:

token expire
bị block
❌ Chỉ dùng selenium

→ chạy được nhưng:

quá chậm
không scale được


-----------------------
Checklist chuẩn:

✔ tách Fetch / Parse / Service

✔ không hardcode doc_id

✔ chuẩn hoá pagination

✔ define output schema

✔ tách session riêng

✔ handle error

✔ không phụ thuộc sâu vào JSON FB

✔ design interface chung

✔ có retry / delay

✔ có logging

