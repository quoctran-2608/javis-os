# Brain OS - Tag Taxonomy Policy

## Tag dùng để làm gì

Tag trả lời:

> File này thuộc những nhóm/chủ đề nào?

Một note có thể có nhiều tag, nhưng tag không được thay thế folder hoặc Wiki concept.

## Canonical tags

- Canonical tags nằm trong `System/Taxonomy/tags.yml`.
- Alias nằm trong `System/Taxonomy/tag-aliases.yml`.
- Luôn reuse canonical tag trước khi đề xuất tag mới.
- Alias phải được normalize về canonical tag trước khi ghi.

Ví dụ:

```text
vat
gtgt
thuế-gtgt
thue-vat
```

đều có thể map về:

```text
accounting/tax/vat
```

## Hierarchical tags

Dùng slash hierarchy theo cách Obsidian hiểu tự nhiên:

```text
personal/learning
accounting/tax/vat
business/finance/cash-flow
```

- Mặc định 1-3 level.
- Không tạo hierarchy sâu nếu không có nhu cầu điều hướng thật.

## Không biến mọi concept thành tag

Một note có thể nói về nhiều concept nhưng chỉ cần vài note-level tags.

Ví dụ Living Note `ĐIỀU TÔI HỌC ĐƯỢC` có thể chứa:

```text
Điềm tĩnh
Tự tin
Tâm xả
Nghỉ ngơi
Giao tiếp
Lựa chọn
```

Nhưng tags có thể chỉ là:

```yaml
tags:
  - personal/learning
  - personal/life
```

Các concept chi tiết nên đi vào Wiki/wikilinks, không tự động trở thành tag.

## Giới hạn

Theo config mặc định:

```text
max_per_note: 6
max_depth: 3
allow_auto_create: false
```

Nếu vượt giới hạn, ưu tiên giữ tag tổng quát và có giá trị tìm kiếm lâu dài hơn.

## Legacy tags

Khi import Amplenote hoặc hệ thống cũ:

- giữ tag cũ trong `legacy_tags` trước khi canonicalize;
- map alias sang canonical tag nếu registry đã biết;
- không xoá lịch sử chỉ vì taxonomy mới khác tên.

Ví dụ:

```yaml
legacy_tags:
  - dieutoihocduoc
  - mylife

tags:
  - personal/learning
  - personal/life
```

## Tag mới

Mặc định chỉ `propose_only`.

Không tạo tag mới vì:
- chỉ xuất hiện một lần;
- là synonym của tag hiện hữu;
- chỉ là một entity/concept tốt hơn nếu dùng Wiki link;
- chỉ lặp lại tên folder mà không tạo thêm giá trị tìm kiếm.

## Manual user tags

Tag người dùng tự thêm là tín hiệu mạnh. Brain OS không được âm thầm xóa tag user chưa biết chỉ vì nó chưa nằm trong registry.

Nếu tag chưa canonical:
- giữ nguyên;
- có thể đề xuất mapping/alias;
- chỉ migration có chủ đích mới rewrite tag.

## Khi dry run

Chỉ báo:

```text
existing tags
canonical mapping
proposed tags
unknown tags
```

Không ghi lại note.
