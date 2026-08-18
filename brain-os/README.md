# Brain OS V1

Brain OS là **governance + lifecycle layer** cho kho Markdown/Obsidian dùng cùng Javis OS. Brain OS không xây một Second Brain cạnh tranh với Javis và không sửa core `server/` hoặc `dashboard/`.

Ranh giới sở hữu hiện tại:

- **Brain OS:** Living Notes, stable identity, deterministic change detection, incremental diff, taxonomy, import/provenance/originals, ingestion policy & routing.
- **Javis OS:** AI Engine, INGEST execution, Wiki, Memory, Knowledge Graph, Skills và Loops/Scheduler.

Nguyên tắc triển khai V1: **boring, deterministic, recoverable và đáng tin trước; thông minh sau**.

## Quy ước thư mục trong repo

`brain-os/template/` là cây file mẫu để cài vào một Brain thật. Nó không phải một folder cần xuất hiện nguyên khối trong Obsidian/Javis Brain.

Ví dụ:

```text
brain-os/template/skills/brain-manager/scripts/brain_os.py
```

khi cài sẽ trở thành:

```text
<Brain>/skills/brain-manager/scripts/brain_os.py
```

Tương tự:

```text
brain-os/template/Javis/loops/brain-os-probe.md
```

trở thành:

```text
<Brain>/Javis/loops/brain-os-probe.md
```

## Ranh giới an toàn

- `brain-os/template/`: file sẽ được cài vào Brain.
- `brain-os/tools/`: công cụ dev/validator/installer, không phải dữ liệu Brain.
- `brain-os/tests/`: acceptance/regression tests của Brain OS.
- Không đưa dữ liệu cá nhân thật vào repo.
- Không sửa core Javis nếu Skill/Loop/Script trong Brain giải quyết được.
- Không dùng AI cho việc có thể quyết định chắc chắn bằng rule/hash/state.
- Không auto-create folder/tag tùy ý.
- Không tự Wiki hóa hoặc Memory hóa dữ liệu ở các chặng hiện tại.

Các cây capability/vận hành như `skills/`, `agents/`, `workflows/`, `plugins/`, `System/`, `Javis/` và `.javis/` được loại khỏi knowledge scan để Brain OS không tự index/tự kích hoạt chính nó.

## Trạng thái hiện tại

### Gate 0 — Runtime Probe: hoàn tất

- `probe_runtime.py`
- kiểm Python, Brain root, quyền ghi, SQLite/FTS5, PyYAML, UTF-8 filename, SHA-256, atomic rename và path resolution;
- loop probe mặc định `enabled: false`.

### Gate 1 — Config + Policy: hoàn tất

- config fail-safe;
- folder taxonomy;
- canonical tag taxonomy + aliases;
- metadata/folder/tag/ingest policy;
- foundation validator;
- `dry_run: true`, auto-move/auto-create mặc định tắt.

### Gate 2 — Deterministic Core + SQLite: hoàn tất

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

SQLite dưới `.javis/brain-index.db` là **derived/rebuildable state**, không phải source of truth. Markdown/file gốc vẫn là dữ liệu có thẩm quyền.

### Gate 3 — Scanner + Change Detection: hoàn tất

Bổ sung:

```text
brain_os_lib/
├── changes.py
├── scanner.py
├── diffing.py
└── reconcile.py
```

Scanner:

- quét `.md` và `.markdown`;
- bỏ qua capability/system/cache/attachments theo policy;
- không follow symlink;
- dùng SHA-256, không dùng mtime một mình để kết luận content changed;
- reuse hash khi stat không đổi và có `reconcile` full-hash để kiểm tra toàn vẹn;
- phát hiện `CREATED`, `MODIFIED`, `RENAMED`, `MOVED`, `DELETED`;
- identity matching theo bằng chứng duy nhất, ưu tiên `javis_id` rồi inode/device rồi exact hash;
- ambiguity hoặc duplicate identity thì không đoán;
- delete chỉ chuyển `MISSING`, không xóa source/Wiki;
- lỗi traversal làm suppress deletion detection của vòng scan đó;
- lưu incremental text snapshots dưới `.javis/snapshots/`;
- không rewrite note người dùng.

### Gate 4 — Document Type Classifier: hoàn tất

Bổ sung:

```text
brain_os_lib/
└── classifier.py
```

Document types:

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

Classifier không gọi AI. Precedence deterministic gồm zone, explicit frontmatter, path/filename và các signal đã định nghĩa. Tín hiệu yếu chỉ tạo proposal/`needs_ai`; nó không được commit speculative type.

`javis: auto|ignore|index|ingest|wiki` là processing override riêng, không bị trộn với semantic document type.

### Gate 5 — Folder Category + Tag Taxonomy: hoàn tất

Bổ sung chính:

```text
brain_os_lib/
├── taxonomy.py
└── metadata.py
```

Gate 5 chỉ **plan taxonomy**, chưa move note thật. Các invariant chính:

