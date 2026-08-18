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
```

## Kiến trúc Gate 8

Stage 8 giữ đúng ranh giới: **Javis thực thi AI; Brain OS chỉ governance + validation + routing**. Python không gọi model. Nó queue những case deterministic chưa giải được, cung cấp bounded evidence + constraints, validate structured output AI, rồi ghi derived state/candidate nếu hợp lệ.

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
Javis xử lý INGEST/Wiki/Memory ở execution layer
```

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

Các invariant Gate 8:

- chỉ queue case `needs_ai` hoặc taxonomy unresolved; deterministic resolved không tốn AI;
- `javis: ignore|index` không bị AI nâng quyền;
- job id gắn `source_id + content_hash + policy_id`, cùng state không duplicate;
- evidence gửi AI bị giới hạn; Living Note không bị gửi toàn bộ mặc định;
- note content là **data**, không phải instruction; Skill có rule chống prompt injection;
- output phải đúng schema, không field thừa/thiếu;
- stale `content_hash` fail-closed và phải queue job mới;
- AI không được gán privileged type `memory`, `derived_wiki`, `system`, `binary_source`;
- deterministic document type/category đã commit luôn thắng AI;
- AI chỉ được chọn exact category id/canonical tag có trong registry;
- không auto-create category/tag;
- confidence dưới acceptance threshold chỉ tạo `ai_review` candidate, không commit type/category/routing state;
- `ingest`/`incremental_ingest` chỉ chuyển derived processing state sang pending, không tự chạy Javis INGEST;
- `wiki_candidate`/`memory_candidate` chỉ ghi candidate có provenance, không ghi Wiki/Memory thật;
- không move/rename note, không rewrite frontmatter, không sửa user content;
- invalid/stale/escalating output fail-closed và job chuyển `failed` để review/requeue.

CLI Stage 8:

```bash
python skills/brain-manager/scripts/brain_manager.py queue --limit 3
python skills/brain-manager/scripts/brain_manager.py jobs --status pending --limit 3
python skills/brain-manager/scripts/brain_manager.py apply /tmp/brain-manager-result.json
python skills/brain-manager/scripts/brain_manager.py candidates --status pending
```

## Gate proof

Implementation commit:

```text
bf7054bdfac67bbeb11a67b8e67c6e526e783bd0
feat: add Stage 8 Brain Manager governance bridge
```

Temporary proof commit:

```text
e027ff277b467a24b80158879554c69808029432
test: add temporary Gate 8 CI proof
```

GitHub Actions run `#54` / `32131991839` chạy suite Javis chuẩn trước, sau đó canonical Brain OS Gate 0–8; step `Brain OS Stage 0-8 gate (temporary)` đã PASS. Temporary bridge sau đó được gỡ và `.github/workflows/ci.yml` restore đúng baseline blob `0d17557a60f0cd27a04f7a381886f55d19196649`.

Canonical Brain OS proof command:

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

Không dùng skip/xfail/ignore hoặc hạ assertion để tạo green giả.

## Các gate trước

Gate 0–7 giữ nguyên invariant đã chứng minh: runtime probe; fail-safe config; rebuildable SQLite; SHA-256 change detection; stable identity; deterministic classifier; bounded taxonomy planning; immutable Markdown originals; idempotent Living Note import; Amplenote directory/ZIP migration với ZIP provenance và fail-closed traversal/symlink/encryption checks.

Các lệnh chính vẫn gồm:

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
```

## Những gì V1 ở Gate 8 vẫn cố ý chưa làm

- chưa Brain Watch automation — Gate 9; **Javis Loop sở hữu scheduler**, Brain OS chỉ deterministic scan/change/job pipeline;
- chưa PDF/DOCX/Sheets import — Gate 10;
- chưa materialize Amplenote images/attachments vào working Library; ZIP provenance vẫn giữ nguyên;
- Brain Manager không tự thực thi Javis INGEST;
- không tự tạo/update Wiki hoặc Memory; Stage 8 chỉ routing/candidate;
- chưa semantic reorganization toàn vault;
- chưa vector DB/embeddings;
- chưa auto-create category/tag;
- chưa realtime filesystem daemon riêng.

Mặc định `System/BrainOS/config.yml` vẫn giữ `dry_run: true`, auto-move và auto-create taxonomy tắt. Gate 8 chỉ ghi derived operational state/candidates vào `.javis`; quyền xử lý tri thức thật vẫn thuộc Javis execution layer.
