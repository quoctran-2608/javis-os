# Brain OS Recovery & Pilot Runbook

Tài liệu này là runbook vận hành cho Brain thật. Nó không tự cấp quyền sửa dữ liệu và không tự bật Brain Watch.

## Nguyên tắc

- Markdown/Living Notes và immutable originals vẫn là dữ liệu người dùng/source of truth.
- `.javis/brain-index.db` là operational index có thể rebuild.
- `.javis/recovery/` chứa **recovery evidence** cho stable identity/lần INGEST thành công gần nhất; không phải user knowledge nhưng phải được giữ cùng backup của Brain.
- `wiki/` là derived knowledge; không ingest ngược vào source.
- Initial pilot luôn giữ `dry_run: true` và `Javis/loops/brain-watch.md` ở `enabled: false`.

## Chuẩn bị runtime

```bash
pip install -r requirements-brain-os.txt
python skills/brain-manager/scripts/brain_os.py doctor
```

## Chuẩn bị recovery trước dữ liệu thật

Preview trước, không ghi user files:

```bash
python skills/brain-manager/scripts/brain_recovery.py audit
python skills/brain-manager/scripts/brain_recovery.py prepare
```

Sau khi đã backup toàn bộ Brain và đọc preview, apply explicit:

```bash
python skills/brain-manager/scripts/brain_recovery.py prepare --apply
```

`prepare --apply` có thể thêm duy nhất `javis_id` vào Markdown cần durable identity. Trước mỗi thay đổi có byte-for-byte backup dưới `.javis/originals/identity-bootstrap/`. Lifecycle INGEST hiện hữu được migrate bằng recovery checkpoint; source vốn stale vẫn stale.

## Điều kiện bắt đầu initial pilot

```bash
python skills/brain-manager/scripts/brain_pilot.py check
```

Chỉ bắt đầu pilot khi trả:

```text
pilot_ready: true
```

Preflight yêu cầu tối thiểu:

- `dry_run: true`;
- SQLite `PRAGMA quick_check=ok`;
- recovery marker + lifecycle checkpoints hợp lệ;
- durable identity không conflict/missing trong managed user knowledge;
- Brain Watch vẫn `enabled: false`;
- không có Brain Watch/recovery lock đang giữ;
- không có AI job ở trạng thái `processing`;
- governed skills canonical/mirror khớp nhau;
- runtime có PyYAML + pypdf.

## Rebuild SQLite

Không xóa DB bằng tay. Preview:

```bash
python skills/brain-manager/scripts/brain_recovery.py rebuild
```

Apply explicit:

```bash
python skills/brain-manager/scripts/brain_recovery.py rebuild --apply
```

Flow:

1. audit durable identity + recovery evidence;
2. từ chối nếu Brain Watch đang chạy;
3. archive DB/WAL/SHM byte-for-byte vào `.javis/recovery/db-archives/`;
4. rebuild full-hash từ filesystem;
5. restore `last_ingested_hash` + `INGESTED/COMPOUNDED/STALE` từ checkpoint;
6. rebuild deterministic classification/taxonomy state;
7. verify filesystem không đổi giữa pre/post snapshot;
8. consume chỉ các synthetic CREATED events do rebuild;
9. chạy post-rebuild audit.

Nếu bất kỳ bước nào fail sau khi archive, tool cố rollback DB từ archive. Không overwrite user Markdown trong rebuild.

## DB hỏng

Nếu DB hỏng **sau khi Brain đã được `prepare --apply`**, `rebuild --apply` có thể recovery từ durable identities + checkpoints và vẫn archive exact corrupt DB trước khi rebuild.

Nếu DB hỏng **trước khi có recovery-ready marker**, tool fail-closed. Không xóa DB, không đoán lifecycle, không tự generate lại identity cho source đã mất DB evidence.

## Sau pilot dry-run

Không tự bật Brain Watch. Chỉ bật sau khi đã review output/audit trên Brain thật và chủ Brain chủ động quyết định. Việc bật automation không phải một phần của recovery/pilot preflight.
