# Tài liệu task lấy dữ liệu ảnh từ Facebook Group

## 1. Mục tiêu

Task này dùng Facebook GraphQL API và lấy danh sách ảnh trong một group Facebook theo từng trang dữ liệu:

- `get_group_info.py`: lấy trang đầu tiên của bộ ảnh trong group.
- `get_next_group_info.py`: lấy trang tiếp theo dựa trên `cursor` của lần gọi trước.

Kết quả được lưu ra file JSON để phục vụ crawl hoặc xử lý tiếp.

## 2. Đầu vào chung

Cả hai script đều dùng cùng endpoint:

- `url`: `https://www.facebook.com/api/graphql/`

Header gửi đi gồm:

- `User-Agent`: chuỗi user agent bất kỳ, hiện tại là `Mozilla/5.0`
- `Content-Type`: `application/x-www-form-urlencoded`
- `Cookie`: cookie Facebook đăng nhập, tối thiểu đang thấy script dùng dạng:
  - `locale=vi_VN; c_user=XXX; xs=XXX;`

Lưu ý:

- `c_user` và `xs` là dữ liệu xác thực bắt buộc để Facebook trả dữ liệu.
- Nếu cookie hết hạn hoặc không đủ quyền truy cập group thì request có thể lỗi hoặc trả dữ liệu không đúng kỳ vọng.

## 3. Đầu vào của script lấy dữ liệu group lần đầu

Script này dùng để lấy dữ liệu trang đầu tiên của media trong group.

### Request payload

- `doc_id`: `4430099110431117`
- `variables` là JSON string, gồm:
  - `groupID`: ID group Facebook cần lấy dữ liệu
  - `scale`: hệ số scale ảnh, trong mẫu là `4`
  - `useCometPhotoViewerPlaceholderFrag`: cờ boolean, trong mẫu là `false`

### Ví dụ đầu vào

```json
{
  "doc_id": "4430099110431117",
  "variables": "{\"groupID\":\"804362789744484\",\"scale\":4,\"useCometPhotoViewerPlaceholderFrag\":false}"
}
```

### Ý nghĩa đầu vào

- `groupID` là đầu vào chính để xác định group cần crawl.
- `doc_id` xác định mẫu truy vấn GraphQL Facebook cho lần lấy đầu tiên.

## 4. Đầu vào của script lấy dữ liệu group những lần tiếp theo

Script này dùng để lấy trang tiếp theo của cùng bộ dữ liệu media.

### Request payload

- `doc_id`: `4544387022318594`
- `variables` là JSON string, gồm:
  - `cursor`: con trỏ phân trang lấy từ response trước
  - `scale`: hệ số scale ảnh, trong mẫu là `1`
  - `useCometPhotoViewerPlaceholderFrag`: cờ boolean, trong mẫu là `false`
  - `id`: ID group Facebook

### Ví dụ đầu vào

```json
{
  "doc_id": "4544387022318594",
  "variables": "{\"cursor\":\"AQHRmBn_UJsK3dR-yNSeM5EWqZrUOWENfN4yiKwCpt6ib02x7laEf3BabmJAJqN_1SVcd6Pbdm68LGtr5Ay9v5zlgw\",\"scale\":1,\"useCometPhotoViewerPlaceholderFrag\":false,\"id\":\"263510030791508\"}"
}
```

### Ý nghĩa đầu vào

- `cursor` là đầu vào bắt buộc để đi sang trang kế tiếp.
- `id` là group ID tương ứng với dữ liệu đang phân trang.
- `doc_id` ở script này là mẫu truy vấn dành cho phân trang tiếp theo, khác với lần lấy đầu tiên.


### File đầu ra

- `group_info.json`

### Cấu trúc chính

Đường dẫn dữ liệu chính:

```text
data.group.group_mediaset.media
```

Các trường quan trọng trong response:

