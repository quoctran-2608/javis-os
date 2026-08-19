#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath

from brain_os import resolve_root
from brain_os_lib.config import BrainOSConfig, BrainOSConfigError
from brain_os_lib.db import BrainIndex, BrainIndexError, utc_now
from brain_os_lib.models import ProcessingState
from brain_os_lib.paths import safe_join
from brain_os_lib.reconcile import reconcile_brain
from brain_os_lib.recovery import BrainRecoveryError, write_lifecycle_for_item


class IngestRecordError(RuntimeError):
    pass


def _json(data: dict, *, compact: bool = False) -> None:
    print(
        json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":") if compact else None,
            indent=None if compact else 2,
        )
    )


def _brain_relative_path(config: BrainOSConfig, value: str) -> str:
    raw = Path(value).expanduser()
    if raw.is_absolute():
        try:
            return raw.resolve().relative_to(config.brain_root).as_posix()
        except ValueError as exc:
            raise IngestRecordError(
                f"Path phải nằm trong Brain hiện tại: {raw}"
            ) from exc

    rel = PurePosixPath(str(value).replace("\\", "/")).as_posix()
    safe_join(config.brain_root, rel)
    return rel


def record_ingest(
    config: BrainOSConfig,
    *,
    path: str,
    compounded: bool = False,
) -> dict:
    rel_path = _brain_relative_path(config, path)
    target = safe_join(config.brain_root, rel_path)
    if not target.is_file():
        raise IngestRecordError(f"Không tìm thấy Brain source: {rel_path}")

    # Refresh deterministic state first so the recorded hash is exactly the bytes
    # Javis has just consumed. This writes derived state only under .javis/.
    reconcile_brain(config, full_hash=False)

    with BrainIndex(config.db_path) as index:
        item = index.get_file_by_path(rel_path)
        if item is None:
            raise IngestRecordError(
                f"Source chưa được Brain OS index: {rel_path}; chạy scan/import trước."
            )
        if item.state in {ProcessingState.MISSING, ProcessingState.IGNORED}:
            raise IngestRecordError(
                f"Không được record ingest cho state={item.state.value}: {rel_path}"
            )
        if not item.content_hash or len(item.content_hash) != 64:
            raise IngestRecordError(
                f"Source chưa có content hash hợp lệ: {rel_path}"
            )

        item.last_ingested_hash = item.content_hash
        item.last_ingested_at = utc_now()
        item.state = (
            ProcessingState.COMPOUNDED if compounded else ProcessingState.INGESTED
        )

        # Recovery checkpoint is written before the DB row. Javis INGEST has already
        # happened when this helper is called, so if the DB update fails afterward,
        # preserving the completed-ingest fact outside SQLite is the safer failure mode.
        checkpoint = write_lifecycle_for_item(config, item)
        index.upsert_file(item)

        return {
            "ok": True,
            "action": "record-ingest",
            "path": item.path,
            "source_id": item.source_id,
            "document_type": item.document_type.value,
            "content_hash": item.content_hash,
            "last_ingested_hash": item.last_ingested_hash,
            "last_ingested_at": item.last_ingested_at,
            "state": item.state.value,
            "recovery_checkpointed": True,
            "recovery_state_hint": checkpoint["state_hint"],
            "writes_user_files": False,
            "derived_state_only": True,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record a completed Javis INGEST in Brain OS derived state."
    )
    parser.add_argument("--path", required=True, help="Brain-relative managed Markdown path.")
    parser.add_argument("--brain-root", help="Override Brain/vault root.")
    parser.add_argument(
        "--compounded",
        action="store_true",
        help="Mark COMPOUNDED when Javis actually wrote derived Wiki/Memory knowledge.",
    )
    parser.add_argument("--compact", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        config = BrainOSConfig.load(resolve_root(args.brain_root))
        payload = record_ingest(
            config,
            path=str(args.path),
            compounded=bool(args.compounded),
        )
        _json(payload, compact=bool(args.compact))
        return 0
    except (
        BrainOSConfigError,
        BrainIndexError,
        BrainRecoveryError,
        IngestRecordError,
        OSError,
        ValueError,
    ) as exc:
        _json(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            compact=bool(getattr(args, "compact", False)),
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
