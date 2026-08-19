# Brain OS V1 — Hardening Status

Brain OS V1 đã đóng Gate 0–10. Giai đoạn hardening không mở thêm feature gate; mục tiêu là làm các lớp hiện hữu recoverable, bounded và đủ quan sát trước real-vault rollout.

## Trạng thái

| Workstream | Status | Evidence |
|---|---|---|
| Recovery — stable identity | PASS | DB-only identity materialization, immutable pre-change backup, conflict fail-closed, lifecycle-preserving migration |
| Recovery — INGEST lifecycle | PASS | checksum-verified checkpoint ngoài SQLite cho lần INGEST gần nhất; current/stale/compounded restore |
| Recovery — SQLite rebuild | PASS | recovery-ready marker, exact DB archive, corrupt-DB rebuild, rollback path, synthetic-event suppression |
| Performance — large vault | PASS | 10k-note scanner proof: sparse scan reuses all unchanged hashes; one edit rehashes one file |
| Safety | PASS within V1 scope | symlink/path traversal, malformed metadata, identity conflict, ZIP/document bounds, provenance/recovery tamper fail-closed |
| Upgrade resilience | PASS for packaged extension | canonical + `.claude` governed skills must match; runtime scripts/contract/dependencies checked by pilot preflight |
| Observability | PASS | `brain_recovery.py audit` + `brain_pilot.py check` expose DB, identity, lifecycle, locks, jobs, compatibility and blockers |
| Real-vault rollout tooling | READY | initial pilot requires dry-run + Brain Watch disabled + recovery prepared; actual user vault still needs its own backup/pilot run |

## Recovery contract

SQLite remains operational/derived. A safe rebuild is allowed only when the information SQLite cannot safely invent has durable recovery evidence:

- `javis_id` for managed user knowledge lives in Markdown frontmatter;
- immutable import/document originals remain under `.javis/originals/` and `Library/`;
- completed INGEST evidence is checkpointed under `.javis/recovery/lifecycle/`;
- `.javis/recovery/ready.json` proves a healthy DB was reconciled/backfilled at least once under the current recovery contract.

Recovery evidence is not user knowledge and does not replace Markdown as source of truth. It exists to prevent a lost/corrupt operational DB from causing identity forks or blind re-ingest.

## Operational commands

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
