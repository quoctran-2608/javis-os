# Brain OS V1

Brain OS là **governance + lifecycle layer** cho kho Markdown/Obsidian dùng cùng Javis OS. Brain OS không xây một Second Brain cạnh tranh với Javis và không sửa core `server/` hoặc `dashboard/`.

Ranh giới sở hữu:

- **Brain OS:** Living Notes, stable identity, deterministic change detection, incremental diff, taxonomy, import/provenance/originals, ingestion policy & routing, document normalization.
- **Javis OS:** AI Engine, INGEST execution, Wiki, Memory, Knowledge Graph, Skills và Loops/Scheduler.

Nguyên tắc V1: **boring, deterministic, recoverable và đáng tin trước; thông minh sau**.

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
Gate 10 — PDF/DOCX/Sheets Import        PASS
```

## Kiến trúc tổng thể sau Gate 10

```text
Markdown / Amplenote / PDF / DOCX / XLSX / CSV / TSV
                       │
                       ▼
              Brain OS governance
     ┌─────────────────┼──────────────────┐
     │                 │                  │
stable identity   provenance/originals   taxonomy
     │                 │                  │
     └──────────────┬──┴──────────────────┘
                    ▼
         normalized Markdown source
                    │
                    ▼
       deterministic scan / classify
          / taxonomy / AI routing
                    │
                    ▼
             Javis execution
       INGEST / Wiki / Memory / Graph
```

Brain OS không tự thực thi Javis INGEST, không tự materialize Wiki/Memory và không tạo scheduler thứ hai.

## Gate 9 — Brain Watch

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

Các invariant chính:

- `brain-watch.md` là Javis Loop, `enabled: false` mặc định, cadence 5 phút, `notify: false`;
- Python Watch không gọi LLM và không tự schedule;
- backlog AI vẫn được drain khi không có file change mới;
- current-hash job đã completed không gây starvation;
- claim `processing` trước handoff để tránh duplicate AI work;
- stale hash/policy fail trước handoff;
- retry model/tool bounded ở 3;
- `.javis/brain-watch.lock` ngăn overlapping cycle;
- không move/rename note, không rewrite frontmatter, không chạy INGEST/Wiki/Memory.

Gate 9 implementation: `9e7b5d3bcecb506dc5e7c9664198c6c134ae1647`.
Gate 9 proof: run `#62` / `32135376026`, rerun attempt 2 PASS canonical Gate 0–9. Final Gate 9 HEAD `f31286884769013c20eabbe4876cd75ebabceb90` có normal CI #64 và #66 PASS.

## Gate 10 — Document normalization / import

Stage 10 thêm deterministic adapters cho:

```text
.pdf
.docx
.xlsx
.csv
.tsv
```

Pipeline:

```text
external document
      │
      ├─ SHA-256 + stable source id
      │
      ├─ immutable binary original
      │      Library/Documents/<source_id>/original.<format>
      │
      ├─ document provenance manifest
      │      .javis/originals/documents/<source_id>/manifest.json
      │
      └─ deterministic extraction
              ↓
        normalized Markdown
              ↓
        Gate 6 import_markdown()
              ↓
        immutable Markdown snapshot
              +
        editable source under sources/
```

Bổ sung:

```text
brain-os/
├── requirements-documents.txt
└── tests/
    └── test_stage10_documents.py

skills/brain-manager/scripts/
├── import_document.py
└── brain_os_lib/
    ├── documents.py
    └── extractors/
        ├── __init__.py
        ├── pdf.py
        ├── docx.py
        └── sheets.py
```

### Invariant Gate 10

- binary original được giữ **byte-for-byte** và SHA-256 verify trước mọi reuse;
- stable identity gắn với exact binary bytes + format; rename source bên ngoài không fork identity;
- normalized Markdown đi qua **chính Gate 6 importer**, không tạo import engine song song;
- exact re-import reuse binary provenance + normalized provenance + working source và không overwrite user edits;
- tampered/missing Library original hoặc normalized immutable snapshot fail-closed;
- category chỉ được chọn từ existing `knowledge` taxonomy; không auto-create;
- ZIP-based DOCX/XLSX reject traversal, absolute/drive path, duplicate normalized path, symlink, encrypted entry và oversized archive;
- extraction có giới hạn file/page/row/column/normalized-size từ `System/BrainOS/config.yml`;
- Stage 10 không gọi AI, không chạy Javis INGEST, không ghi Wiki/Memory, không move/rewrite note hiện hữu;
- `.xls` legacy và format ngoài allowlist bị từ chối thay vì đoán parser.

