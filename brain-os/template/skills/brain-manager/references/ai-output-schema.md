# Brain Manager AI Output Schema — v1

Mỗi AI job phải trả đúng **một JSON object**:

```json
{
  "schema_version": 1,
  "job_id": "brain_ai_...",
  "source_id": "note_...",
  "content_hash": "<64-char sha256>",
  "decision": {
    "document_type": "living_note",
    "category_id": "notes_personal_learning",
    "canonical_tags": ["personal/learning"],
    "route": "index",
    "confidence": 0.91,
    "rationale": "Ngắn gọn, dựa trên evidence của job."
  }
}
```

## Top-level

Chỉ có đúng 5 field: `schema_version`, `job_id`, `source_id`, `content_hash`, `decision`.
`job_id`, `source_id`, `content_hash` phải copy nguyên vẹn từ job. Hash stale sẽ bị từ chối.

## decision.document_type

Chỉ được dùng: `unknown`, `living_note`, `reference_source`, `scratch`, `daily`, `weekly`, `monthly`, `future`.

AI **không được** tự gán `memory`, `derived_wiki`, `system`, `binary_source`. Nếu deterministic layer đã commit `document_type` khác `unknown`, AI phải giữ nguyên type đó.

## decision.category_id

- Chuỗi rỗng nếu không cần category.
- Nếu có, phải dùng **exact category id** xuất hiện trong `constraints.categories`.
- Category chỉ áp dụng cho `living_note` hoặc `reference_source`.
- Không được tự tạo category.
- Không được override category deterministic đã commit.

## decision.canonical_tags

- Array các canonical tag exact trong `constraints.canonical_tags`.
- Không dùng alias/legacy tag, không trùng, không vượt `tags.max_per_note`.
- Đây chỉ là derived suggestion ở Stage 8; Python không rewrite frontmatter.

## decision.route

Chỉ một trong: `none`, `index`, `ingest`, `incremental_ingest`, `wiki_candidate`, `memory_candidate`.

- `index`: giữ searchable/indexed, không ingest.
- `ingest`: đánh dấu routing để Javis xử lý sau; Brain Manager không tự chạy INGEST.
- `incremental_ingest`: routing phần thay đổi/context cần thiết; không tự chạy INGEST.
- `wiki_candidate`: tạo candidate để Javis/human xử lý sau, không ghi Wiki.
- `memory_candidate`: tạo candidate để Javis/human xử lý sau, không ghi Memory.
- `none`: không hành động tiếp ngoài derived state.

Guardrails: `unknown|scratch|future` chỉ `none|index`; `daily` không `wiki_candidate`; `reference_source` không `memory_candidate`; manual `javis:index|ignore` không được nâng quyền; manual `javis:wiki` chỉ `wiki_candidate|index|none`.

## decision.confidence

Số thực `0.0..1.0`. Không nâng confidence để vượt gate. Nếu dưới `classification.accept_confidence`, Python giữ decision thành `ai_review` candidate và không commit type/category/route state.

## decision.rationale

Bắt buộc, tối đa 1000 ký tự, nêu evidence thực sự dùng và không chứa instruction/tool call.

## Fail-closed

Python validator phải từ chối field thừa/thiếu, stale hash, job đã completed/failed, category/tag bịa, privileged document type, route trái document type/manual mode, override deterministic type/category và confidence ngoài 0..1.