- `data.group.id`: ID group
- `data.group.group_mediaset.reference_token`: token tham chiếu media set của group
- `data.group.group_mediaset.id`: ID media set
- `data.group.group_mediaset.media.edges`: danh sách item ảnh
- `data.group.group_mediaset.media.page_info.end_cursor`: cursor để lấy trang tiếp theo
- `data.group.group_mediaset.media.page_info.has_next_page`: còn trang tiếp theo hay không

### Cấu trúc một phần tử trong `edges`

Mỗi phần tử trong `edges` có dạng:

```json
{
  "node": {
    "__typename": "Photo",
    "id": "122210088464329177",
    "accessibility_caption": "Có thể là hình ảnh về em bé và bệnh viện",
    "image": {
      "uri": "https://..."
    },
    "viewer_image_orig": {
      "height": 1548,
      "width": 1290
    },
    "feedback": {
      "id": "..."
    },
    "immersive_photo_encodings": [],
    "is_playable": false,
    "owner": {
      "__typename": "User",
      "id": "61559875321869"
    }
  },
  "cursor": "AQHS..."
}
```

### Ý nghĩa đầu ra

Từ file này có thể lấy được:

- danh sách ảnh của group
- ID ảnh
- URL ảnh
- caption mô tả ảnh
- kích thước ảnh gốc
- owner của ảnh
- cursor từng item
- `end_cursor` để gọi trang tiếp theo

Trong mẫu hiện tại, response chứa `50` phần tử trong `edges`.


### File đầu ra

- `next_group_info.json`

### Cấu trúc chính

Đường dẫn dữ liệu chính:

```text
data.node.group_mediaset.media
```

Các trường quan trọng:

- `data.node.__typename`: kiểu node, trong mẫu là `Group`
- `data.node.id`: ID group
- `data.node.group_mediaset.reference_token`: token media set
- `data.node.group_mediaset.id`: ID media set
- `data.node.group_mediaset.media.edges`: danh sách ảnh của trang tiếp theo
- `data.node.group_mediaset.media.page_info.end_cursor`: cursor cho lần gọi kế tiếp
- `data.node.group_mediaset.media.page_info.has_next_page`: còn trang kế tiếp hay không

### Cấu trúc item

Cấu trúc từng phần tử `edges[]` giống với file `group_info.json`, gồm:

- `node.id`
- `node.accessibility_caption`
- `node.image.uri`
- `node.viewer_image_orig.height`
- `node.viewer_image_orig.width`
- `node.feedback.id`
- `node.is_playable`
- `node.owner.__typename`
- `node.owner.id`
- `cursor`

Trong mẫu hiện tại, response cũng chứa `50` phần tử trong `edges`.

## 7. Khác biệt giữa 2 loại response

Khác biệt chính:

- Lần đầu: dữ liệu nằm ở `data.group.group_mediaset.media`
- Trang tiếp theo: dữ liệu nằm ở `data.node.group_mediaset.media`

Ngoài khác biệt đó, cấu trúc `edges[]` và `page_info` về cơ bản là giống nhau.

## 8. Hành vi lưu file

Cả hai script đều:

- gọi `requests.post(...)`
- `raise_for_status()` nếu HTTP lỗi
- thử parse `res.json()`
- nếu parse JSON thành công thì lưu file với format đẹp (`indent=2`)
- nếu response không phải JSON thì lưu raw text vào file output

Tên file output được xác định cố định theo tên script:

- `get_group_info.py` -> `group_info.json`
- `get_next_group_info.py` -> `next_group_info.json`

## 9. Tóm tắt đầu vào và đầu ra

### Đầu vào tối thiểu

- Cookie Facebook hợp lệ
- Group ID
- Cursor phân trang, nếu muốn lấy trang tiếp theo
- `doc_id` phù hợp với từng loại truy vấn

### Đầu ra chính

- Danh sách ảnh trong group theo từng trang
- Thông tin từng ảnh:
  - ID ảnh
  - URL ảnh
  - caption
  - kích thước
  - owner
  - cursor item
- Thông tin phân trang:
  - `end_cursor`
  - `has_next_page`
