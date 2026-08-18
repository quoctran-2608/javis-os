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

### Gate 6 — Living Note + Markdown Import: hoàn tất

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

### Gate 7 — Amplenote Migration Adapter: hoàn tất

Bổ sung:

```text
skills/brain-manager/scripts/
├── import_amplenote.py
└── brain_os_lib/
    └── amplenote.py
```

Stage 7 là **migration adapter deterministic bọc quanh Gate 6**, không phải một importer/knowledge engine song song. Nó nhận Amplenote Markdown export dưới dạng thư mục hoặc ZIP, preflight toàn bộ Markdown trước khi ghi, rồi đưa từng note qua `import_markdown` để tái sử dụng cùng invariant provenance + stable identity của Gate 6.

Mặc định chỉ preview:

```bash
python skills/brain-manager/scripts/import_amplenote.py "/path/amplenote-export.zip"
```

Chỉ ghi khi explicit `--apply`:

```bash
python skills/brain-manager/scripts/import_amplenote.py "/path/amplenote-export.zip" --apply
```

Các invariant Gate 7:

- mặc định preview không ghi `.javis`, working note hay taxonomy vào Brain;
- toàn batch được preflight trước khi note đầu tiên được ghi;
- ZIP path traversal, absolute/drive path, symlink entry, encrypted entry và archive vượt safety limit bị từ chối fail-closed;
- Markdown note vẫn có immutable `original.md` byte-for-byte + `javis_id` ổn định qua Gate 6;
- ZIP export gốc được giữ nguyên byte-for-byte theo SHA-256 tại `.javis/originals/amplenote-exports/<sha256>/export.zip`;
- provenance của từng note ghi `source_system: amplenote`, `source_entry` và `export_sha256` khi nguồn là ZIP;
- legacy Amplenote tags được resolve qua registry/aliases hiện có; tag không biết không được tự biến thành canonical tag;
- exact re-import reuse identity/snapshot/working copy và **không overwrite user edits**;
- Living Note `ĐIỀU TÔI HỌC ĐƯỢC` với legacy tags `dieutoihocduoc`, `mylife` đi về `Notes/Personal/Learning/` và không bị chẻ thành nhiều note;
- non-Markdown asset trong ZIP chưa được materialize vào working Library ở Gate 7, nhưng không mất provenance vì toàn ZIP gốc đã được giữ nguyên;
- Stage 7 không gọi AI, Javis INGEST, Wiki hay Memory.

### Gate 8 — AI Brain Manager / Ingestion Policy & Routing: hoàn tất

Bổ sung:

```text
skills/brain-manager/
├── SKILL.md
├── references/
│   └── ai-output-schema.md
└── scripts/
    ├── brain_manager.py
    └── brain_os_lib/
        ├── ai_manager.py
        ├── jobs.py
        └── candidates.py
```

Stage 8 giữ ranh giới kiến trúc mới: **Javis thực thi AI; Brain OS chỉ governance + validation + routing**. Python không gọi model. Nó chỉ queue những case deterministic chưa giải được, cung cấp bounded evidence + constraints, validate output AI, rồi ghi derived state/candidate nếu hợp lệ.

Pipeline:

```text
deterministic classifier/taxonomy
        ↓ unresolved only
Brain OS jobs queue
        ↓
Javis chạy Brain Manager Skill
        ↓ structured JSON
Python schema/policy validator
        ↓
derived type/category/tag suggestion + routing/candidate
        ↓
Javis xử lý INGEST/Wiki/Memory ở chặng thực thi phù hợp
```

Các invariant Gate 8:

