from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from .changes import (
    FileObservation,
    choose_existing_match,
    move_change_kind,
    state_after_content_change,
    state_after_restore,
)
from .config import BrainOSConfig
from .db import BrainIndex, BrainIndexError, utc_now
from .diffing import SnapshotStore, diff_text
from .identity import new_source_id
from .models import BrainFile, ChangeKind, DocumentType, ProcessingState
from .scanner import ScanCollection, collect_files


@dataclass
class ScanReport:
    ok: bool = True
    full_hash: bool = False
    files_seen: int = 0
    created: int = 0
    modified: int = 0
    moved: int = 0
    renamed: int = 0
    deleted: int = 0
    restored: int = 0
    unchanged: int = 0
    identity_collisions: int = 0
    hashed_files: int = 0
    reused_hashes: int = 0
    snapshots_written: int = 0
    diffs_generated: int = 0
    deletions_suppressed: bool = False
    warnings: list[str] = field(default_factory=list)
    traversal_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _conn(index: BrainIndex):
    if index.conn is None:
        raise BrainIndexError("BrainIndex chưa được open().")
    return index.conn


def list_index_files(index: BrainIndex) -> list[BrainFile]:
    conn = _conn(index)
    rows = conn.execute("SELECT source_id FROM files ORDER BY path").fetchall()
    out: list[BrainFile] = []
    for row in rows:
        item = index.get_file(str(row["source_id"]))
        if item is not None:
            out.append(item)
    return out


def list_events(
    index: BrainIndex,
    *,
    limit: int = 50,
    unhandled_only: bool = False,
) -> list[dict[str, Any]]:
    conn = _conn(index)
    where = "WHERE handled_at=''" if unhandled_only else ""
    rows = conn.execute(
        f"""
        SELECT event_id, source_id, event_type, path, old_path,
               old_hash, new_hash, observed_at, handled_at, payload_json
        FROM events
        {where}
        ORDER BY event_id DESC
        LIMIT ?
        """,
        (max(1, min(int(limit), 500)),),
    ).fetchall()
    out = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        out.append(
            {
                "event_id": int(row["event_id"]),
                "source_id": row["source_id"],
                "event_type": row["event_type"],
                "path": row["path"],
                "old_path": row["old_path"],
                "old_hash": row["old_hash"],
                "new_hash": row["new_hash"],
                "observed_at": row["observed_at"],
                "handled_at": row["handled_at"],
                "payload": payload if isinstance(payload, dict) else {},
            }
        )
    return out


