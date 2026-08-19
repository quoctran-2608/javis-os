#!/usr/bin/env python3
"""Recovery hardening: persist DB-only Brain OS identities into Markdown.

This command is intentionally separate from scan/classify/watch. Preview is the
default. `--apply` is explicit maintenance permission to add only `javis_id` to
eligible user-authored Markdown, after preserving the exact pre-change bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from brain_os_lib.config import BrainOSConfig, BrainOSConfigError
from brain_os_lib.db import BrainIndex, BrainIndexError
from brain_os_lib.frontmatter import FrontmatterError, load_markdown, update_frontmatter
from brain_os_lib.identity import read_javis_id, valid_existing_id
from brain_os_lib.models import ProcessingState
from brain_os_lib.reconcile import reconcile_brain


class IdentityMaterializationError(RuntimeError):
    pass


PROTECTED_DOCUMENT_TYPES = {
    "derived_wiki",
    "memory",
    "system",
    "binary_source",
}


def infer_brain_root(script_path: Path) -> Path | None:
    p = script_path.resolve()
    scripts_dir = p.parent
    skill_dir = scripts_dir.parent
    skills_dir = skill_dir.parent
    if (
        scripts_dir.name == "scripts"
        and skill_dir.name == "brain-manager"
        and skills_dir.name == "skills"
    ):
        return skills_dir.parent.resolve()
    return None


def resolve_root(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    inferred = infer_brain_root(Path(__file__))
    if inferred is None:
        raise BrainOSConfigError(
            "Không suy được Brain root; truyền --brain-root hoặc cài script đúng vị trí."
        )
    return inferred


def emit(data: dict[str, Any], *, compact: bool) -> None:
    if compact:
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def _manual_mode(config: BrainOSConfig, metadata: dict[str, Any]) -> str:
    manual = config.core.get("manual_override") or {}
    field = str(manual.get("field", "javis") or "javis")
    return str(metadata.get(field) or "auto").strip().casefold()


def _indexed_items(config: BrainOSConfig):
    if not config.db_path.is_file():
        raise BrainIndexError(
            "Brain index chưa được khởi tạo; chạy `brain_os.py scan` trước khi materialize identity."
        )
    items = []
    with BrainIndex(config.db_path) as index:
        rows = index._require().execute("SELECT source_id FROM files ORDER BY path").fetchall()
        for row in rows:
            item = index.get_file(str(row["source_id"]))
            if item is not None:
                items.append(item)
    return items


def plan_materialization(config: BrainOSConfig) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    blocked_ingested: list[dict[str, Any]] = []
    durable: list[str] = []
    skipped: list[dict[str, str]] = []

    for item in _indexed_items(config):
        if item.state == ProcessingState.MISSING:
            skipped.append({"path": item.path, "reason": "missing"})
            continue
        if str(item.document_type.value) in PROTECTED_DOCUMENT_TYPES:
            skipped.append({"path": item.path, "reason": "protected_document_type"})
            continue

        path = config.brain_root / item.path
        if path.suffix.lower() not in {".md", ".markdown"} or not path.is_file():
            skipped.append({"path": item.path, "reason": "not_current_markdown"})
            continue

        document = load_markdown(path)
        if _manual_mode(config, document.metadata) == "ignore":
            skipped.append({"path": item.path, "reason": "manual_ignore"})
            continue

        raw = str(document.metadata.get("javis_id") or "").strip()
        if raw:
            if valid_existing_id(raw) and raw == item.source_id:
                durable.append(item.path)
            else:
                conflicts.append(
                    {
                        "path": item.path,
                        "database_source_id": item.source_id,
                        "frontmatter_javis_id": raw,
                        "reason": "frontmatter_identity_conflict",
                    }
                )
            continue

        record = {
            "path": item.path,
            "source_id": item.source_id,
            "content_hash": item.content_hash,
        }
        if item.last_ingested_hash:
            blocked_ingested.append(
                {
                    **record,
                    "last_ingested_hash": item.last_ingested_hash,
                    "reason": "already_ingested_requires_separate_migration",
                }
            )
            continue
        candidates.append(record)

    ready = not conflicts and not blocked_ingested
    return {
        "ready_to_apply": ready,
        "candidates": candidates,
        "conflicts": conflicts,
        "blocked_ingested": blocked_ingested,
        "durable_paths": durable,
        "skipped": skipped,
        "counts": {
            "candidates": len(candidates),
            "conflicts": len(conflicts),
            "blocked_ingested": len(blocked_ingested),
            "already_durable": len(durable),
            "skipped": len(skipped),
        },
    }


def _backup_identity_source(
    config: BrainOSConfig,
    *,
    source_id: str,
    relative_path: str,
    source_bytes: bytes,
) -> Path:
    root = config.brain_root / ".javis" / "originals" / "identity-bootstrap" / source_id
    original = root / "original.md"
    manifest = root / "manifest.json"
    digest = _sha256(source_bytes)

    if root.exists():
        if not original.is_file() or not manifest.is_file():
            raise IdentityMaterializationError(
                f"Identity backup không hoàn chỉnh cho {source_id}: {root}"
            )
        existing = original.read_bytes()
        if _sha256(existing) != digest:
            raise IdentityMaterializationError(
                f"Identity backup collision cho {source_id}; từ chối overwrite immutable backup."
            )
        return original

    root.mkdir(parents=True, exist_ok=False)
    _atomic_write_bytes(original, source_bytes)
    payload = {
        "schema_version": 1,
        "source_id": source_id,
        "path": relative_path,
        "source_sha256": digest,
        "created_at": _utc_now(),
        "purpose": "pre-javis_id-materialization backup",
    }
    _atomic_write_bytes(
        manifest,
        (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )
    if original.read_bytes() != source_bytes:
        raise IdentityMaterializationError(f"Không verify được identity backup: {original}")
    return original


def apply_materialization(config: BrainOSConfig, plan: dict[str, Any]) -> dict[str, Any]:
    if plan["conflicts"]:
        raise IdentityMaterializationError(
            "Có frontmatter `javis_id` xung đột với DB; không tự overwrite identity do người dùng sở hữu."
        )
    if plan["blocked_ingested"]:
        raise IdentityMaterializationError(
            "Có note DB-only identity đã từng ingest; cần migration riêng để không tạo stale/re-ingest giả."
        )

    changed: list[dict[str, Any]] = []
    try:
        for record in plan["candidates"]:
            path = config.brain_root / record["path"]
            before = path.read_bytes()
            backup = _backup_identity_source(
                config,
                source_id=record["source_id"],
                relative_path=record["path"],
                source_bytes=before,
            )
            result = update_frontmatter(
                path,
                updates={"javis_id": record["source_id"]},
                dry_run=False,
            )
            if not result.changed:
                raise IdentityMaterializationError(
                    f"Expected identity write nhưng file không đổi: {record['path']}"
                )
            if read_javis_id(path) != record["source_id"]:
                raise IdentityMaterializationError(
                    f"Identity verification failed sau apply: {record['path']}"
                )
            changed.append(
                {
                    **record,
                    "backup_path": str(backup.relative_to(config.brain_root)),
                    "before_bytes": before,
                }
            )

        scan = reconcile_brain(config, full_hash=True)
        if not scan.ok:
            raise IdentityMaterializationError(
                "Full reconcile sau identity materialization không PASS; rollback user files."
            )

        with BrainIndex(config.db_path) as index:
            for record in changed:
                current = index.get_file_by_path(record["path"])
                if current is None or current.source_id != record["source_id"]:
                    raise IdentityMaterializationError(
                        f"Stable identity không giữ sau reconcile: {record['path']}"
                    )

        public_changed = [
            {key: value for key, value in record.items() if key != "before_bytes"}
            for record in changed
        ]
        return {
            "applied": len(changed),
            "changed": public_changed,
            "scan": scan.to_dict(),
        }
    except Exception:
        for record in reversed(changed):
            try:
                _atomic_write_bytes(
                    config.brain_root / record["path"],
                    record["before_bytes"],
                )
            except OSError:
                pass
        try:
            if config.db_path.is_file():
                reconcile_brain(config, full_hash=True)
        except Exception:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Brain OS recovery hardening: durable stable identity materialization"
    )
    parser.add_argument("--brain-root")
    parser.add_argument("--compact", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    materialize = sub.add_parser(
        "materialize",
        help="Preview/apply javis_id for DB-only identities; preview is default.",
    )
    materialize.add_argument(
        "--apply",
        action="store_true",
        help="Explicitly add javis_id after immutable pre-change backup.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        config = BrainOSConfig.load(resolve_root(args.brain_root))
        if args.command != "materialize":  # pragma: no cover
            parser.error(f"Unknown command: {args.command}")
            return 2

        plan = plan_materialization(config)
        if not args.apply:
            output = {
                "ok": True,
                "action": "identity-materialize-preview",
                "dry_run": True,
                "writes_user_files": False,
                "mutates_frontmatter": False,
                "executes_javis_ingest": False,
                "writes_wiki": False,
                "writes_memory": False,
                "plan": plan,
            }
        else:
            result = apply_materialization(config, plan)
            output = {
                "ok": True,
                "action": "identity-materialize-apply",
                "dry_run": False,
                "explicit_apply": True,
                "writes_user_files": bool(result["applied"]),
                "mutates_frontmatter": bool(result["applied"]),
                "only_frontmatter_field": "javis_id",
                "executes_javis_ingest": False,
                "writes_wiki": False,
                "writes_memory": False,
                "plan": plan,
                "result": result,
            }
        emit(output, compact=bool(args.compact))
        return 0
    except (
        IdentityMaterializationError,
        BrainOSConfigError,
        BrainIndexError,
        FrontmatterError,
        OSError,
        ValueError,
    ) as exc:
        emit(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            compact=bool(getattr(args, "compact", False)),
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
