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
| Portable distribution | PASS | deterministic ZIP is copied into a Brain created by Javis, extracted to hidden `.brain-os-installer/`, checksum-verified, previewed, applied and verified on Ubuntu + Windows; scanner ignores installer; runtime caches are excluded |
| Observability | PASS | `brain_recovery.py audit` + `brain_pilot.py check` expose DB, identity, lifecycle, locks, jobs, compatibility and blockers |
| Real-vault rollout tooling | PILOT-READY | cross-platform drop-in + portable-package proofs passed; initial pilot still requires per-vault backup, installer preview/apply, dry-run + Brain Watch disabled + recovery prepared |

## Release-hardening proof

Release CI contains a dedicated `release-hardening` matrix on both `ubuntu-latest` and `windows-latest`. Each runner starts from a clean checkout, installs the root runtime dependencies from scratch, verifies Brain OS runtime imports, then exercises the public installer and active-Brain integration path rather than copying the template directly.

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

The portable-package E2E additionally models the intended fresh-install user workflow instead of an empty-folder approximation:

1. Javis creates/scaffolds a new Brain using the same `_ensure_brain_scaffold` path as the Brain creation API.
2. The package is built outside that Brain.
3. Only `BrainOS-V1-Portable.zip` is copied into the new Brain.
4. The ZIP is extracted there to `.brain-os-installer/`.
5. The installer is launched from the extracted package without a Brain path or Javis-root argument; normal `<Javis>/brains/<Brain>` runtime discovery must succeed.
6. Preview must report compatible runtime, valid package integrity and zero conflicts before Brain OS-owned files exist in the target.
7. Apply must use the Javis runtime Python (preferring `.venv`), run `system_sync`, install only missing Brain OS-owned files, preserve pre-existing Javis/user content and run the read-only Brain OS `doctor` smoke check.
8. While ZIP/package remain in the Brain, the real Brain OS scanner must prune `.brain-os-installer/` completely so package Markdown can never become user knowledge.
9. `--verify` must re-check package checksums, installed file/system-skill contract and run read-only `doctor` successfully.
10. A deliberately tampered payload must fail integrity validation before Brain OS target files are written.

The CI then builds the deterministic ZIP only after the main suite and both release-hardening runners are green. The archive embeds the source commit in `manifest.json`/`RELEASE.md`, ships SHA-256 checksums and excludes derived/user/runtime material including `.javis/**`, `.claude/**`, Notes/sources/wiki, Javis-owned system-skill mirrors and Python/tool caches such as `__pycache__`, `.pyc/.pyo`, pytest/mypy/ruff caches.

This CI proof establishes the packaged/runtime baseline. A real user vault is still a separate rollout event and must keep the backup + preview + pilot posture below; passing release CI is not permission to skip per-vault safety checks.

## Javis runtime integration contract

Brain OS **không** giả định Claude/Javis chạy với cwd bằng thư mục Brain. Javis giữ cwd ở project root để bảo toàn system skill/plugin discovery. Vì vậy mọi thao tác Brain OS từ agent phải đi qua bundled tool `javis_brain_os`, tool này lấy active Brain từ `ctx.vault_root` rồi resolve script bằng absolute path trong Brain.

Hệ quả:

- không chạy `python skills/brain-manager/scripts/...` từ agent trong Brain OS-managed mode;
- không đoán `/brains/<name>` từ cwd;
- không đổi global Javis cwd sang Brain;
- Brain không có `System/BrainOS/config.yml` thì bridge Brain OS fail-closed;
- runtime Javis phải có bundled plugin `system/plugins/brain-os` và dependency Brain OS cần thiết, gồm `pypdf` trong root `requirements.txt`.

## Cài portable vào Brain mới — release workflow ưu tiên

Đây là workflow dùng cho lần test sạch/real-vault rollout tiếp theo.

1. Tạo Brain mới bằng Javis trước, ví dụ `MinhSecondBrain`; không tạo một thư mục rỗng thủ công để thay cho Javis scaffold.
2. Lấy đúng artifact `BrainOS-V1-Portable.zip` được build từ final green HEAD.
3. Copy **chỉ ZIP đó** vào root của Brain mới rồi giải nén tại chỗ. ZIP tạo thư mục ẩn `.brain-os-installer/`.
4. Chạy preview trước:

```bash
python .brain-os-installer/install.py
```

Chỉ tiếp tục nếu output có `ok: true`, `runtime.compatible: true`, `package_integrity.ok: true` và `plan.conflicts: []`.

5. Apply:

```bash
python .brain-os-installer/install.py --apply
```

6. Verify read-only:

```bash
python .brain-os-installer/install.py --verify
```

`--verify` kiểm installed contract và chạy Brain OS `doctor` bằng Python runtime của Javis; `doctor` là read-only và không khởi tạo DB nếu DB chưa tồn tại.

7. Sau khi verify PASS, có thể xoá `BrainOS-V1-Portable.zip` và `.brain-os-installer/`. Brain OS đã nằm ở các path do nó quản lý trong Brain.

Với Brain nằm ngoài `<Javis>/brains/`, truyền `--javis-root <path-Javis>` hoặc đặt `JAVIS_ROOT`. Normal local Brain dưới `<Javis>/brains/<name>` không cần tham số này.

Package/installer không copy `.javis` state, không copy Notes/sources/wiki, không copy `.claude` mirrors, không copy runtime cache/bytecode và không overwrite file khác nội dung ở path Brain OS quản lý.

## Cài trực tiếp từ Javis repository — operator/dev workflow

Không copy nguyên thư mục `brain-os-v1/` vào Brain và không `cp -r` mù `brain-os/template/` lên dữ liệu hiện hữu.

Từ **Javis repository root**, operator/dev vẫn có thể dùng installer nguồn:

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
