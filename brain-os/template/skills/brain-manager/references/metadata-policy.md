# Brain OS - Metadata Policy

## Mục tiêu

Frontmatter trong Markdown phải hữu ích cho người dùng và Obsidian, không trở thành nơi chứa state kỹ thuật liên tục thay đổi.

## Metadata có thể ghi vào note

Brain OS chỉ nên dùng các field sau khi thực sự cần:

```yaml
javis_id: note_xxx
type: living-note
origin: amplenote
category: personal/learning
tags:
  - personal/learning
legacy_tags:
  - dieutoihocduoc
javis: auto
```

Ý nghĩa:

- `javis_id`: stable identity của note/source được Brain OS quản lý.
- `type`: loại tài liệu theo schema Brain OS.
- `origin`: nguồn import nếu có; không bắt buộc cho note tạo trực tiếp trong Obsidian.
- `category`: primary category duy nhất.
- `tags`: canonical tags dùng trong Obsidian.
- `legacy_tags`: tag cũ cần bảo toàn lịch sử nhưng không tiếp tục dùng làm canonical taxonomy.
- `javis`: manual override của người dùng.

## Metadata không được ghi vào note

State kỹ thuật phải nằm trong `.javis/brain-index.db`, ví dụ:

```text
content_hash
last_seen_hash
last_ingested_hash
last_indexed_at
last_ingested_at
state
retry_count
classifier_version
```

Lý do:
- tránh Javis tự sửa note liên tục;
- tránh watcher tự kích hoạt lại do metadata kỹ thuật;
- giữ Markdown sạch và hữu ích cho người dùng.

## Bảo toàn frontmatter của người dùng

- Không xóa key lạ/không thuộc Brain OS.
- Không reorder/rewrite toàn frontmatter nếu không có thay đổi thực tế cần ghi.
- Không thay tag người dùng bằng canonical tag một cách phá huỷ nếu chưa có policy migration rõ ràng.
- Legacy tag khi migrate phải được giữ ở `legacy_tags` trước khi canonicalize.

## Stable identity

`javis_id` phải ổn định qua rename/move.

- Không dùng pathname làm identity duy nhất.
- Không cấp ID mới chỉ vì tên file thay đổi.
- Re-import cùng một snapshot/source không được tạo ID mới nếu có thể nhận diện chắc chắn là cùng nguồn.

## Manual override

Field:

```yaml
javis: auto | ignore | index | ingest | wiki
```

Người dùng override luôn thắng classifier AI, trừ safety rule cứng.

## Write discipline

- Khi `dry_run: true`, không ghi metadata vào user note.
- Khi apply metadata, phải dùng atomic write.
- Nếu nội dung frontmatter sau normalize không thay đổi, không rewrite file.
- Body Markdown phải được giữ nguyên byte/text nhiều nhất có thể; metadata writer không được tự format lại body.
