# Brain OS V1

Brain OS là **governance + lifecycle layer** cho kho Markdown/Obsidian dùng cùng Javis OS. Brain OS không xây một Second Brain cạnh tranh với Javis và không sửa core `server/` hoặc `dashboard/`.

Ranh giới sở hữu hiện tại:

- **Brain OS:** Living Notes, stable identity, deterministic change detection, incremental diff, taxonomy, import/provenance/originals, ingestion policy & routing.
- **Javis OS:** AI Engine, INGEST execution, Wiki, Memory, Knowledge Graph, Skills và Loops/Scheduler.

Nguyên tắc triển khai V1: **boring, deterministic, recoverable và đáng tin trước; thông minh sau**.

## Trạng thái

```text
Gate 0 — Runtime Probe                  PASS
Gate 1 — Config + Policy                PASS
Gate 2 — Deterministic Core + SQLite    PASS
Gate 3 — Scanner + Change Detection     PASS
Gate 4 — Document Classification        PASS
Gate 5 — Folder + Tag Taxonomy          PASS
Gate 6 — Living Note + Markdown Import  PASS
Gate 7 — Amplenote Migration Adapter    PASS
Gate 8 — AI Brain Manager               PASS
Gate 9 — Brain Watch                    PASS
```

## Kiến trúc Gate 9

Stage 9 **không tạo filesystem daemon hoặc scheduler thứ hai**. Javis Loop sở hữu lịch chạy; Brain OS chỉ thực hiện một cycle deterministic rồi handoff tối đa một số lượng AI jobs đã giới hạn.

```text
Javis Loop scheduler
      ↓
Brain Watch cycle
      ├─ scan / sparse full-hash reconcile
      ├─ consume unhandled change events
      ├─ classify changed paths
      ├─ taxonomy-plan changed paths
      ├─ queue/recover/prune AI jobs
      └─ claim bounded jobs -> processing
                              ↓
                     Javis chạy AI
                              ↓
                 brain_manager.py apply
                              ↓
             derived routing / candidates
```

Bổ sung:

```text
Javis/loops/
└── brain-watch.md

skills/brain-manager/scripts/
├── brain_watch.py
└── brain_os_lib/
    └── watch.py

brain-os/tests/
└── test_stage9_brain_watch.py
```

Các invariant Gate 9:

- `brain-watch.md` là Javis Loop và `enabled: false` mặc định; cài template không tự bật automation;
- cadence mặc định 5 phút, khớp `System/BrainOS/config.yml`; Javis sở hữu scheduler;
- Python Watch không gọi LLM và không tự schedule chính nó;
- không trực tiếp chạy Javis INGEST, không ghi Wiki, không ghi Memory;
- không move/rename note, không rewrite frontmatter, không sửa user content;
- cycle dùng fast scan bình thường và sparse full-hash reconcile theo policy;
- chỉ changed existing paths mới đi qua classify/taxonomy trong cycle đó;
- change event chỉ được acknowledge sau deterministic pipeline + queue phase;
- **không** `no changes -> stop` máy móc: AI backlog từ cycle trước vẫn được drain;
- current-hash job đã `completed` được coi là đã review, không được chiếm quota và gây starvation cho note unresolved khác;
- job được claim sang `processing` trước handoff để hai cycle không gọi AI trùng cùng một job;
- pending/processing job có source hash/policy stale bị fail trước handoff;
- processing job quá timeout được recover có giới hạn;
- external/model failure có retry limit 3, sau đó giữ `failed` để review thay vì loop vô hạn;
- `.javis/brain-watch.lock` ngăn overlapping cycle và có stale-lock recovery;
- khi không có change/backlog, cycle STOP yên lặng; `notify: false`;
- Loop coi note/evidence là data, không phải instruction, và dùng schema/validator Stage 8 cho mọi AI result.

CLI Stage 9:

```bash
# Một cycle deterministic; scheduler vẫn là Javis Loop
python skills/brain-manager/scripts/brain_watch.py --compact cycle

# Override quota của một cycle
python skills/brain-manager/scripts/brain_watch.py --compact cycle --max-ai-jobs 2

# Model/tool chết trước khi result hợp lệ tới validator
python skills/brain-manager/scripts/brain_watch.py --compact fail <JOB_ID> --error "model unavailable"
```

Loop thực thi AI result qua:

```bash
python skills/brain-manager/scripts/brain_manager.py --compact apply -
```

