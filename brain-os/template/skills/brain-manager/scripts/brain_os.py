#!/usr/bin/env python3
"""Brain OS deterministic CLI.

Stage 3 adds read-only filesystem observation and writes only derived state
under `.javis/` (SQLite + incremental-diff snapshots). It still does not move,
rename, annotate, classify with AI, ingest or create Wiki pages.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from brain_os_lib.config import BrainOSConfig, BrainOSConfigError
from brain_os_lib.db import BrainIndex, BrainIndexError, SCHEMA_VERSION
from brain_os_lib.hashing import fingerprint_file
from brain_os_lib.paths import BrainPaths
from brain_os_lib.reconcile import list_events, reconcile_brain


def infer_brain_root(script_path: Path) -> Path | None:
    """Infer <brain> from <brain>/skills/brain-manager/scripts/brain_os.py."""

    p = script_path.resolve()
    try:
        scripts_dir = p.parent
        skill_dir = scripts_dir.parent
        skills_dir = skill_dir.parent
        if (
            scripts_dir.name == "scripts"
            and skill_dir.name == "brain-manager"
            and skills_dir.name == "skills"
        ):
            return skills_dir.parent.resolve()
    except OSError:
        return None
    return None


def _json(data: dict[str, Any], *, compact: bool = False) -> None:
    if compact:
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def resolve_root(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    inferred = infer_brain_root(Path(__file__))
    if inferred is None:
        raise BrainOSConfigError(
            "Không suy được Brain root. Cài script tại "
            "<brain>/skills/brain-manager/scripts/ hoặc truyền --brain-root."
        )
    return inferred


def cmd_init(config: BrainOSConfig) -> dict[str, Any]:
    paths = BrainPaths(config)
    state_dir = paths.ensure_state_dir()
    with BrainIndex(config.db_path) as index:
        index.set_meta("brain_root", str(config.brain_root))
        status = index.status()
    return {
        "ok": True,
        "action": "init",
        "dry_run": config.dry_run,
        "brain_root": str(config.brain_root),
        "state_dir": str(state_dir),
        "database": status,
        "note": "Chỉ khởi tạo derived state. Không scan, move, ingest hay sửa note.",
    }


def cmd_status(config: BrainOSConfig) -> dict[str, Any]:
    exists = config.db_path.is_file()
    database: dict[str, Any] = {"path": str(config.db_path), "initialized": False}
    scan: dict[str, Any] = {}
    if exists:
        with BrainIndex(config.db_path) as index:
            database = {"initialized": True, **index.status()}
            raw = index.get_meta("last_scan_report", "")
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        scan = parsed
                except json.JSONDecodeError:
                    scan = {}
            scan["last_scan_at"] = index.get_meta("last_scan_at", "")
            scan["last_full_reconcile_at"] = index.get_meta(
                "last_full_reconcile_at", ""
            )
    return {
        "ok": True,
        "action": "status",
        "config": config.summary(),
        "database": database,
        "scan": scan,
    }


def _check_fts5() -> bool:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE probe_fts USING fts5(content)")
        return True
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()


def cmd_doctor(config: BrainOSConfig) -> dict[str, Any]:
    paths = BrainPaths(config)
    state_target = paths.abs(".javis")
    try:
        state_target.relative_to(config.brain_root)
        state_inside = True
    except ValueError:
        state_inside = False

    scan_cfg = config.core.get("scan") or {}
    checks = {
        "brain_root_exists": config.brain_root.is_dir(),
        "config_loaded": True,
        "dry_run": config.dry_run,
        "state_path_inside_brain": state_inside,
        "sqlite_version": sqlite3.sqlite_version,
        "sqlite_fts5": _check_fts5(),
        "schema_supported": SCHEMA_VERSION,
        "database_exists": config.db_path.is_file(),
        "scan_follow_symlinks": bool(scan_cfg.get("follow_symlinks", False)),
        "scan_extensions": list(scan_cfg.get("extensions") or [".md", ".markdown"]),
    }

    ok = bool(
        checks["brain_root_exists"]
        and checks["config_loaded"]
        and checks["state_path_inside_brain"]
        and checks["scan_follow_symlinks"] is False
    )
    return {
        "ok": ok,
        "action": "doctor",
        "brain_root": str(config.brain_root),
        "checks": checks,
        "note": "Doctor là read-only; không khởi tạo DB nếu DB chưa tồn tại.",
    }


def cmd_config(config: BrainOSConfig) -> dict[str, Any]:
    return {"ok": True, "action": "config", "config": config.summary()}


def cmd_fingerprint(config: BrainOSConfig, value: str) -> dict[str, Any]:
    paths = BrainPaths(config)
    fp = Path(value).expanduser()
    if not fp.is_absolute():
        fp = paths.abs(value)
    result = fingerprint_file(fp, brain_root=config.brain_root)
    return {
        "ok": True,
        "action": "fingerprint",
        "fingerprint": result.to_dict(),
    }


def cmd_scan(config: BrainOSConfig, *, full_hash: bool = False) -> dict[str, Any]:
    report = reconcile_brain(config, full_hash=full_hash)
    return {
        "ok": report.ok,
        "action": "reconcile" if full_hash else "scan",
        "dry_run": config.dry_run,
        "writes_user_files": False,
        "derived_state_only": True,
        "report": report.to_dict(),
    }


def cmd_events(
    config: BrainOSConfig,
    *,
    limit: int = 50,
    unhandled_only: bool = False,
) -> dict[str, Any]:
    if not config.db_path.is_file():
        return {
            "ok": True,
            "action": "events",
            "initialized": False,
            "events": [],
        }
    with BrainIndex(config.db_path) as index:
        events = list_events(
            index,
            limit=limit,
            unhandled_only=unhandled_only,
        )
    return {
        "ok": True,
        "action": "events",
        "initialized": True,
        "events": events,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Brain OS V1 deterministic CLI")
    parser.add_argument("--brain-root", help="Override Brain/vault root.")
    parser.add_argument("--compact", action="store_true", help="Compact JSON output.")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create/open rebuildable Brain OS state DB.")
    sub.add_parser("status", help="Show config, DB and latest scan status.")
    sub.add_parser("doctor", help="Read-only runtime/config checks.")
    sub.add_parser("config", help="Print normalized config summary.")

    fingerprint = sub.add_parser("fingerprint", help="Hash one file without writing it.")
    fingerprint.add_argument("path")

    scan = sub.add_parser(
        "scan",
        help="Fast reconcile using size/mtime cache; writes only .javis derived state.",
    )
    scan.add_argument(
        "--full-hash",
        action="store_true",
        help="Hash every eligible file instead of reusing unchanged fingerprints.",
    )

    sub.add_parser(
        "reconcile",
        help="Full-hash reconcile for sparse integrity checking.",
    )

    events = sub.add_parser("events", help="Show filesystem change journal.")
    events.add_argument("--limit", type=int, default=50)
    events.add_argument("--unhandled", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        root = resolve_root(args.brain_root)
        config = BrainOSConfig.load(root)

        if args.command == "init":
            report = cmd_init(config)
        elif args.command == "status":
            report = cmd_status(config)
        elif args.command == "doctor":
            report = cmd_doctor(config)
        elif args.command == "config":
            report = cmd_config(config)
        elif args.command == "fingerprint":
            report = cmd_fingerprint(config, args.path)
        elif args.command == "scan":
            report = cmd_scan(config, full_hash=bool(args.full_hash))
        elif args.command == "reconcile":
            report = cmd_scan(config, full_hash=True)
        elif args.command == "events":
            report = cmd_events(
                config,
                limit=args.limit,
                unhandled_only=bool(args.unhandled),
            )
        else:  # pragma: no cover
            parser.error(f"Unknown command: {args.command}")
            return 2

        _json(report, compact=args.compact)
        return 0 if report.get("ok") else 2
    except (BrainOSConfigError, BrainIndexError, OSError, ValueError) as exc:
        _json(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            },
            compact=getattr(args, "compact", False),
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