- một primary folder/category, nhiều canonical tags;
- chỉ chọn category đã đăng ký trong `System/Taxonomy/folders.yml`;
- tag alias được normalize về canonical tag;
- không auto-create folder/tag;
- không folder/tag explosion;
- Living Note không bị move tùy tiện theo một keyword mới xuất hiện;
- ambiguous decision giữ ở candidate/unresolved thay vì đoán;
- output có thể báo `would_move_to` nhưng không mutate source/frontmatter.

### Gate 6 — Living Note + Markdown Import: hoàn tất khi Gate 0–6 xanh

Bổ sung:

```text
brain_os_lib/
├── originals.py
└── importer.py
```

và CLI:

```bash
python skills/brain-manager/scripts/brain_os.py import <file.md>
```

Import V1 có hai chế độ:

```bash
# Preview an toàn — mặc định không ghi gì
python skills/brain-manager/scripts/brain_os.py import "/path/note.md"

# Ghi immutable snapshot + editable working copy
python skills/brain-manager/scripts/brain_os.py import "/path/note.md" --apply
```

Có thể chỉ định deterministic type/category đã tồn tại:

```bash
python skills/brain-manager/scripts/brain_os.py import "/path/note.md" \
  --type living_note \
  --category notes_personal_learning \
  --apply
```

Stage 6 **không** gọi Javis INGEST, không ghi Wiki, không ghi Memory và không gọi AI.

Cơ chế provenance:

```text
external/original Markdown
        │
        ├── byte-for-byte snapshot
        │   .javis/originals/imports/<javis_id>/original.md
        │
        ├── manifest provenance
        │   .javis/originals/imports/<javis_id>/manifest.json
        │
        └── editable working note
            Notes/... hoặc sources/...
```

Invariant Gate 6:

- source bên ngoài không bị sửa;
- `original.md` phải có SHA-256 đúng bằng source lúc import;
- snapshot được kiểm hash trước khi reuse; tamper thì fail closed;
- Living Note có stable `javis_id` trong working frontmatter;
- working note sửa độc lập mà không làm đổi immutable snapshot;
- rename/move working note vẫn giữ identity qua scanner;
- exact re-import không tạo duplicate và không overwrite edit của người dùng;
- nếu working copy mất nhưng snapshot còn, re-import phải dùng lại đúng identity/provenance cũ;
- cùng exact source bytes dưới tên external file khác không được fork thành identity thứ hai;
- reference source mặc định về `sources/_Unsorted/` nếu chưa có category đủ chắc chắn;
- chỉ category đã đăng ký mới được dùng; category bịa phải bị từ chối;
- technical hashes/state vẫn ở `.javis`, không nhồi vào frontmatter người dùng.

## CLI hiện có

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
python skills/brain-manager/scripts/brain_os.py taxonomy
python skills/brain-manager/scripts/brain_os.py taxonomy-plans --limit 50
python skills/brain-manager/scripts/brain_os.py import "/path/file.md"
python skills/brain-manager/scripts/brain_os.py import "/path/file.md" --apply
python skills/brain-manager/scripts/brain_os.py events --limit 50
```

`status`, `doctor`, `config`, `fingerprint`, `classifications`, `taxonomy-plans` và `events` là read-only. `scan`, `reconcile`, `classify`, `taxonomy` chỉ ghi derived state. `import` mặc định preview; chỉ `import --apply` được phép tạo immutable snapshot + working note của file đang được nhập.

## Gate kiểm thử 0–6

Từ root repo:

```bash
python -m compileall -q brain-os/template/skills/brain-manager/scripts
pytest -q \
  brain-os/tests/test_foundation.py \
  brain-os/tests/test_core_stage2.py \
  brain-os/tests/test_stage3_scanner.py \
  brain-os/tests/test_stage3_identity_edges.py \
  brain-os/tests/test_stage4_classifier.py \
  brain-os/tests/test_stage5_taxonomy.py \
  brain-os/tests/test_stage6_importer.py \
  brain-os/tests/test_stage6_cli.py \
  brain-os/tests/test_stage6_reimport_edges.py
```

Gate chỉ được đóng khi **toàn bộ** test của các chặng trước và chặng hiện tại cùng xanh. Không dùng skip/xfail/ignore hoặc hạ assertion để tạo green giả.

## Những gì V1 ở Gate 6 vẫn cố ý chưa làm

- chưa Amplenote migration adapter — đó là Gate 7;
- chưa AI Brain Manager/policy fallback — Gate 8;
- chưa Brain Watch automation — Gate 9; scheduler sẽ thuộc Javis Loop;
- chưa PDF/DOCX/Sheets import — Gate 10;
- chưa gọi Javis INGEST trong importer;
- chưa tự tạo Wiki/Memory;
- chưa semantic reorganization toàn vault;
- chưa vector DB/embeddings;
- chưa auto-create category/tag;
- chưa realtime filesystem daemon riêng.

Mặc định `System/BrainOS/config.yml` vẫn giữ `dry_run: true`, tắt auto-move và auto-create taxonomy. Quyền ghi ở Gate 6 chỉ mở theo hành động import explicit `--apply`, trên đúng file người dùng yêu cầu nhập.