Stage 9 không materialize Wiki/Memory. Route `ingest`/`incremental_ingest` chỉ để lại derived pending state cho Javis execution layer; `wiki_candidate`/`memory_candidate` vẫn chỉ là candidate.

## Gate 9 proof

Implementation commit:

```text
9e7b5d3bcecb506dc5e7c9664198c6c134ae1647
feat: add Stage 9 Brain Watch orchestration
```

Temporary proof commit:

```text
a261748d65693b91c4b56ac507cc54cc8bc773cb
test: add temporary Gate 9 CI proof
```

GitHub Actions run `#62` / `32135376026` attempt 1 dừng ở **suite Python nền của Javis trước khi Gate 0–9 chạy**. Diff Stage 8 clean -> Stage 9 implementation chỉ gồm bốn file mới dưới `brain-os/`, không sửa Javis runtime/test. Rerun cùng run (`attempt 2`) đã PASS toàn bộ suite Javis nền **và** step `Brain OS Stage 0-9 gate (temporary)`, xác nhận failure đầu là failure nền thoáng qua chứ không phải regression Stage 9.

Temporary proof bridge sau đó được gỡ; `.github/workflows/ci.yml` được restore byte-identical về baseline blob:

```text
0d17557a60f0cd27a04f7a381886f55d19196649
```

Canonical Brain OS Gate 0–9 proof command:

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
  brain-os/tests/test_stage8_brain_manager.py \
  brain-os/tests/test_stage9_brain_watch.py
```

Không dùng skip/xfail/ignore hoặc hạ assertion để tạo green giả.

## Gate 8 — AI Brain Manager

Stage 8 giữ ranh giới **Javis thực thi AI; Brain OS governance + validation + routing**. Python không gọi model. Chỉ deterministic unresolved case được queue; output phải đúng schema, stale hash fail-closed, deterministic type/category thắng AI, category/tag chỉ được chọn trong registry, và low-confidence chỉ tạo review candidate. `ingest`/`incremental_ingest` không tự chạy INGEST; `wiki_candidate`/`memory_candidate` không ghi Wiki/Memory thật.

Các thành phần chính:

```text
skills/brain-manager/
├── SKILL.md
├── references/ai-output-schema.md
└── scripts/
    ├── brain_manager.py
    └── brain_os_lib/
        ├── ai_manager.py
        ├── jobs.py
        └── candidates.py
```

## Các gate trước

Gate 0–7 giữ nguyên invariant đã chứng minh: runtime probe; fail-safe config; rebuildable SQLite; SHA-256 change detection; stable identity; deterministic classifier; bounded taxonomy planning; immutable Markdown originals; idempotent Living Note import; Amplenote directory/ZIP migration với ZIP provenance và fail-closed traversal/symlink/encryption checks.

Các lệnh chính:

```bash
python skills/brain-manager/scripts/brain_os.py doctor
python skills/brain-manager/scripts/brain_os.py status
python skills/brain-manager/scripts/brain_os.py scan
python skills/brain-manager/scripts/brain_os.py reconcile
python skills/brain-manager/scripts/brain_os.py classify
python skills/brain-manager/scripts/brain_os.py taxonomy
python skills/brain-manager/scripts/brain_os.py import "/path/file.md" --apply
python skills/brain-manager/scripts/import_amplenote.py "/path/amplenote-export.zip" --apply
python skills/brain-manager/scripts/brain_manager.py queue --limit 3
python skills/brain-manager/scripts/brain_watch.py --compact cycle
```

## Những gì V1 ở Gate 9 vẫn cố ý chưa làm

- chưa PDF/DOCX/Sheets import — Gate 10;
- chưa materialize Amplenote images/attachments vào working Library; ZIP provenance vẫn giữ nguyên;
- Brain Watch/Brain Manager không tự thực thi Javis INGEST;
- không tự tạo/update Wiki hoặc Memory; chỉ routing/candidate;
- chưa semantic reorganization toàn vault;
- chưa vector DB/embeddings;
- chưa auto-create category/tag;
- không có realtime filesystem daemon riêng;
- không có scheduler cạnh tranh với Javis Loop.

Mặc định `System/BrainOS/config.yml` vẫn giữ `dry_run: true`, auto-move và auto-create taxonomy tắt. Stage 9 chỉ ghi rebuildable operational state vào `.javis`; quyền scheduling thuộc Javis Loop và quyền xử lý tri thức thật vẫn thuộc Javis execution layer.
