# Kế hoạch: Giới hạn expand theo “cửa sổ” + prune DOM theo `article[role="article"]`

Mục tiêu:
- Crawl hết comment nhưng không để DOM phình quá lớn gây chậm/tràn bộ nhớ.
- Vẫn scroll “tự nhiên” (không giật nhảy), không làm hỏng cơ chế load thêm comment.
- Chỉ expand (View more / Xem thêm / replies) quanh viewport/cửa sổ, tránh quét toàn bộ `root`.
- Prune theo đơn vị `article[role="article"]` (xóa các article cũ đã xử lý).

Phạm vi file/điểm chèn chính:
- Luồng crawl comment chi tiết: `src/fbprofile/browser/selector_posts.py:916` (`parse_comment`)
  - Chèn logic “cửa sổ + prune” sau khi đã extract xong vòng hiện tại và **trước** `_scroll_comment_context(...)`
  - Vị trí khuyến nghị: giữa `src/fbprofile/browser/selector_posts.py:993` và `src/fbprofile/browser/selector_posts.py:1006`
- Expand replies/comments: `src/fbprofile/browser/selector_posts.py:1865` (`expand_comments_until_done`)
  - Thay vì quét toàn `root`, chuyển sang quét các expand buttons nằm trong “vùng gần viewport” (windowed expand).

---

## 1) Định nghĩa tham số “cửa sổ”

Thiết lập các ngưỡng (tinh chỉnh tùy máy & post):
- `KEEP_ANCHORS = 250`  
  Số anchor/comment cuối (gần đáy/viewport) luôn giữ lại trong DOM.
- `PRUNE_BUFFER = 150`  
  Chỉ prune khi DOM vượt `KEEP_ANCHORS + PRUNE_BUFFER` để tránh prune quá thường xuyên.
- `EXPAND_VIEWPORT_MARGIN_PX = 1200`  
  Mở rộng vùng viewport để expand (tính cả phía trên/dưới) nhằm bắt được “View more replies” vừa xuất hiện.
- `EXPAND_MAX_CLICKS_PER_ROUND = 30`  
  Tránh click vô hạn trong một vòng; sau đó để vòng lặp `parse_comment` chạy tiếp.
- `PRUNE_MAX_ARTICLES_PER_ROUND = 200`  
  Tránh xóa quá nhiều trong một lần (dễ gây giật).

Định nghĩa “processed”:
- Dựa trên `seen` trong `parse_comment` (key = `reply_comment_id or comment_id`) ở `src/fbprofile/browser/selector_posts.py:928-988`.
- Chỉ prune article nếu anchor `comment_id` trong article map ra `key` đã nằm trong `seen`.

---

## 2) Giới hạn expand “quanh viewport/cửa sổ”

Mục tiêu: chỉ tìm/click expand buttons nằm gần vùng đang xem, thay vì quét toàn bộ `root`.

Thiết kế:
1. Xác định “scroll container” đang dùng để load comment:
   - Tái sử dụng heuristic trong `_scroll_comment_context` (`src/fbprofile/browser/selector_posts.py:603-691`)
   - Ưu tiên trả về `target` (scrollable element) nếu tìm được.
2. Với mỗi vòng trong `parse_comment`:
   - Lấy danh sách `article[role="article"]` trong `root` (hoặc trong `scroll container` nếu xác định được).
   - Lọc các article “near viewport”:
     - Dùng `getBoundingClientRect()` để lấy `top/bottom`.
     - Giữ article có `bottom >= -EXPAND_VIEWPORT_MARGIN_PX` và `top <= window.innerHeight + EXPAND_VIEWPORT_MARGIN_PX`.
     - Nếu scroll container là nested, dùng rect của container để tính “viewport” tương ứng (container viewport).
3. Tìm expand buttons **chỉ trong các article near viewport**:
   - Reuse `_find_expand_buttons(article)` thay vì `_find_expand_buttons(root)`.
   - Click theo fingerprint như hiện tại để tránh click trùng.
   - Giới hạn click mỗi vòng: `EXPAND_MAX_CLICKS_PER_ROUND`.

Điểm triển khai:
- Thêm một hàm dạng “windowed expand” (ý tưởng):
  - `expand_comments_near_viewport(driver, root, *, margin_px, max_clicks, pause_after_click)`
- Trong `parse_comment`, thay `expand_comments_until_done(driver, root)` bằng:
  - `expand_comments_near_viewport(...)` trước
  - (tuỳ chọn) gọi `expand_comments_until_done(...)` với `max_rounds` nhỏ nếu bạn muốn fallback (nhưng mục tiêu là bỏ quét toàn bộ).

