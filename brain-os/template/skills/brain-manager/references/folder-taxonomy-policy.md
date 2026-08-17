# Brain OS - Folder Taxonomy Policy

## Folder dùng để làm gì

Folder trả lời một câu hỏi duy nhất:

> File này có **nhà chính** ở đâu?

Một file chỉ có một primary home. Folder không phải là nơi biểu diễn toàn bộ semantic meaning của bài viết.

## Phân loại theo scope

Có ít nhất hai scope khác nhau:

### Living Notes

Root: `Notes/`

Ưu tiên context sử dụng lâu dài của note, ví dụ:

```text
Notes/Personal/Learning/
Notes/Work/
Notes/Ideas/
```

### Knowledge Sources

Roots:

```text
sources/
Library/
```

Ưu tiên domain kiến thức, ví dụ:

```text
Accounting/Tax/
Business/Operations/
AI/
Technology/
```

`Library/` và `sources/` nên dùng cùng category ID/path cho cùng một source original + normalized source.

## Thứ tự quyết định

1. User vừa tự move file -> tôn trọng vị trí mới.
2. Explicit `category` hợp lệ trong frontmatter.
3. Category hiện tại đã hợp lý -> giữ nguyên.
4. Registry `System/Taxonomy/folders.yml`.
5. Existing tags/title/headings/content/related notes.
6. AI classifier khi deterministic evidence chưa đủ.
7. Không chắc -> `_Unsorted` hoặc giữ nguyên vị trí.

## Dominant topic

Folder classifier phải hỏi:

> Nếu 6 tháng sau người dùng tìm file này bằng cây folder, họ sẽ tìm ở đâu đầu tiên?

Không dùng câu hỏi:

> Bài này nhắc tới tất cả chủ đề nào?

Câu hỏi thứ hai dành cho tags/Wiki links.

## Độ sâu

- Mặc định 1-2 tầng category.
- Tối đa 3 tầng nếu thật sự cần.
- Không tạo hierarchy sâu chỉ vì có nhiều concept.

Tốt:

```text
Accounting/Tax/
Accounting/Tax/Legal/
Business/Operations/
```

Không tốt:

```text
Accounting/Tax/VAT/Input VAT/Invoice/Legal/
```

## Tạo category mới

Mặc định `propose_only`.

Không tạo category mới chỉ vì:
- gặp một keyword mới;
- có một file duy nhất;
- synonym/alias chưa được normalize;
- một concept có thể giải quyết bằng tag/Wiki.

Ưu tiên category cha hiện hữu nếu đủ dùng.

## Confidence

Theo config mặc định:

```text
>= 0.80        -> đủ điều kiện auto-move khi tính năng auto-move đã được bật
0.55 - 0.80    -> candidate/suggestion
< 0.55         -> _Unsorted hoặc giữ nguyên
```

Trong giai đoạn `dry_run` hoặc `allow_auto_move: false`, mọi mức confidence chỉ sinh proposal, không move thật.

## Folder stability

Living Note không được chuyển folder liên tục theo đoạn nội dung mới.

Chỉ đề xuất move khi:
- user yêu cầu;
- category hiện tại rõ ràng sai;
- user đã thay đổi bản chất note;
- taxonomy được refactor có chủ đích.

## User move wins

Nếu người dùng tự move file trong Obsidian:
- xem đó là hành động có chủ đích;
- cập nhật index path;
- nếu folder mới map rõ vào category registry thì có thể đề xuất cập nhật `category` metadata;
- không tự kéo file về folder cũ chỉ vì classifier cũ khác ý người dùng.

## Không nhân bản file để phục vụ nhiều category

Nếu một bài vừa liên quan Accounting vừa AI, chọn một primary home và dùng tags/Wiki links cho phần còn lại. Không copy cùng một file vào nhiều folder chỉ để phân loại.