### PDF

PDF dùng `pypdf` lazy dependency từ:

```bash
pip install -r brain-os/requirements-documents.txt
```

V1 chỉ extract **text layer**. PDF scan/image-only hoặc không có text extractable sẽ fail-closed; Stage 10 không tự OCR. OCR/vision nên đi qua capability Javis/document vision riêng rồi trả normalized source có provenance.

### DOCX

DOCX dùng stdlib ZIP/XML parser:

- paragraph text;
- table → Markdown table;
- core metadata như title/creator khi có;
- không OCR embedded images ở V1.

### XLSX / CSV / TSV

XLSX dùng stdlib ZIP/XML parser:

- sheet names;
- shared strings;
- cells;
- formulas giữ dạng `=<formula> [cached: <value>]` khi có cached value;
- date serial giữ raw value ở V1, không tự đoán formatting/date semantics.

CSV/TSV yêu cầu UTF-8 hoặc UTF-8-SIG và dùng row/column bounds giống spreadsheet policy.

### CLI

```bash
# Preview mặc định — không ghi Brain
python skills/brain-manager/scripts/import_document.py "/path/file.pdf"

# Apply explicit
python skills/brain-manager/scripts/import_document.py "/path/file.docx" --apply

# Chỉ dùng category đã tồn tại
python skills/brain-manager/scripts/import_document.py "/path/book.xlsx" \
  --category knowledge_ai \
  --apply
```

CLI report luôn chỉ rõ Stage 10:

```text
uses_ai: false
executes_javis_ingest: false
writes_wiki: false
writes_memory: false
moves_user_files: false
mutates_existing_user_notes: false
```

## Gate 10 proof

Implementation commit:

```text
8f4db71fffc781ec418cd7415cf999281b62d2b2
feat: add Stage 10 document normalization
```

Temporary proof commit:

```text
e9abd59fbe145f8025592e5db4f2ed33f724db28
test: add temporary Gate 10 CI proof
```

Sau đó `config.yml` được đưa về đúng cấu trúc/comments cũ và chỉ giữ block Stage 10 ở commit:

```text
7bc22d48ef56a8c828ee79db55113b2768088855
chore: keep Stage 10 config diff minimal
```

GitHub Actions:

- run `#68` / `32141067885`: normal Javis suite PASS + canonical Brain OS Gate 0–10 PASS;
- run `#69` / `32141318739`: proof lại sau config cleanup, normal Javis suite PASS + canonical Brain OS Gate 0–10 PASS.

Temporary proof bridge được gỡ ngay sau proof và `.github/workflows/ci.yml` restore byte-identical về baseline blob:

```text
0d17557a60f0cd27a04f7a381886f55d19196649
```

Canonical proof command:

```bash
pip install -r brain-os/requirements-documents.txt
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
  brain-os/tests/test_stage9_brain_watch.py \
  brain-os/tests/test_stage10_documents.py
```

Không dùng skip/xfail/ignore hoặc hạ assertion để tạo green giả.

## Các gate trước

Gate 0–8 giữ nguyên invariant đã chứng minh: runtime probe; fail-safe config; rebuildable SQLite; SHA-256 change detection; stable identity; deterministic classifier; bounded taxonomy planning; immutable Markdown originals; idempotent Living Note import; Amplenote directory/ZIP migration; AI Brain Manager strict schema/policy routing nhưng Javis vẫn sở hữu model execution/INGEST/Wiki/Memory.

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
python skills/brain-manager/scripts/import_document.py "/path/file.pdf" --apply
```

## Sau Gate 10: Hardening

Không mở thêm feature gate trước khi hardening các lớp đã có. Trọng tâm tiếp theo:

- **Recovery:** DB rebuild, interrupted import/write, orphan snapshot, stale jobs;
- **Performance:** vault lớn, 10k+ notes, large Living Notes, scan latency, large documents;
- **Safety:** path traversal, symlink, malformed YAML/frontmatter, binary masquerading, ZIP bombs, provenance tamper;
- **Upgrade resilience:** Javis update, Skill/Loop compatibility, extension-only invariant;
- **Observability:** status/doctor/logs/candidate review/failure reason;
- **Real-vault rollout:** dry-run trước, fixture/backup trước apply, không tự bật Brain Watch.

V1 vẫn cố ý chưa làm realtime filesystem daemon riêng, vector DB/embeddings, semantic reorganization toàn vault, auto-create taxonomy, auto-Wiki/Memory, hoặc OCR tự động trong Stage 10.