Tiêu chí an toàn:
- Không click các button không visible/enabled (reuse `_element_visible_enabled`).
- Không click những mục có thể gây điều hướng ra khỏi dialog (nếu phát hiện label giống “See translation”, “Share”,… thì bỏ).

---

## 3) Prune DOM theo `article[role="article"]` (an toàn, không giật scroll)

Mục tiêu: sau khi đã extract xong vòng hiện tại, xóa bớt article cũ (phía trên) đã processed, nhưng vẫn giữ nguyên cảm giác scroll.

Thiết kế “anchor + bù scroll”:
1. Từ danh sách `anchors = root.find_elements("a[href*='comment_id=']")` (đang có ở `src/fbprofile/browser/selector_posts.py:951-957`):
   - Nếu `len(anchors) <= KEEP_ANCHORS + PRUNE_BUFFER` → không prune.
2. Chọn cutoff anchor:
   - `cutoff_index = len(anchors) - KEEP_ANCHORS`
   - `cutoff_anchor = anchors[cutoff_index]` (đây là “neo” phải được giữ lại)
3. Trước khi xóa:
   - Đo vị trí `cutoff_anchor` so với viewport (hoặc container viewport):
     - `beforeTop = cutoff_anchor.getBoundingClientRect().top`
   - Nếu đang scroll trong nested container, đo `beforeTop` tương đối container (ví dụ `anchorRect.top - containerRect.top`).
4. Xác định tập article để prune:
   - Đi từ đầu danh sách anchor lên tới trước `cutoff_anchor`.
   - Với mỗi anchor:
     - Tìm `article = closest('[role="article"]')` (reuse `_closest_comment_article`).
     - Parse `key` từ href (`comment_id/reply_comment_id`) như đang làm ở `parse_comment`.
     - Nếu `key in seen` thì article “eligible”.
   - Dedup theo element id/outerHTML fingerprint để không remove nhiều lần.
   - Chỉ prune tối đa `PRUNE_MAX_ARTICLES_PER_ROUND`.
5. Thực hiện remove:
   - `article.parentNode.removeChild(article)` (hoặc `article.remove()`).
   - Không xóa các ancestor lớn (đừng xóa `root`/container), chỉ `article`.
6. Sau khi xóa:
   - Đo lại `afterTop` của cutoff anchor.
   - Bù scroll để `cutoff_anchor` quay về đúng vị trí:
     - `delta = afterTop - beforeTop`
     - Nếu delta > 0: scroll lên/giảm scrollTop; nếu delta < 0: scroll xuống/tăng scrollTop.
   - Bù đúng vào **cùng scroll container** đang dùng để load comment (nếu có), tránh bù `window.scrollTo` khi thực tế nested container mới là nơi scroll.

Tiêu chí an toàn khi prune:
- Chỉ prune article đã processed (`key in seen`).
- Không prune article nằm trong vùng near viewport hiện tại (để tránh “mất trước mắt”).
- Không prune nếu `cutoff_anchor` không còn tồn tại (stale) hoặc không đo được rect.
- Nếu sau prune phát hiện comment không load thêm nữa:
  - tăng `KEEP_ANCHORS`, giảm số lượng prune mỗi vòng, hoặc bỏ prune khi đang ở dialog đặc biệt.

---

## 4) Trình tự tích hợp vào `parse_comment` (flow)

Mỗi vòng trong `parse_comment`:
1. Xác định `root` bằng `_find_comment_context_root`.
2. (Best-effort) chọn filter “All comments”.
3. **Expand near viewport** (windowed expand) + pause ngắn.
4. Quét anchors `comment_id`, extract comment detail, update `seen`, append `collected`.
5. Nếu đã ổn định nhiều vòng → break theo logic hiện có.
6. **Prune DOM** theo article (chỉ khi vượt ngưỡng).
7. `_scroll_comment_context(driver, root)` để load batch tiếp theo.

---

## 5) Quan sát/Debug để tinh chỉnh

Các log/metrics nên thêm (tuỳ chọn):
- `len(anchors)` mỗi vòng, số article pruned, số clicks expand, số comment mới (`new_this_round`).
- Thời gian mỗi vòng: expand / extract / prune.

Chỉ báo “đang prune đúng”:
- `len(anchors)` dao động quanh `KEEP_ANCHORS + PRUNE_BUFFER` thay vì tăng vô hạn.
- Không xuất hiện hiện tượng “cuộn bị giật lên đầu”.
- Vẫn tiếp tục có comment mới sau mỗi lần `_scroll_comment_context`.

