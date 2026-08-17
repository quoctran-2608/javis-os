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

Core hiện có:

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

Chặng 2 **chưa scan vault, chưa move file, chưa classify bằng AI và chưa ingest**.

CLI an toàn hiện hỗ trợ:

```bash
python skills/brain-manager/scripts/brain_os.py doctor
python skills/brain-manager/scripts/brain_os.py status
python skills/brain-manager/scripts/brain_os.py config
python skills/brain-manager/scripts/brain_os.py fingerprint "Notes/example.md"
python skills/brain-manager/scripts/brain_os.py init
```

`status`, `doctor`, `config`, `fingerprint` là read-only. `init` chỉ tạo state directory + `.javis/brain-index.db`; không tạo/move/sửa note.

## Kiểm thử trước khi sang chặng tiếp theo

Từ root repo:

```bash
python -m compileall -q brain-os/template/skills/brain-manager/scripts
pytest -q brain-os/tests/test_foundation.py brain-os/tests/test_core_stage2.py
```

Các gate quan trọng của Chặng 2:

- SQLite index là derived/rebuildable, không phải source of truth.
- DB từ chối downgrade nếu schema trên đĩa mới hơn code.
- Path traversal ra ngoài Brain bị chặn.
- Hash dùng SHA-256 và phát hiện file thay đổi trong lúc đang hash.
- Frontmatter update không rewrite file nếu metadata không đổi.
- Khi phải thêm metadata, body Markdown, BOM và kiểu newline được giữ.
- `javis_id` có dry-run trước khi ghi.
- CLI `status`/`doctor` không vô tình tạo DB.
- `init` không tạo `Notes/`, `wiki/` hay thay đổi nội dung người dùng.

Mặc định `System/BrainOS/config.yml` vẫn để `dry_run: true`, tắt auto-move và auto-create taxonomy cho tới khi các gate kiểm thử ở những chặng sau đạt.