- chỉ queue case `needs_ai` hoặc taxonomy unresolved; case deterministic resolved không tốn AI;
- `javis: ignore|index` không bị AI nâng quyền;
- job id gắn với `source_id + content_hash + policy_id`, nên cùng state không tạo duplicate;
- evidence gửi AI bị giới hạn, không đọc vô hạn Living Note;
- note content được coi là **data**, không phải instruction; Skill ghi rõ chống prompt injection;
- output phải đúng schema, không field thừa/thiếu;
- stale `content_hash` bị từ chối, phải queue job mới;
- AI không được gán privileged type `memory`, `derived_wiki`, `system`, `binary_source`;
- deterministic document type/category đã commit luôn thắng AI;
- AI chỉ được chọn exact category id/canonical tag đã có trong registry;
- không auto-create category/tag;
- confidence dưới acceptance gate chỉ tạo `ai_review` candidate, không commit type/category/routing state;
- `ingest`/`incremental_ingest` chỉ chuyển derived processing state sang pending; **không tự chạy Javis INGEST**;
- `wiki_candidate` và `memory_candidate` chỉ ghi candidate có provenance, **không ghi Wiki/Memory thật**;
- Stage 8 không move/rename note, không rewrite frontmatter và không sửa user content;
- invalid/stale/escalating output fail-closed và job được đánh dấu failed để có thể review/requeue.

CLI Stage 8:

```bash
python skills/brain-manager/scripts/brain_manager.py queue --limit 3
python skills/brain-manager/scripts/brain_manager.py jobs --status pending --limit 3
python skills/brain-manager/scripts/brain_manager.py apply /tmp/brain-manager-result.json
python skills/brain-manager/scripts/brain_manager.py candidates --status pending
```

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
python skills/brain-manager/scripts/brain_os.py classifications --needs-ai --limit 50
python skills/brain-manager/scripts/brain_os.py taxonomy
python skills/brain-manager/scripts/brain_os.py taxonomy-plans --limit 50
python skills/brain-manager/scripts/brain_os.py import "/path/file.md"
python skills/brain-manager/scripts/brain_os.py import "/path/file.md" --apply
python skills/brain-manager/scripts/import_amplenote.py "/path/amplenote-export.zip"
python skills/brain-manager/scripts/import_amplenote.py "/path/amplenote-export.zip" --apply
python skills/brain-manager/scripts/brain_manager.py queue --limit 3
python skills/brain-manager/scripts/brain_manager.py jobs --status pending --limit 3
python skills/brain-manager/scripts/brain_manager.py apply /tmp/brain-manager-result.json
python skills/brain-manager/scripts/brain_manager.py candidates --status pending
python skills/brain-manager/scripts/brain_os.py events --limit 50
```

`status`, `doctor`, `config`, `fingerprint`, `classifications`, `taxonomy-plans`, `events`, Brain Manager `jobs` và `candidates` là read-only. Scanner/classifier/taxonomy/Brain Manager routing chỉ ghi derived state dưới `.javis`. Import/migration chỉ ghi user-facing working note khi có explicit `--apply`.

## Gate kiểm thử 0–8

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
  brain-os/tests/test_stage6_reimport_edges.py \
  brain-os/tests/test_stage7_amplenote.py \
  brain-os/tests/test_stage8_brain_manager.py
```

Gate chỉ được đóng khi **toàn bộ** test của các chặng trước và chặng hiện tại cùng xanh. Không dùng skip/xfail/ignore hoặc hạ assertion để tạo green giả.

Gate 8 đã được chứng minh trên GitHub Actions runner thật bằng temporary Stage 0–8 bridge; bridge được gỡ ngay sau proof để workflow repo trở lại đúng baseline.

## Những gì V1 ở Gate 8 vẫn cố ý chưa làm

- chưa Brain Watch automation — Gate 9; scheduler thuộc Javis Loop, Brain OS chỉ scan/change/job pipeline;
- chưa PDF/DOCX/Sheets import — Gate 10;
- chưa materialize Amplenote images/attachments vào working Library; ZIP provenance vẫn được giữ nguyên để không mất dữ liệu nguồn;
- Brain Manager chưa tự thực thi Javis INGEST;
- chưa tự tạo/update Wiki hoặc Memory; Stage 8 chỉ tạo routing/candidate;
- chưa semantic reorganization toàn vault;
- chưa vector DB/embeddings;
- chưa auto-create category/tag;
- chưa realtime filesystem daemon riêng.

Mặc định `System/BrainOS/config.yml` vẫn giữ `dry_run: true`, tắt auto-move và auto-create taxonomy. Gate 8 chỉ ghi derived operational state/candidates vào `.javis`; quyền xử lý tri thức thật vẫn được giữ ở Javis execution layer.
