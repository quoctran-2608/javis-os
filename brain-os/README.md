# Brain OS V1

Brain OS là lớp mở rộng chạy trên Brain của Javis, không sửa core `server/` hoặc `dashboard/`.

## Quy ước thư mục trong repo

`brain-os/template/` là **cây file mẫu để cài vào một Brain thật**. Nó không phải là một folder cần xuất hiện nguyên khối trong Obsidian/Javis Brain.

Ví dụ file trong repo:

```text
brain-os/template/skills/brain-manager/scripts/probe_runtime.py
```

khi cài vào Brain sẽ trở thành:

```text
<Brain>/skills/brain-manager/scripts/probe_runtime.py
```

Tương tự:

```text
brain-os/template/Javis/loops/brain-os-probe.md
```

sẽ trở thành:

```text
<Brain>/Javis/loops/brain-os-probe.md
```

Dấu `/` trong tài liệu chỉ là ký hiệu phân cách các thư mục lồng nhau; nó không nằm trong tên folder.

## Ranh giới an toàn

- `brain-os/template/`: file sẽ được cài vào Brain.
- `brain-os/tools/`: công cụ dev/validator/installer, không phải dữ liệu Brain.
- `brain-os/tests/`: test của Brain OS.
- Không đưa dữ liệu cá nhân thật vào repo code này.
- Không sửa core Javis nếu Skill/Loop/Script trong Brain giải quyết được.

Các cây capability/vận hành như `skills/`, `agents/`, `workflows/`, `plugins/`, `System/`, `Javis/` và `.javis/` được Brain OS loại khỏi knowledge scan để tránh tự index/tự kích hoạt chính nó.

## Trạng thái hiện tại

### Chặng 0 - Runtime probe: hoàn tất

- `probe_runtime.py`
- loop probe mặc định `enabled: false`

### Chặng 1 - Foundation: hoàn tất

- config fail-safe
- folder taxonomy
- tag taxonomy + aliases
- policy documents
- foundation validator

### Chặng 2 - Deterministic core: hoàn tất

Core nền:

```text
skills/brain-manager/scripts/
├── brain_os.py
└── brain_os_lib/
    ├── __init__.py
    ├── models.py
    ├── config.py
    ├── db.py
    ├── hashing.py
    ├── frontmatter.py
    ├── paths.py
    └── identity.py
```

### Chặng 3 - Scanner + Change Detection: hoàn tất

Bổ sung:

```text
brain_os_lib/
├── changes.py
├── scanner.py
├── diffing.py
└── reconcile.py
```

Chặng 3 cho Brain OS có khả năng **quan sát** vault nhưng vẫn chưa cho phép AI hoặc automation thay đổi note.

Scanner hiện làm được:

- quét `.md` và `.markdown`;
- bỏ qua `.javis`, `.obsidian`, `skills`, `agents`, `workflows`, `plugins`, `System`, `Javis`, cache/attachments;
- không follow symlink;
- dùng SHA-256 và chống file bị Obsidian sửa giữa lúc hash;
- scan nhanh reuse hash nếu size + mtime chưa đổi;
- `reconcile` full-hash để kiểm tra toàn vẹn định kỳ;
- phát hiện `CREATED`, `MODIFIED`, `RENAMED`, `MOVED`, `DELETED`;
- rename/move chỉ match khi có bằng chứng duy nhất theo thứ tự `javis_id → inode/device → exact content hash`;
- nếu match mơ hồ thì không đoán;
- duplicate `javis_id` trong cùng một scan bị vô hiệu hóa cho identity matching để không file nào “chiếm” ID theo thứ tự duyệt;
- delete chỉ chuyển state thành `MISSING`, không xóa dữ liệu hoặc Wiki;
- nếu traversal có lỗi thì suppress toàn bộ deletion detection của vòng đó;
- lưu change journal trong SQLite;
- lưu snapshot text phái sinh dưới `.javis/snapshots/` để tạo incremental line diff;
- snapshot mặc định tối đa 2 MiB/file và có thể xóa/rebuild mà không mất source of truth;
- scanner không tự thêm `javis_id` vào note: ID mới ở Chặng 3 chỉ được giữ trong DB nếu note chưa có ID.

### Chặng 4 - Document Type Classifier: hoàn tất

Bổ sung:

```text
brain_os_lib/
└── classifier.py
```

Classifier Chặng 4 **không gọi AI**. Nó áp dụng deterministic signals trước và lưu quyết định vào derived SQLite state.

Các loại tài liệu hiện có:

```text
living_note
reference_source
scratch
daily
weekly
monthly
future
memory
derived_wiki
system
binary_source
unknown
```

Nguyên tắc quan trọng:

