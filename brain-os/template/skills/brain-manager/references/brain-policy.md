# Brain OS - Core Policy

## Mục tiêu

Brain OS giúp Javis nhận biết, tìm, phân loại và chưng cất tri thức trong cùng vault mà người dùng mở bằng Obsidian. Hệ thống phải ưu tiên an toàn dữ liệu và khả năng tiếp tục chỉnh sửa note của người dùng.

## Thứ tự ưu tiên quyết định

Khi nhiều rule xung đột, áp dụng theo thứ tự sau:

1. Safety rule và vùng cấm ghi/ingest.
2. Manual override của người dùng (`javis:`).
3. Hành động trực tiếp của người dùng trên filesystem (đổi tên, move note).
4. Zone policy trong `System/BrainOS/config.yml`.
5. Document type đã xác định.
6. Folder/tag taxonomy registry.
7. Heuristic deterministic.
8. AI classifier.
9. Khi vẫn không chắc: **index only**, không tự đoán.

## Source of truth

- Markdown và file thật trong Brain là source of truth.
- `.javis/brain-index.db` là derived state, mất thì rebuild được.
- Wiki là tri thức phái sinh; không thay thế Living Note hoặc source gốc.
- File original trong Library/snapshot không được AI sửa.

## Các cấp xử lý

```text
DISCOVER -> INDEX -> CLASSIFY -> INGEST -> COMPOUND
```

`un-ingested` không có nghĩa là `invisible`: một note đã index vẫn phải tìm và đọc được khi người dùng hỏi.

## Dry run

Khi `dry_run: true`:

- được scan, hash, index vào môi trường test/dev nếu command cho phép;
- được tính proposed classification;
- được báo proposed move/tag/category;
- **không move/rename file người dùng**;
- **không ghi metadata vào note người dùng**;
- **không tạo Wiki/Memory tự động**;
- **không tạo category/tag mới**.

Mọi write path sau này phải kiểm tra dry-run ở lớp cuối trước khi ghi, không chỉ dựa vào prompt.

## Vùng không được ingest

Tối thiểu:

- `.javis/**`
- `wiki/**` (được index, không ingest lại)
- `00 - Dashboard/**`
- các path nằm trong `ignore_paths`

## Living Note

Living Note là tài sản người dùng sở hữu và tiếp tục chỉnh sửa.

- Không tự chia note thành nhiều file vật lý.
- Không tự thay Living Note bằng Wiki.
- Không move liên tục theo chủ đề mới xuất hiện.
- Nếu người dùng tự move note trong Obsidian, xem đó là tín hiệu chủ động và không tự kéo file về vị trí cũ.
- Chỉ phần thay đổi cần được xem xét cho incremental ingest khi có đủ state.

## Reference Source

Reference Source là tài liệu chủ yếu để đọc/tham khảo.

- Markdown reference có thể ở `sources/`.
- PDF/DOCX/Sheet original ở `Library/`, bản máy đọc ở `sources/`.
- Wiki chỉ được tạo/update khi có giá trị tái sử dụng, không theo mỗi heading của source.

## Self-trigger prevention

File do Brain OS/Javis tạo trong `wiki/` hoặc `.javis/` không được đưa ngược vào ingest pipeline.

## Delete

Khi source biến mất:

- mark missing trong index;
- giữ provenance;
- không tự xóa Wiki/Memory ngay;
- cleanup là hành động riêng có kiểm soát.

## Nguyên tắc cuối

Khi không chắc nên làm gì, chọn hành động ít phá huỷ hơn:

```text
IGNORE < INDEX < CANDIDATE < INGEST < WRITE/MOVE
```
