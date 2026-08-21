# Brain OS V1 — Hardening Status

Brain OS V1 đã đóng Gate 0–10. Giai đoạn hardening không mở thêm feature gate; mục tiêu là làm các lớp hiện hữu recoverable, bounded, đủ quan sát và tích hợp đúng với runtime Javis trước real-vault rollout.

## Trạng thái

| Workstream | Status | Evidence |
|---|---|---|
| Recovery — stable identity | PASS | DB-only identity materialization, immutable pre-change backup, conflict fail-closed, lifecycle-preserving migration |
| Recovery — INGEST lifecycle | PASS | checksum-verified checkpoint ngoài SQLite cho lần INGEST gần nhất; current/stale/compounded restore |
| Recovery — SQLite rebuild | PASS | recovery-ready marker, exact DB archive, corrupt-DB rebuild, rollback path, synthetic-event suppression |
| Performance — large vault | PASS | 10k-note scanner proof: sparse scan reuses all unchanged hashes; one edit rehashes one file |
| Safety | PASS within V1 scope | symlink/path traversal, malformed metadata, identity conflict, ZIP/document bounds, provenance/recovery tamper fail-closed |
| Upgrade resilience | PASS for packaged extension | canonical + `.claude` governed skills must match; runtime scripts/contract/dependencies checked by pilot preflight |
| Javis runtime integration | PASS | clean drop-in installer + `system_sync` + active-Brain bridge + governed ingest E2E pass on fresh Ubuntu and Windows runners; runtime dependency imports and document regressions also pass |
| Observability | PASS | `brain_recovery.py audit` + `brain_pilot.py check` expose DB, identity, lifecycle, locks, jobs, compatibility and blockers |
| Real-vault rollout tooling | PILOT-READY | cross-platform drop-in proof passed; initial pilot still requires per-vault backup, installer preview/apply, dry-run + Brain Watch disabled + recovery prepared |

## Release-hardening proof

Release CI now contains a dedicated `release-hardening` matrix on both `ubuntu-latest` and `windows-latest`. Each runner starts from a clean checkout, installs the root runtime dependencies from scratch, verifies Brain OS runtime imports, then exercises the public installer and active-Brain integration path rather than copying the template directly.

The release E2E covers:

- installer preview with zero writes and zero conflicts;
- installer apply while preserving unrelated existing Brain content;
- Javis `system_sync` materializing governed system skills;
- `javis_brain_os` resolving the active Brain from `ctx.vault_root` while Javis cwd remains the repository root;
- scan/index creation;
- Living Note import through the bridge;
- Brain-relative snapshot/working paths;
- immutable snapshot bytes;
- rename/re-import retaining the same stable `javis_id` and the same provenance snapshot;
- no duplicate import manifest;
- INGEST lifecycle checkpoint to `compounded` without rewriting user knowledge;
- final scan after lifecycle update;
- cross-platform Stage 10 document regression;
- actual runtime imports for `pypdf` and `yaml` after a clean dependency install.

This CI proof establishes the packaged/runtime baseline. A real user vault is still a separate rollout event and must keep the backup + preview + pilot posture below; passing release CI is not permission to skip per-vault safety checks.

## Javis runtime integration contract

Brain OS **không** giả định Claude/Javis chạy với cwd bằng thư mục Brain. Javis giữ cwd ở project root để bảo toàn system skill/plugin discovery. Vì vậy mọi thao tác Brain OS từ agent phải đi qua bundled tool `javis_brain_os`, tool này lấy active Brain từ `ctx.vault_root` rồi resolve script bằng absolute path trong Brain.

Hệ quả:

- không chạy `python skills/brain-manager/scripts/...` từ agent trong Brain OS-managed mode;
- không đoán `/brains/<name>` từ cwd;
- không đổi global Javis cwd sang Brain;
- Brain không có `System/BrainOS/config.yml` thì bridge Brain OS fail-closed;
- runtime Javis phải có bundled plugin `system/plugins/brain-os` và dependency Brain OS cần thiết, gồm `pypdf` trong root `requirements.txt`.

## Cài vào một Brain Javis hiện hữu

Không copy nguyên thư mục `brain-os-v1/` vào Brain và không `cp -r` mù `brain-os/template/` lên dữ liệu hiện hữu.

Từ **Javis repository root**, dùng installer:

```bash
# Preview — không ghi target Brain
python brain-os/install_brain_os.py "/brains/MyBrain"

# Chỉ apply khi preview báo runtime compatible và conflicts = 0
python brain-os/install_brain_os.py "/brains/MyBrain" --apply
```

Installer chỉ materialize các file Brain OS-owned cần thiết, bỏ qua system-skill mirrors do Javis `system_sync` sở hữu, giữ nguyên file không liên quan và từ chối overwrite nếu target đã có nội dung khác ở path do Brain OS quản lý.

Layout sau cài là overlay ở **Brain root**, ví dụ:

```text
/brains/MyBrain/
├── System/BrainOS/...
├── System/Taxonomy/...
├── skills/brain-manager/...
└── Javis/loops/...
```

Không phải:

```text
/brains/MyBrain/brain-os-v1/template/...
```

## Recovery contract

SQLite remains operational/derived. A safe rebuild is allowed only when the information SQLite cannot safely invent has durable recovery evidence:

- `javis_id` for managed user knowledge lives in Markdown frontmatter;
- immutable import/document originals remain under `.javis/originals/` and `Library/`;
- completed INGEST evidence is checkpointed under `.javis/recovery/lifecycle/`;
- `.javis/recovery/ready.json` proves a healthy DB was reconciled/backfilled at least once under the current recovery contract.

Recovery evidence is not user knowledge and does not replace Markdown as source of truth. It exists to prevent a lost/corrupt operational DB from causing identity forks or blind re-ingest.

## Operational commands

Các lệnh dưới đây là thao tác vận hành trực tiếp khi operator đã ở đúng Brain root hoặc truyền đúng `--brain-root`; agent Javis không dùng chúng để thay bridge.

```bash
# Read-only diagnostics
python skills/brain-manager/scripts/brain_os.py doctor
python skills/brain-manager/scripts/brain_recovery.py audit
python skills/brain-manager/scripts/brain_pilot.py check

# Explicit maintenance preparation
python skills/brain-manager/scripts/brain_recovery.py prepare
python skills/brain-manager/scripts/brain_recovery.py prepare --apply

# Controlled DB rebuild
python skills/brain-manager/scripts/brain_recovery.py rebuild
python skills/brain-manager/scripts/brain_recovery.py rebuild --apply
```

## Pilot invariant

`brain_pilot.py check` chỉ trả `pilot_ready: true` khi initial pilot vẫn ở fail-safe posture: `dry_run: true`, Brain Watch tắt, DB/recovery/skills/runtime hợp lệ và không có AI job đang processing.

Nó **không** bật automation, không gọi model, không chạy INGEST và không ghi Wiki/Memory.

## V1 vẫn cố ý không làm

- filesystem daemon thứ hai;
- vector DB/embeddings bắt buộc;
- semantic reorganization toàn vault;
- auto-create folder/tag taxonomy;
- auto-Wiki/Memory không có explicit governed path;
- OCR tự động cho PDF scan/image-only;
- tự bật Brain Watch sau pilot.

Các mục trên là feature expansion, không phải blocker của Brain OS V1 pilot-ready hardening.
