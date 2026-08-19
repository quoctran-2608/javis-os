from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import BrainOSConfig
from .db import BrainIndex, BrainIndexError
from .identity import valid_existing_id
from .models import ProcessingState


RECOVERY_SCHEMA_VERSION = 1
LIFECYCLE_STATE_HINTS = {"ingested", "compounded"}


class BrainRecoveryError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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


def _canonical_checksum(payload: dict[str, Any]) -> str:
    clean = {key: value for key, value in payload.items() if key != "checksum"}
    encoded = json.dumps(
        clean,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_sha256(value: Any, *, field: str) -> str:
    raw = str(value or "").strip().lower()
    if len(raw) != 64 or any(ch not in "0123456789abcdef" for ch in raw):
        raise BrainRecoveryError(f"{field} không phải SHA-256 hợp lệ")
    return raw


def recovery_root(config: BrainOSConfig) -> Path:
    return config.path("state") / "recovery"


def lifecycle_root(config: BrainOSConfig) -> Path:
    return recovery_root(config) / "lifecycle"


def lifecycle_checkpoint_path(config: BrainOSConfig, source_id: str) -> Path:
    raw = str(source_id or "").strip()
    if not valid_existing_id(raw):
        raise BrainRecoveryError(f"source_id không hợp lệ cho recovery checkpoint: {source_id!r}")
    return lifecycle_root(config) / f"{_sha256_text(raw)}.json"


def _validate_lifecycle_payload(payload: dict[str, Any], *, path: Path) -> dict[str, Any]:
    if int(payload.get("schema_version", 0) or 0) != RECOVERY_SCHEMA_VERSION:
        raise BrainRecoveryError(f"Lifecycle checkpoint schema không hỗ trợ: {path}")
    source_id = str(payload.get("source_id") or "").strip()
    if not valid_existing_id(source_id):
        raise BrainRecoveryError(f"Lifecycle checkpoint thiếu source_id hợp lệ: {path}")
    last_hash = _validate_sha256(
        payload.get("last_ingested_hash"),
        field=f"{path}: last_ingested_hash",
    )
    state_hint = str(payload.get("state_hint") or "").strip().casefold()
    if state_hint not in LIFECYCLE_STATE_HINTS:
        raise BrainRecoveryError(f"Lifecycle checkpoint state_hint không hợp lệ: {path}")
    expected = str(payload.get("checksum") or "").strip().lower()
    actual = _canonical_checksum(payload)
    if expected != actual:
        raise BrainRecoveryError(
            f"Lifecycle checkpoint checksum mismatch: {path}; expected={expected} actual={actual}"
        )
    normalized = dict(payload)
    normalized["source_id"] = source_id
    normalized["last_ingested_hash"] = last_hash
    normalized["state_hint"] = state_hint
    normalized["path"] = str(payload.get("path") or "")
    normalized["last_ingested_at"] = str(payload.get("last_ingested_at") or "")
    normalized["checkpointed_at"] = str(payload.get("checkpointed_at") or "")
    return normalized


def read_lifecycle_checkpoint(
    config: BrainOSConfig,
    source_id: str,
) -> dict[str, Any] | None:
    path = lifecycle_checkpoint_path(config, source_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrainRecoveryError(f"Lifecycle checkpoint hỏng: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise BrainRecoveryError(f"Lifecycle checkpoint phải là JSON object: {path}")
    normalized = _validate_lifecycle_payload(payload, path=path)
    if normalized["source_id"] != str(source_id):
        raise BrainRecoveryError(
            f"Lifecycle checkpoint source_id collision: {path}; "
            f"expected={source_id} actual={normalized['source_id']}"
        )
    return normalized


def write_lifecycle_checkpoint(
    config: BrainOSConfig,
    *,
    source_id: str,
    path: str,
    last_ingested_hash: str,
    last_ingested_at: str,
    state_hint: str,
) -> dict[str, Any]:
    source_id = str(source_id or "").strip()
    if not valid_existing_id(source_id):
        raise BrainRecoveryError(f"source_id không hợp lệ: {source_id!r}")
    last_hash = _validate_sha256(last_ingested_hash, field="last_ingested_hash")
    normalized_state = str(state_hint or "").strip().casefold()
    if normalized_state not in LIFECYCLE_STATE_HINTS:
        raise BrainRecoveryError(f"state_hint không hợp lệ: {state_hint!r}")

    target = lifecycle_checkpoint_path(config, source_id)
    payload: dict[str, Any] = {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "source_id": source_id,
        "path": str(path or ""),
        "last_ingested_hash": last_hash,
        "last_ingested_at": str(last_ingested_at or ""),
        "state_hint": normalized_state,
        "checkpointed_at": _utc_now(),
    }
    payload["checksum"] = _canonical_checksum(payload)
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(target, encoded)

    verified = read_lifecycle_checkpoint(config, source_id)
    if verified is None or verified["last_ingested_hash"] != last_hash:
        raise BrainRecoveryError(f"Không verify được lifecycle checkpoint: {target}")
    return verified


def write_lifecycle_for_item(
    config: BrainOSConfig,
    item,
    *,
    state_hint: str | None = None,
) -> dict[str, Any]:
    if not item.last_ingested_hash:
        raise BrainRecoveryError(
            f"Không thể checkpoint source chưa có last_ingested_hash: {item.path}"
        )
    if state_hint is None:
        state_hint = (
            "compounded"
            if item.state == ProcessingState.COMPOUNDED
            else "ingested"
        )
    return write_lifecycle_checkpoint(
        config,
        source_id=item.source_id,
        path=item.path,
        last_ingested_hash=item.last_ingested_hash,
        last_ingested_at=item.last_ingested_at,
        state_hint=state_hint,
    )


def load_lifecycle_checkpoints(config: BrainOSConfig) -> dict[str, dict[str, Any]]:
    root = lifecycle_root(config)
    if not root.is_dir():
        return {}
    checkpoints: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BrainRecoveryError(f"Lifecycle checkpoint hỏng: {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise BrainRecoveryError(f"Lifecycle checkpoint phải là object: {path}")
        normalized = _validate_lifecycle_payload(payload, path=path)
        source_id = normalized["source_id"]
        expected_name = lifecycle_checkpoint_path(config, source_id).name
        if path.name != expected_name:
            raise BrainRecoveryError(
                f"Lifecycle checkpoint filename không khớp source_id: {path.name} != {expected_name}"
            )
        if source_id in checkpoints:
            raise BrainRecoveryError(f"Duplicate lifecycle checkpoint source_id: {source_id}")
        checkpoints[source_id] = normalized
    return checkpoints


def backfill_lifecycle_checkpoints(config: BrainOSConfig) -> dict[str, Any]:
    if not config.db_path.is_file():
        raise BrainIndexError("Brain index chưa được khởi tạo")
    existing = load_lifecycle_checkpoints(config)
    written = 0
    reused = 0
    rows_with_lifecycle = 0
    with BrainIndex(config.db_path) as index:
        rows = index._require().execute("SELECT source_id FROM files ORDER BY path").fetchall()
        for row in rows:
            item = index.get_file(str(row["source_id"]))
            if item is None or not item.last_ingested_hash:
                continue
            rows_with_lifecycle += 1
            previous = existing.get(item.source_id)
            state_hint = (
                previous["state_hint"]
                if previous is not None
                else (
                    "compounded"
                    if item.state == ProcessingState.COMPOUNDED
                    else "ingested"
                )
            )
            if (
                previous is not None
                and previous["last_ingested_hash"] == item.last_ingested_hash
                and previous["last_ingested_at"] == item.last_ingested_at
                and previous["path"] == item.path
            ):
                reused += 1
                continue
            write_lifecycle_checkpoint(
                config,
                source_id=item.source_id,
                path=item.path,
                last_ingested_hash=item.last_ingested_hash,
                last_ingested_at=item.last_ingested_at,
                state_hint=state_hint,
            )
            written += 1
    return {
        "rows_with_lifecycle": rows_with_lifecycle,
        "written": written,
        "reused": reused,
    }


def audit_lifecycle_against_db(config: BrainOSConfig) -> dict[str, Any]:
    checkpoints = load_lifecycle_checkpoints(config)
    report: dict[str, Any] = {
        "checkpoint_count": len(checkpoints),
        "db_readable": False,
        "rows_with_lifecycle": 0,
        "missing_checkpoints": [],
        "hash_mismatches": [],
        "orphan_checkpoints": [],
    }
    if not config.db_path.is_file():
        report["orphan_checkpoints"] = sorted(checkpoints)
        return report

    db_ids: set[str] = set()
    with BrainIndex(config.db_path) as index:
        report["db_readable"] = True
        rows = index._require().execute("SELECT source_id FROM files ORDER BY path").fetchall()
        for row in rows:
            item = index.get_file(str(row["source_id"]))
            if item is None:
                continue
            db_ids.add(item.source_id)
            if not item.last_ingested_hash:
                continue
            report["rows_with_lifecycle"] += 1
            checkpoint = checkpoints.get(item.source_id)
            if checkpoint is None:
                report["missing_checkpoints"].append(item.path)
                continue
            if checkpoint["last_ingested_hash"] != item.last_ingested_hash:
                report["hash_mismatches"].append(
                    {
                        "path": item.path,
                        "source_id": item.source_id,
                        "db": item.last_ingested_hash,
                        "checkpoint": checkpoint["last_ingested_hash"],
                    }
                )
    report["orphan_checkpoints"] = sorted(set(checkpoints) - db_ids)
    return report


def restore_lifecycle_checkpoints(config: BrainOSConfig) -> dict[str, Any]:
    if not config.db_path.is_file():
        raise BrainIndexError("Brain index chưa được khởi tạo")
    checkpoints = load_lifecycle_checkpoints(config)
    restored_current = 0
    restored_stale = 0
    missing_sources: list[str] = []
    with BrainIndex(config.db_path) as index:
        for source_id, checkpoint in checkpoints.items():
            item = index.get_file(source_id)
            if item is None or item.state == ProcessingState.MISSING:
                missing_sources.append(source_id)
                continue
            item.last_ingested_hash = checkpoint["last_ingested_hash"]
            item.last_ingested_at = checkpoint["last_ingested_at"]
            if item.content_hash == item.last_ingested_hash:
                item.state = (
                    ProcessingState.COMPOUNDED
                    if checkpoint["state_hint"] == "compounded"
                    else ProcessingState.INGESTED
                )
                restored_current += 1
            else:
                item.state = ProcessingState.STALE
                restored_stale += 1
            index.upsert_file(item)
    return {
        "checkpoint_count": len(checkpoints),
        "restored_current": restored_current,
        "restored_stale": restored_stale,
        "missing_sources": sorted(missing_sources),
    }
