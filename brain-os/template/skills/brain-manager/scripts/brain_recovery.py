#!/usr/bin/env python3
"""Brain OS controlled recovery preparation, audit and safe SQLite rebuild.

The SQLite index is derived, but a safe rebuild still needs durable identity and
completed-INGEST lifecycle checkpoints. This tool establishes and verifies those
preconditions, archives the old DB byte-for-byte, rebuilds deterministic state,
restores lifecycle, and suppresses only recovery-synthetic filesystem events.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from brain_identity import apply_materialization, plan_materialization
from brain_os import resolve_root
from brain_os_lib.classifier import classify_brain
from brain_os_lib.config import BrainOSConfig, BrainOSConfigError
from brain_os_lib.db import BrainIndex, BrainIndexError, SCHEMA_VERSION, utc_now
from brain_os_lib.frontmatter import FrontmatterError, load_markdown
from brain_os_lib.reconcile import reconcile_brain
from brain_os_lib.recovery import (
    BrainRecoveryError,
    audit_lifecycle_against_db,
    backfill_lifecycle_checkpoints,
    load_lifecycle_checkpoints,
    recovery_root,
    restore_lifecycle_checkpoints,
)
from brain_os_lib.scanner import collect_files
from brain_os_lib.taxonomy import TaxonomyError, plan_brain_taxonomy


READY_SCHEMA_VERSION = 1
READY_CONTRACT = "brain-os-recovery-ready-v1"


class ControlledRecoveryError(RuntimeError):
    pass


def _emit(data: dict[str, Any], *, compact: bool) -> None:
    if compact:
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checksum(payload: dict[str, Any]) -> str:
    clean = {key: value for key, value in payload.items() if key != "checksum"}
    encoded = json.dumps(
        clean,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.brain-os-",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(path, encoded)


def _ready_path(config: BrainOSConfig) -> Path:
    return recovery_root(config) / "ready.json"


def _write_ready_marker(
    config: BrainOSConfig,
    *,
    observed_files: int,
    durable_required_files: int,
    checkpoint_count: int,
    last_rebuild_archive: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": READY_SCHEMA_VERSION,
        "contract": READY_CONTRACT,
        "prepared_at": _utc_now(),
        "db_schema_version": SCHEMA_VERSION,
        "observed_files": int(observed_files),
        "durable_required_files": int(durable_required_files),
        "lifecycle_checkpoint_count": int(checkpoint_count),
        "last_rebuild_archive": str(last_rebuild_archive or ""),
    }
    payload["checksum"] = _checksum(payload)
    _atomic_write_json(_ready_path(config), payload)
    return _read_ready_marker(config) or payload


def _read_ready_marker(config: BrainOSConfig) -> dict[str, Any] | None:
    path = _ready_path(config)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ControlledRecoveryError(f"Recovery ready marker hỏng: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ControlledRecoveryError(f"Recovery ready marker phải là object: {path}")
    if int(payload.get("schema_version", 0) or 0) != READY_SCHEMA_VERSION:
        raise ControlledRecoveryError(f"Recovery ready marker schema không hỗ trợ: {path}")
    if str(payload.get("contract") or "") != READY_CONTRACT:
        raise ControlledRecoveryError(f"Recovery ready marker contract không hợp lệ: {path}")
    expected = str(payload.get("checksum") or "").strip().lower()
    actual = _checksum(payload)
    if expected != actual:
        raise ControlledRecoveryError(
            f"Recovery ready marker checksum mismatch: expected={expected} actual={actual}"
        )
    return payload


def _db_health(config: BrainOSConfig) -> dict[str, Any]:
    path = config.db_path
    report: dict[str, Any] = {
        "exists": path.is_file(),
        "readable": False,
        "integrity_ok": False,
        "quick_check": "missing" if not path.is_file() else "",
        "schema_version": 0,
        "error": "",
    }
    if not path.is_file():
        return report
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        row = conn.execute("PRAGMA quick_check").fetchone()
        quick = str(row[0]) if row else ""
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        report.update(
            {
                "readable": True,
                "integrity_ok": quick == "ok" and version == SCHEMA_VERSION,
                "quick_check": quick,
                "schema_version": version,
            }
        )
    except (sqlite3.DatabaseError, OSError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if conn is not None:
            conn.close()
    return report


def _manual_ignore(config: BrainOSConfig, path: Path) -> tuple[bool, str]:
    try:
        metadata = load_markdown(path).metadata
    except FrontmatterError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    manual = config.core.get("manual_override") or {}
    field = str(manual.get("field", "javis") or "javis")
    return str(metadata.get(field) or "auto").strip().casefold() == "ignore", ""


def _identity_required(config: BrainOSConfig, observation) -> tuple[bool, str]:
    paths = config.core.get("paths") or {}
    exempt_zones = {
        str(paths.get("wiki") or "wiki"),
        str(paths.get("memory") or "memory"),
        str(paths.get("dashboard") or "00 - Dashboard"),
    }
    if observation.zone in exempt_zones:
        return False, "derived_or_dashboard_zone"
    ignored, error = _manual_ignore(
        config,
        config.brain_root / observation.path,
    )
    if error:
        return True, error
    if ignored:
        return False, "manual_ignore"
    return True, ""


def _collect_recovery_identity_state(config: BrainOSConfig) -> dict[str, Any]:
    collection = collect_files(config, existing_by_path={}, full_hash=True)
    missing_identity: list[str] = []
    metadata_errors: list[dict[str, str]] = []
    required_count = 0
    exempt_count = 0
    by_path: dict[str, dict[str, str]] = {}
    by_source: dict[str, str] = {}

    for observation in collection.observations:
        required, reason = _identity_required(config, observation)
        if required:
            required_count += 1
            if reason:
                metadata_errors.append({"path": observation.path, "error": reason})
            if not observation.javis_id:
                missing_identity.append(observation.path)
        else:
            exempt_count += 1
        by_path[observation.path] = {
            "content_hash": observation.sha256,
            "javis_id": observation.javis_id,
            "zone": observation.zone,
        }
        if observation.javis_id:
            by_source[observation.javis_id] = observation.path

    checkpoints = load_lifecycle_checkpoints(config)
    checkpoint_identity_drift: list[dict[str, str]] = []
    for source_id, checkpoint in checkpoints.items():
        if source_id in by_source:
            continue
        old_path = str(checkpoint.get("path") or "")
        current = by_path.get(old_path)
        if current is not None and current.get("javis_id") != source_id:
            checkpoint_identity_drift.append(
                {
                    "checkpoint_source_id": source_id,
                    "path": old_path,
                    "current_javis_id": str(current.get("javis_id") or ""),
                }
            )

    blockers: list[str] = []
    if collection.traversal_errors:
        blockers.append("scanner_traversal_errors")
    if collection.uncertain_paths:
        blockers.append("scanner_uncertain_paths")
    if collection.duplicate_javis_ids:
        blockers.append("duplicate_javis_id")
    if missing_identity:
        blockers.append("missing_durable_identity")
    if metadata_errors:
        blockers.append("frontmatter_metadata_errors")
    if checkpoint_identity_drift:
        blockers.append("checkpoint_identity_drift")

    return {
        "ok": not blockers,
        "blockers": blockers,
        "observed_files": len(collection.observations),
        "durable_required_files": required_count,
        "identity_exempt_files": exempt_count,
        "missing_identity": missing_identity[:100],
        "duplicate_javis_ids": sorted(collection.duplicate_javis_ids),
        "metadata_errors": metadata_errors[:100],
        "checkpoint_identity_drift": checkpoint_identity_drift[:100],
        "traversal_errors": list(collection.traversal_errors)[:100],
        "uncertain_paths": sorted(collection.uncertain_paths)[:100],
        "warnings": list(collection.warnings)[:100],
        "snapshot": by_path,
    }


def audit_recovery(config: BrainOSConfig) -> dict[str, Any]:
    db = _db_health(config)
    identity = _collect_recovery_identity_state(config)
    checkpoints = load_lifecycle_checkpoints(config)
    marker = _read_ready_marker(config)
    lifecycle: dict[str, Any] = {
        "checkpoint_count": len(checkpoints),
        "db_readable": False,
        "rows_with_lifecycle": 0,
        "missing_checkpoints": [],
        "hash_mismatches": [],
        "orphan_checkpoints": sorted(checkpoints),
    }
    if db["integrity_ok"]:
        lifecycle = audit_lifecycle_against_db(config)

    blockers = list(identity["blockers"])
    if marker is None:
        blockers.append("recovery_ready_marker_missing")
    if db["integrity_ok"]:
        if lifecycle["missing_checkpoints"]:
            blockers.append("lifecycle_checkpoint_missing")
        if lifecycle["hash_mismatches"]:
            blockers.append("lifecycle_checkpoint_mismatch")
    elif db["exists"] and marker is None:
        blockers.append("unreadable_db_without_prepared_recovery")

    # De-duplicate without hiding ordering/root cause.
    blockers = list(dict.fromkeys(blockers))
    return {
        "ok": True,
        "action": "recovery-audit",
        "read_only": True,
        "db": db,
        "identity": {key: value for key, value in identity.items() if key != "snapshot"},
        "lifecycle": lifecycle,
        "ready_marker": marker,
        "rebuild_ready": not blockers,
        "blockers": blockers,
    }


def _latest_event_id(config: BrainOSConfig) -> int:
    if not config.db_path.is_file():
        return 0
    with BrainIndex(config.db_path) as index:
        row = index._require().execute("SELECT COALESCE(MAX(event_id),0) FROM events").fetchone()
        return int(row[0]) if row else 0


def _mark_recovery_events_handled(
    config: BrainOSConfig,
    *,
    after_event_id: int = 0,
    paths: set[str] | None = None,
) -> int:
    if not config.db_path.is_file():
        return 0
    path_filter = set(paths or set())
    with BrainIndex(config.db_path) as index:
        conn = index._require()
        rows = conn.execute(
            "SELECT event_id, path FROM events WHERE handled_at='' AND event_id>? ORDER BY event_id",
            (int(after_event_id),),
        ).fetchall()
        ids = [
            int(row["event_id"])
            for row in rows
            if not path_filter or str(row["path"]) in path_filter
        ]
        if not ids:
            return 0
        now = utc_now()
        handled = 0
        for start in range(0, len(ids), 400):
            batch = ids[start : start + 400]
            marks = ",".join("?" for _ in batch)
            with conn:
                cur = conn.execute(
                    f"UPDATE events SET handled_at=? WHERE handled_at='' AND event_id IN ({marks})",
                    (now, *batch),
                )
            handled += int(cur.rowcount)
        return handled


def _assert_no_brain_watch(config: BrainOSConfig) -> None:
    watch_lock = config.path("state") / "brain-watch.lock"
    if watch_lock.exists():
        raise ControlledRecoveryError(
            f"Brain Watch lock đang tồn tại; từ chối maintenance đồng thời: {watch_lock}"
        )


@contextmanager
def _recovery_lock(config: BrainOSConfig) -> Iterator[None]:
    path = recovery_root(config) / "recovery.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ControlledRecoveryError(
            f"Recovery lock đã tồn tại; kiểm tra maintenance process trước: {path}"
        ) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"pid": os.getpid(), "created_at": _utc_now()}, fh, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        yield
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def prepare_recovery(config: BrainOSConfig) -> dict[str, Any]:
    with _recovery_lock(config):
        _assert_no_brain_watch(config)
        db = _db_health(config)
        if not db["integrity_ok"]:
            raise ControlledRecoveryError(
                "Recovery prepare cần DB hiện tại đọc được và PRAGMA quick_check=ok; "
                "không được backfill lifecycle bằng dữ liệu DB hỏng."
            )

        scan = reconcile_brain(config, full_hash=True)
        if not scan.ok:
            raise ControlledRecoveryError("Full reconcile trước prepare không PASS")

        first_backfill = backfill_lifecycle_checkpoints(config)
        baseline_event = _latest_event_id(config)
        plan = plan_materialization(config)
        if plan["conflicts"]:
            raise ControlledRecoveryError(
                "Có javis_id conflict; resolve thủ công trước khi recovery prepare."
            )
        materialized = apply_materialization(config, plan)
        materialized_paths = {item["path"] for item in materialized["changed"]}
        handled_identity_events = _mark_recovery_events_handled(
            config,
            after_event_id=baseline_event,
            paths=materialized_paths,
        )

        if materialized_paths:
            classify_brain(config, force=True, paths=materialized_paths)
            plan_brain_taxonomy(config, force=True, paths=materialized_paths)

        second_backfill = backfill_lifecycle_checkpoints(config)
        lifecycle = audit_lifecycle_against_db(config)
        if lifecycle["missing_checkpoints"] or lifecycle["hash_mismatches"]:
            raise ControlledRecoveryError(
                "Lifecycle checkpoint audit chưa sạch sau prepare; từ chối ready marker."
            )

        identity = _collect_recovery_identity_state(config)
        if not identity["ok"]:
            raise ControlledRecoveryError(
                f"Durable identity audit chưa sạch sau prepare: {identity['blockers']}"
            )

        checkpoints = load_lifecycle_checkpoints(config)
        marker = _write_ready_marker(
            config,
            observed_files=identity["observed_files"],
            durable_required_files=identity["durable_required_files"],
            checkpoint_count=len(checkpoints),
        )
        final = audit_recovery(config)
        if not final["rebuild_ready"]:
            raise ControlledRecoveryError(
                f"Recovery prepare kết thúc nhưng rebuild vẫn bị block: {final['blockers']}"
            )
        return {
            "scan": scan.to_dict(),
            "first_backfill": first_backfill,
            "identity": materialized,
            "handled_identity_events": handled_identity_events,
            "second_backfill": second_backfill,
            "lifecycle": lifecycle,
            "ready_marker": marker,
            "audit": final,
        }


def _db_files(config: BrainOSConfig) -> list[Path]:
    return [
        config.db_path,
        Path(str(config.db_path) + "-wal"),
        Path(str(config.db_path) + "-shm"),
    ]


def _checkpoint_wal_if_possible(config: BrainOSConfig) -> None:
    health = _db_health(config)
    if not health["integrity_ok"]:
        return
    conn = sqlite3.connect(str(config.db_path))
    try:
        conn.execute("PRAGMA wal_checkpoint(FULL)").fetchall()
    finally:
        conn.close()


def _archive_db(config: BrainOSConfig, health: dict[str, Any]) -> dict[str, Any]:
    _checkpoint_wal_if_possible(config)
    root = recovery_root(config) / "db-archives"
    root.mkdir(parents=True, exist_ok=True)
    archive = root / f"{_timestamp_slug()}-{os.getpid()}"
    archive.mkdir(parents=True, exist_ok=False)

    files: list[dict[str, Any]] = []
    for source in _db_files(config):
        if not source.is_file():
            continue
        target = archive / source.name
        shutil.copy2(source, target)
        source_hash = _sha256_file(source)
        target_hash = _sha256_file(target)
        if source_hash != target_hash:
            raise ControlledRecoveryError(f"DB archive hash verify failed: {source}")
        files.append(
            {
                "name": source.name,
                "sha256": source_hash,
                "size": int(source.stat().st_size),
            }
        )

    manifest = {
        "schema_version": 1,
        "created_at": _utc_now(),
        "db_path": str(config.db_path),
        "db_health_before": health,
        "files": files,
    }
    manifest["checksum"] = _checksum(manifest)
    _atomic_write_json(archive / "manifest.json", manifest)

    # Only remove originals after every archive member has been copied and verified.
    for source in reversed(_db_files(config)):
        source.unlink(missing_ok=True)
    return {
        "path": str(archive.relative_to(config.brain_root)),
        "absolute_path": str(archive),
        "files": files,
    }


def _restore_db_archive(config: BrainOSConfig, archive: dict[str, Any]) -> None:
    archive_path = Path(str(archive.get("absolute_path") or ""))
    if not archive_path.is_dir():
        return
    for current in reversed(_db_files(config)):
        current.unlink(missing_ok=True)
    for record in archive.get("files") or []:
        source = archive_path / str(record["name"])
        target = config.db_path.parent / str(record["name"])
        if not source.is_file():
            raise ControlledRecoveryError(f"Archive rollback thiếu file: {source}")
        shutil.copy2(source, target)
        if _sha256_file(target) != str(record["sha256"]):
            raise ControlledRecoveryError(f"Archive rollback hash mismatch: {target}")


def _snapshot_for_race_check(identity: dict[str, Any]) -> dict[str, tuple[str, str]]:
    raw = identity.get("snapshot") or {}
    return {
        path: (str(value.get("content_hash") or ""), str(value.get("javis_id") or ""))
        for path, value in raw.items()
    }


def rebuild_database(config: BrainOSConfig) -> dict[str, Any]:
    with _recovery_lock(config):
        _assert_no_brain_watch(config)
        audit = audit_recovery(config)
        if not audit["rebuild_ready"]:
            raise ControlledRecoveryError(
                f"Recovery preflight chưa sẵn sàng: {audit['blockers']}"
            )
        before_identity = _collect_recovery_identity_state(config)
        before_snapshot = _snapshot_for_race_check(before_identity)
        health = _db_health(config)
        archive: dict[str, Any] = {"path": "", "absolute_path": "", "files": []}

        try:
            archive = _archive_db(config, health)
            scan = reconcile_brain(config, full_hash=True)
            if not scan.ok:
                raise ControlledRecoveryError("Fresh full reconcile sau DB archive không PASS")

            lifecycle = restore_lifecycle_checkpoints(config)
            classification = classify_brain(config, force=True)
            taxonomy = plan_brain_taxonomy(config, force=True)

            after_identity = _collect_recovery_identity_state(config)
            after_snapshot = _snapshot_for_race_check(after_identity)
            if before_snapshot != after_snapshot:
                raise ControlledRecoveryError(
                    "Filesystem thay đổi trong lúc rebuild; rollback DB để không nuốt concurrent edit."
                )
            if not after_identity["ok"]:
                raise ControlledRecoveryError(
                    f"Identity audit hỏng sau rebuild: {after_identity['blockers']}"
                )

            # Fresh DB sees every source as CREATED. They are recovery baseline events,
            # not new user changes, so consume them only after byte/hash identity verify.
            handled_events = _mark_recovery_events_handled(config, after_event_id=0)
            lifecycle_audit = audit_lifecycle_against_db(config)
            if lifecycle_audit["missing_checkpoints"] or lifecycle_audit["hash_mismatches"]:
                raise ControlledRecoveryError(
                    "Lifecycle mismatch sau restore; rollback DB archive."
                )

            with BrainIndex(config.db_path) as index:
                index.set_meta("last_recovery_rebuild_at", utc_now())
                index.set_meta("last_recovery_archive", archive["path"])
                index.set_meta("recovery_rebuild_version", "1")

            checkpoints = load_lifecycle_checkpoints(config)
            marker = _write_ready_marker(
                config,
                observed_files=after_identity["observed_files"],
                durable_required_files=after_identity["durable_required_files"],
                checkpoint_count=len(checkpoints),
                last_rebuild_archive=archive["path"],
            )
            final = audit_recovery(config)
            if not final["rebuild_ready"]:
                raise ControlledRecoveryError(
                    f"Post-rebuild audit chưa sạch: {final['blockers']}"
                )
            return {
                "archive": {key: value for key, value in archive.items() if key != "absolute_path"},
                "scan": scan.to_dict(),
                "lifecycle": lifecycle,
                "classification": classification.to_dict(),
                "taxonomy": taxonomy.to_dict(),
                "handled_recovery_events": handled_events,
                "ready_marker": marker,
                "audit": final,
            }
        except Exception:
            try:
                _restore_db_archive(config, archive)
            except Exception:
                pass
            raise


def _prepare_preview(config: BrainOSConfig) -> dict[str, Any]:
    audit = audit_recovery(config)
    plan: dict[str, Any] = {}
    if audit["db"]["integrity_ok"]:
        plan = plan_materialization(config)
    return {
        "can_apply": bool(audit["db"]["integrity_ok"] and not plan.get("conflicts", [])),
        "identity_plan": plan,
        "audit": audit,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Brain OS controlled recovery hardening")
    parser.add_argument("--brain-root", help="Override Brain/vault root.")
    parser.add_argument("--compact", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("audit", help="Read-only recovery readiness + DB integrity audit.")

    prepare = sub.add_parser(
        "prepare",
        help="Backfill lifecycle + materialize durable identity; preview by default.",
    )
    prepare.add_argument("--apply", action="store_true")

    rebuild = sub.add_parser(
        "rebuild",
        help="Archive and rebuild SQLite from durable Brain state; preview by default.",
    )
    rebuild.add_argument("--apply", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        config = BrainOSConfig.load(resolve_root(args.brain_root))
        if args.command == "audit":
            output = audit_recovery(config)
        elif args.command == "prepare":
            if not args.apply:
                output = {
                    "ok": True,
                    "action": "recovery-prepare-preview",
                    "dry_run": True,
                    "writes_user_files": False,
                    "mutates_frontmatter": False,
                    "result": _prepare_preview(config),
                }
            else:
                result = prepare_recovery(config)
                output = {
                    "ok": True,
                    "action": "recovery-prepare-apply",
                    "dry_run": False,
                    "explicit_apply": True,
                    "writes_user_files": bool(result["identity"]["applied"]),
                    "mutates_frontmatter": bool(result["identity"]["applied"]),
                    "executes_javis_ingest": False,
                    "writes_wiki": False,
                    "writes_memory": False,
                    "result": result,
                }
        elif args.command == "rebuild":
            if not args.apply:
                audit = audit_recovery(config)
                output = {
                    "ok": True,
                    "action": "recovery-rebuild-preview",
                    "dry_run": True,
                    "writes_user_files": False,
                    "database_writes": False,
                    "result": audit,
                }
            else:
                result = rebuild_database(config)
                output = {
                    "ok": True,
                    "action": "recovery-rebuild-apply",
                    "dry_run": False,
                    "explicit_apply": True,
                    "writes_user_files": False,
                    "mutates_frontmatter": False,
                    "executes_javis_ingest": False,
                    "writes_wiki": False,
                    "writes_memory": False,
                    "database_rebuilt": True,
                    "result": result,
                }
        else:  # pragma: no cover
            parser.error(f"Unknown command: {args.command}")
            return 2
        _emit(output, compact=bool(args.compact))
        return 0 if output.get("ok") else 2
    except (
        ControlledRecoveryError,
        BrainRecoveryError,
        BrainOSConfigError,
        BrainIndexError,
        FrontmatterError,
        TaxonomyError,
        sqlite3.DatabaseError,
        OSError,
        ValueError,
    ) as exc:
        _emit(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            compact=bool(getattr(args, "compact", False)),
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
