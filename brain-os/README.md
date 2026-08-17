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
python skills/brain-manager/scripts/brain_os.py events --limit 50
```

`status`, `doctor`, `config`, `fingerprint` là read-only. `init`, `scan`, `reconcile` chỉ ghi **derived state** trong `.javis/`; chúng không move, rename, rewrite, ingest hoặc Wiki hóa note người dùng.

## Gate kiểm thử

Từ root repo:

```bash
python -m compileall -q brain-os/template/skills/brain-manager/scripts
pytest -q \
  brain-os/tests/test_foundation.py \
  brain-os/tests/test_core_stage2.py \
  brain-os/tests/test_stage3_scanner.py \
  brain-os/tests/test_stage3_identity_edges.py
```

Các case Chặng 3 bắt buộc phải giữ:

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

## Những gì Chặng 3 vẫn cố ý CHƯA làm

- chưa AI classification;
- chưa Folder Category Manager apply thật;
- chưa Tag Taxonomy apply thật;
- chưa auto move;
- chưa auto create category/tag;
- chưa ingest;
- chưa Memory/Wiki candidate;
- chưa Brain Watch loop tự động.

Mặc định `System/BrainOS/config.yml` vẫn để `dry_run: true`, tắt auto-move và auto-create taxonomy cho tới khi các gate kiểm thử ở những chặng sau đạt.