- `Document Type` mô tả **vòng đời/ý nghĩa của tài liệu**, không đồng nghĩa với `ingest`, `wiki`, `move` hoặc bất kỳ hành động nào.
- tín hiệu mạnh như zone đã cấu hình hoặc `javis_type` hợp lệ có thể được deterministic classifier chấp nhận;
- tín hiệu yếu như tên file `2026-08-17.md` ngoài zone chỉ tạo `proposed_type: daily` + `needs_ai: true`; DB vẫn giữ `document_type: unknown`;
- `javis_type` là override type chuyên dụng; field `type` chỉ là fallback khi giá trị đúng một Brain OS type đã biết;
- processing override `javis: ignore|index|ingest|wiki|auto` được ghi nhận riêng, không bị trộn với document type;
- `javis: index` hoặc `javis: ignore` có thể tránh AI classification vì người dùng đã chỉ rõ route an toàn, nhưng classifier vẫn không bịa document type;
- classifier đọc frontmatter theo bounded probe, mặc định tối đa 64 KiB; nó không đọc cả Living Note dài chỉ để lấy YAML đầu file;
- malformed/oversized frontmatter không làm chết cả batch: classifier ghi warning rồi fallback sang tín hiệu zone/path;
- cache classification dựa trên `classifier_version + policy_id + content_hash + path`, nên rename/move, sửa nội dung hoặc đổi policy sẽ tự làm cache stale;
- move Living Note từ `Notes/` sang `sources/` có thể được reclassify từ `living_note` sang `reference_source` mà vẫn giữ `source_id` của Chặng 3;
- `MISSING` record không được classifier đụng vào;
- mọi classification metadata đều nằm trong `.javis/brain-index.db`, không được ghi ngược vào note.

CLI hiện hỗ trợ:

```bash
python skills/brain-manager/scripts/brain_os.py doctor
python skills/brain-manager/scripts/brain_os.py status
python skills/brain-manager/scripts/brain_os.py config
python skills/brain-manager/scripts/brain_os.py fingerprint "Notes/example.md"
python skills/brain-manager/scripts/brain_os.py init
python skills/brain-manager/scripts/brain_os.py scan
python skills/brain-manager/scripts/brain_os.py scan --full-hash
python skills/brain-manager/scripts/brain_os.py reconcile
python skills/brain-manager/scripts/brain_os.py classify
python skills/brain-manager/scripts/brain_os.py classify --path "Notes/example.md"
python skills/brain-manager/scripts/brain_os.py classify --force
python skills/brain-manager/scripts/brain_os.py classifications --needs-ai --limit 50
python skills/brain-manager/scripts/brain_os.py events --limit 50
```

`status`, `doctor`, `config`, `fingerprint`, `classifications` và `events` là read-only. `init`, `scan`, `reconcile`, `classify` chỉ ghi **derived state** trong `.javis/`; chúng không move, rename, rewrite, ingest hoặc Wiki hóa note người dùng.

## Gate kiểm thử

Từ root repo:

```bash
python -m compileall -q brain-os/template/skills/brain-manager/scripts
pytest -q \
  brain-os/tests/test_foundation.py \
  brain-os/tests/test_core_stage2.py \
  brain-os/tests/test_stage3_scanner.py \
  brain-os/tests/test_stage3_identity_edges.py \
  brain-os/tests/test_stage4_classifier.py
```

Các invariant Chặng 3 bắt buộc phải giữ:

- first scan chỉ nhận file Markdown hợp lệ;
- scan thứ hai không sinh event `UNCHANGED` rác;
- sửa Living Note sinh incremental diff mà không rewrite note;
- rename giữ nguyên `source_id`;
- move + edit chỉ được nhận là cùng file khi có bằng chứng đủ mạnh;
- hash trùng nhiều file không được đoán rename;
- duplicate `javis_id` không được quyết định bằng thứ tự duyệt file;
- `.markdown` dùng cùng stable identity logic như `.md`;
- delete → `MISSING`, không xóa record/snapshot;
- restore giữ identity;
- `javis_id` trong frontmatter có ưu tiên cao nhất khi identity đó là duy nhất;
- lỗi traversal phải suppress delete;
- CLI scan phải báo `writes_user_files: false` và chỉ tạo state dưới `.javis/`.

Các invariant Chặng 4:

- zone chuẩn được deterministic classify với confidence cao;
- `javis_type` hợp lệ có provenance và có thể override zone;
- field `type` lạ như `meeting` không bị Brain OS chiếm nghĩa;
- tín hiệu tên file yếu chỉ là proposal, không được commit speculative type;
- unknown + `javis: index` không cần AI nhưng vẫn giữ type `unknown`;
- malformed hoặc oversized frontmatter không làm chết batch;
- frontmatter probe bị chặn trong 1 KiB..1 MiB, mặc định 64 KiB;
- classification cache invalid khi hash/path/policy thay đổi;
- move qua zone phải reclassify đúng nhưng giữ stable identity;
- missing record không reclassify;
- CLI `classify` phải báo `uses_ai: false`, `writes_user_files: false`;
- pure classifier không mutate file người dùng.

## Những gì Chặng 4 vẫn cố ý CHƯA làm

- chưa gọi AI cho các record `needs_ai`;
- chưa quyết định giá trị/durability của từng đoạn Living Note;
- chưa Folder Category Manager apply thật;
- chưa Tag Taxonomy apply thật;
- chưa auto move;
- chưa auto create category/tag;
- chưa ingest;
- chưa Memory/Wiki candidate;
- chưa Brain Watch loop tự động.

Mặc định `System/BrainOS/config.yml` vẫn để `dry_run: true`, tắt auto-move và auto-create taxonomy cho tới khi các gate kiểm thử ở những chặng sau đạt.