def record_event(
    index: BrainIndex,
    *,
    source_id: str,
    kind: ChangeKind,
    path: str,
    old_path: str = "",
    old_hash: str = "",
    new_hash: str = "",
    payload: dict[str, Any] | None = None,
) -> int:
    conn = _conn(index)
    with conn:
        cur = conn.execute(
            """
            INSERT INTO events(
                source_id, event_type, path, old_path, old_hash,
                new_hash, observed_at, payload_json
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                source_id,
                kind.value,
                path,
                old_path,
                old_hash,
                new_hash,
                utc_now(),
                json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
            ),
        )
    return int(cur.lastrowid)


def _document_type_for_zone(config: BrainOSConfig, zone: str) -> DocumentType:
    mapping = {
        str((config.core.get("paths") or {}).get("daily", "")): DocumentType.DAILY,
        str((config.core.get("paths") or {}).get("weekly", "")): DocumentType.WEEKLY,
        str((config.core.get("paths") or {}).get("monthly", "")): DocumentType.MONTHLY,
        str((config.core.get("paths") or {}).get("future", "")): DocumentType.FUTURE,
        str((config.core.get("paths") or {}).get("notes", "")): DocumentType.LIVING_NOTE,
        str((config.core.get("paths") or {}).get("sources", "")): DocumentType.REFERENCE_SOURCE,
        str((config.core.get("paths") or {}).get("wiki", "")): DocumentType.DERIVED_WIKI,
        str((config.core.get("paths") or {}).get("memory", "")): DocumentType.MEMORY,
        str((config.core.get("paths") or {}).get("dashboard", "")): DocumentType.SYSTEM,
    }
    return mapping.get(zone, DocumentType.UNKNOWN)


def _scan_metadata(
    old: BrainFile | None,
    observation: FileObservation,
    *,
    identity_source: str = "",
    identity_collision: str = "",
) -> dict[str, Any]:
    metadata = dict(old.metadata if old else {})
    metadata["scan"] = {
        "zone": observation.zone,
        "fs_device": int(observation.fs_device),
        "fs_inode": int(observation.fs_inode),
    }
    if identity_source:
        metadata["identity_source"] = identity_source
    if identity_collision:
        metadata["identity_collision"] = identity_collision
    else:
        metadata.pop("identity_collision", None)
    return metadata


def _present_file(
    *,
    old: BrainFile | None,
    source_id: str,
    observation: FileObservation,
    config: BrainOSConfig,
    state: ProcessingState,
    identity_source: str,
    identity_collision: str = "",
) -> BrainFile:
    doc_type = (
        old.document_type
        if old is not None and old.document_type != DocumentType.UNKNOWN
        else _document_type_for_zone(config, observation.zone)
    )
    return BrainFile(
        source_id=source_id,
        path=observation.path,
        file_type=observation.fingerprint.suffix,
        document_type=doc_type,
        category_id=old.category_id if old else "",
        state=state,
        origin=old.origin if old else "brain",
        size=observation.fingerprint.size,
        mtime_ns=observation.fingerprint.mtime_ns,
        content_hash=observation.sha256,
        last_seen_hash=observation.sha256,
        last_ingested_hash=old.last_ingested_hash if old else "",
        created_at=old.created_at if old else "",
        updated_at=old.updated_at if old else "",
        last_indexed_at=old.last_indexed_at if old else "",
        last_ingested_at=old.last_ingested_at if old else "",
        deleted_at="",
        metadata=_scan_metadata(
            old,
            observation,
            identity_source=identity_source,
            identity_collision=identity_collision,
        ),
    )


def _snapshot_options(config: BrainOSConfig) -> SnapshotStore:
    raw = config.core.get("scan") or {}
    max_bytes = int(raw.get("max_snapshot_bytes", 2 * 1024 * 1024) or 0)
    return SnapshotStore(config.path("state"), max_bytes=max_bytes)


def _diff_payload(
    snapshots: SnapshotStore,
    source_id: str,
    absolute_path: Path,
) -> tuple[dict[str, Any], str | None]:
    old_text = snapshots.read(source_id)
    new_text = snapshots.read_current_file(absolute_path)
    payload: dict[str, Any] = {}
    if old_text is not None and new_text is not None and old_text != new_text:
        payload["diff"] = diff_text(old_text, new_text).to_dict()
    return payload, new_text


def _capture_snapshot(
    snapshots: SnapshotStore,
    source_id: str,
    absolute_path: Path,
    *,
    known_text: str | None = None,
) -> bool:
    text = known_text if known_text is not None else snapshots.read_current_file(absolute_path)
    if text is None:
        return False
    snapshots.write(source_id, text)
    return True


def reconcile_brain(
    config: BrainOSConfig,
    *,
    full_hash: bool = False,
) -> ScanReport:
    """Reconcile filesystem state into the rebuildable Brain OS index.

    This function only writes `.javis` state (SQLite + snapshots). It never
    changes, moves, renames or annotates user notes.
    """

    report = ScanReport(full_hash=full_hash)
    snapshots = _snapshot_options(config)

    with BrainIndex(config.db_path) as index:
        existing = list_index_files(index)
        existing_by_path = {item.path: item for item in existing}
        existing_by_id = {item.source_id: item for item in existing}

        collection: ScanCollection = collect_files(
            config,
            existing_by_path=existing_by_path,
            full_hash=full_hash,
        )
        report.files_seen = len(collection.observations)
        report.hashed_files = collection.hashed_files
        report.reused_hashes = collection.reused_hashes
        report.warnings.extend(collection.warnings)
        report.traversal_errors.extend(collection.traversal_errors)

        observations_by_path = {obs.path: obs for obs in collection.observations}
        seen_ids: set[str] = set()
        consumed_old_ids: set[str] = set()

        # Pass 1: same path. This is the cheapest and least ambiguous identity.
        pending_new: list[FileObservation] = []
        for obs in collection.observations:
            old = existing_by_path.get(obs.path)
            if old is None:
                pending_new.append(obs)
                continue

            if obs.javis_id and obs.javis_id != old.source_id:
                report.warnings.append(
                    f"{obs.path}: javis_id={obs.javis_id!r} khác DB source_id={old.source_id!r}; "
                    "Stage 3 giữ identity trong DB và không sửa note."
                )

            if old.state == ProcessingState.MISSING:
                state = state_after_restore(old, obs.sha256)
                payload, new_text = _diff_payload(
                    snapshots, old.source_id, config.brain_root / obs.path
                )
                payload["restored"] = True
                item = _present_file(
                    old=old,
                    source_id=old.source_id,
                    observation=obs,
                    config=config,
                    state=state,
                    identity_source="database",
                )
                index.upsert_file(item)
                record_event(
                    index,
                    source_id=old.source_id,
                    kind=ChangeKind.CREATED,
                    path=obs.path,
                    old_path=old.path,
                    old_hash=old.content_hash,
                    new_hash=obs.sha256,
                    payload=payload,
                )
                report.restored += 1
                if "diff" in payload:
                    report.diffs_generated += 1
                if _capture_snapshot(
                    snapshots,
                    old.source_id,
                    config.brain_root / obs.path,
                    known_text=new_text,
                ):
                    report.snapshots_written += 1
            elif old.content_hash != obs.sha256:
                payload, new_text = _diff_payload(
                    snapshots, old.source_id, config.brain_root / obs.path
                )
                state = state_after_content_change(old, obs.sha256)
                item = _present_file(
                    old=old,
                    source_id=old.source_id,
                    observation=obs,
                    config=config,
                    state=state,
                    identity_source="database",
                )
                index.upsert_file(item)
                record_event(
                    index,
                    source_id=old.source_id,
                    kind=ChangeKind.MODIFIED,
                    path=obs.path,
                    old_hash=old.content_hash,
                    new_hash=obs.sha256,
                    payload=payload,
                )
                report.modified += 1
                if "diff" in payload:
                    report.diffs_generated += 1
                if _capture_snapshot(
                    snapshots,
                    old.source_id,
                    config.brain_root / obs.path,
                    known_text=new_text,
                ):
                    report.snapshots_written += 1
            else:
                scan_meta = (old.metadata or {}).get("scan") or {}
                fs_changed = (
                    int(scan_meta.get("fs_device", 0) or 0) != obs.fs_device
                    or int(scan_meta.get("fs_inode", 0) or 0) != obs.fs_inode
                )
                stat_changed = (
                    int(old.size) != obs.fingerprint.size
                    or int(old.mtime_ns) != obs.fingerprint.mtime_ns
                )
                if fs_changed or stat_changed or old.deleted_at:
                    item = _present_file(
                        old=old,
                        source_id=old.source_id,
                        observation=obs,
                        config=config,
                        state=old.state,
                        identity_source="database",
                    )
                    index.upsert_file(item)
                report.unchanged += 1
                if snapshots.read(old.source_id) is None:
                    if _capture_snapshot(
                        snapshots, old.source_id, config.brain_root / obs.path
                    ):
                        report.snapshots_written += 1

            seen_ids.add(old.source_id)
            consumed_old_ids.add(old.source_id)

        # Pass 2: new paths. Try to prove rename/move before generating a new DB identity.
        for obs in pending_new:
            candidates = [
                item
                for item in existing
                if item.source_id not in consumed_old_ids
                and item.path not in observations_by_path
            ]
            match = choose_existing_match(obs, candidates)
            old = match.file

            if old is not None:
                kind = move_change_kind(old.path, obs.path)
                changed = old.content_hash != obs.sha256
                payload: dict[str, Any] = {"match_method": match.method}
                new_text: str | None = None
                if changed:
                    diff_payload, new_text = _diff_payload(
                        snapshots, old.source_id, config.brain_root / obs.path
                    )
                    payload.update(diff_payload)
                    state = state_after_content_change(old, obs.sha256)
                elif old.state == ProcessingState.MISSING:
                    state = state_after_restore(old, obs.sha256)
                    payload["restored"] = True
                else:
                    state = old.state

                item = _present_file(
                    old=old,
                    source_id=old.source_id,
                    observation=obs,
                    config=config,
                    state=state,
                    identity_source=match.method,
                )
                index.upsert_file(item)
                record_event(
                    index,
                    source_id=old.source_id,
                    kind=kind,
                    path=obs.path,
                    old_path=old.path,
                    old_hash=old.content_hash,
                    new_hash=obs.sha256,
                    payload=payload,
                )
                if kind == ChangeKind.RENAMED:
                    report.renamed += 1
                else:
                    report.moved += 1
                if "diff" in payload:
                    report.diffs_generated += 1
                if _capture_snapshot(
                    snapshots,
                    old.source_id,
                    config.brain_root / obs.path,
                    known_text=new_text,
                ):
                    report.snapshots_written += 1

                seen_ids.add(old.source_id)
                consumed_old_ids.add(old.source_id)
                continue

            collision = ""
            if obs.javis_id and obs.javis_id in existing_by_id:
                collision = (
                    f"frontmatter javis_id {obs.javis_id!r} đang thuộc "
                    f"{existing_by_id[obs.javis_id].path!r}"
                )
                report.identity_collisions += 1
                report.warnings.append(f"{obs.path}: {collision}")

            if obs.javis_id and obs.javis_id not in existing_by_id and obs.javis_id not in seen_ids:
                source_id = obs.javis_id
                identity_source = "frontmatter"
            else:
                source_id = new_source_id(_document_type_for_zone(config, obs.zone))
                identity_source = "generated_db_only"

            item = _present_file(
                old=None,
                source_id=source_id,
                observation=obs,
                config=config,
                state=ProcessingState.DISCOVERED,
                identity_source=identity_source,
                identity_collision=collision,
            )
            index.upsert_file(item)
            record_event(
                index,
                source_id=source_id,
                kind=ChangeKind.CREATED,
                path=obs.path,
                new_hash=obs.sha256,
                payload={
                    "identity_source": identity_source,
                    "identity_collision": collision,
                    "zone": obs.zone,
                },
            )
            report.created += 1
            if _capture_snapshot(snapshots, source_id, config.brain_root / obs.path):
                report.snapshots_written += 1
            seen_ids.add(source_id)

        # Pass 3: rows no longer observed. Fail closed on incomplete traversal.
        if collection.deletion_safe:
            uncertain = collection.uncertain_paths
            for old in existing:
                if old.source_id in consumed_old_ids or old.source_id in seen_ids:
                    continue
                if old.path in observations_by_path or old.path in uncertain:
                    continue
                if old.state == ProcessingState.MISSING:
                    continue

                missing = replace(
                    old,
                    state=ProcessingState.MISSING,
                    deleted_at=utc_now(),
                    updated_at="",
                )
                index.upsert_file(missing)
                record_event(
                    index,
                    source_id=old.source_id,
                    kind=ChangeKind.DELETED,
                    path=old.path,
                    old_path=old.path,
                    old_hash=old.content_hash,
                    payload={"policy": "mark_missing"},
                )
                report.deleted += 1
        else:
            report.deletions_suppressed = True
            report.warnings.append(
                "Có lỗi khi duyệt thư mục; Stage 3 không đánh dấu file nào là deleted trong vòng này."
            )

        index.set_meta("brain_root", str(config.brain_root))
        index.set_meta("last_scan_at", utc_now())
        if full_hash:
            index.set_meta("last_full_reconcile_at", utc_now())
        index.set_meta("last_scan_report", report.to_dict())

    report.ok = not report.traversal_errors
    return report
