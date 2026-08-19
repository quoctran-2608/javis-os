#!/usr/bin/env python3
"""Read-only preflight for a controlled real-vault Brain OS pilot."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

from brain_os import resolve_root
from brain_os_lib.config import BrainOSConfig, BrainOSConfigError
from brain_os_lib.db import BrainIndex, BrainIndexError
from brain_os_lib.frontmatter import FrontmatterError, load_markdown
from brain_os_lib.recovery import BrainRecoveryError
from brain_recovery import ControlledRecoveryError, audit_recovery


GOVERNED_SKILLS = ("ingest-source", "notes", "query-wiki", "lint-wiki")
REQUIRED_SCRIPTS = (
    "brain_os.py",
    "brain_manager.py",
    "brain_watch.py",
    "brain_identity.py",
    "brain_recovery.py",
    "record_ingest.py",
)


class PilotReadinessError(RuntimeError):
    pass


def _emit(data: dict[str, Any], *, compact: bool) -> None:
    if compact:
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def _compatibility(config: BrainOSConfig) -> dict[str, Any]:
    root = config.brain_root
    missing: list[str] = []
    mismatched_skills: list[str] = []

    contract = root / "System" / "BrainOS" / "javis-integration.md"
    if not contract.is_file():
        missing.append("System/BrainOS/javis-integration.md")

    requirements = root / "requirements-brain-os.txt"
    if not requirements.is_file():
        missing.append("requirements-brain-os.txt")

    scripts = root / "skills" / "brain-manager" / "scripts"
    for name in REQUIRED_SCRIPTS:
        if not (scripts / name).is_file():
            missing.append(f"skills/brain-manager/scripts/{name}")

    for slug in GOVERNED_SKILLS:
        canonical = root / "skills" / slug / "SKILL.md"
        mirror = root / ".claude" / "skills" / slug / "SKILL.md"
        if not canonical.is_file():
            missing.append(f"skills/{slug}/SKILL.md")
            continue
        if not mirror.is_file():
            missing.append(f".claude/skills/{slug}/SKILL.md")
            continue
        if canonical.read_bytes() != mirror.read_bytes():
            mismatched_skills.append(slug)

    return {
        "ok": not missing and not mismatched_skills,
        "missing": missing,
        "mismatched_skills": mismatched_skills,
        "governed_skills": list(GOVERNED_SKILLS),
    }


def _loop_state(config: BrainOSConfig) -> dict[str, Any]:
    path = config.brain_root / "Javis" / "loops" / "brain-watch.md"
    if not path.is_file():
        return {
            "exists": False,
            "enabled": None,
            "path": "Javis/loops/brain-watch.md",
            "error": "missing",
        }
    try:
        metadata = load_markdown(path).metadata
    except FrontmatterError as exc:
        return {
            "exists": True,
            "enabled": None,
            "path": "Javis/loops/brain-watch.md",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "exists": True,
        "enabled": bool(metadata.get("enabled", False)),
        "path": "Javis/loops/brain-watch.md",
        "interval_min": metadata.get("interval_min"),
        "notify": metadata.get("notify"),
        "error": "",
    }


def _runtime_dependencies() -> dict[str, bool]:
    return {
        "yaml": importlib.util.find_spec("yaml") is not None,
        "pypdf": importlib.util.find_spec("pypdf") is not None,
    }


def _operational_state(config: BrainOSConfig, *, db_integrity_ok: bool) -> dict[str, Any]:
    report = {
        "unhandled_events": 0,
        "jobs": {},
        "active_processing_jobs": 0,
        "pending_candidates": 0,
    }
    if not db_integrity_ok or not config.db_path.is_file():
        return report
    with BrainIndex(config.db_path) as index:
        conn = index._require()
        report["unhandled_events"] = int(
            conn.execute("SELECT COUNT(*) FROM events WHERE handled_at='' ").fetchone()[0]
        )
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status ORDER BY status"
        ).fetchall()
        report["jobs"] = {str(row["status"]): int(row["n"]) for row in rows}
        report["active_processing_jobs"] = int(report["jobs"].get("processing", 0))
        report["pending_candidates"] = int(
            conn.execute("SELECT COUNT(*) FROM candidates WHERE status='pending'").fetchone()[0]
        )
    return report


def pilot_check(config: BrainOSConfig) -> dict[str, Any]:
    recovery = audit_recovery(config)
    compatibility = _compatibility(config)
    loop = _loop_state(config)
    dependencies = _runtime_dependencies()
    operational = _operational_state(
        config,
        db_integrity_ok=bool(recovery["db"]["integrity_ok"]),
    )

    recovery_lock = config.path("state") / "recovery" / "recovery.lock"
    watch_lock = config.path("state") / "brain-watch.lock"
    blockers: list[str] = []

    if not config.dry_run:
        blockers.append("dry_run_must_be_true_for_initial_pilot")
    if not recovery["db"]["integrity_ok"]:
        blockers.append("database_integrity_not_ok")
    if not recovery["rebuild_ready"]:
        blockers.append("recovery_not_ready")
    if not compatibility["ok"]:
        blockers.append("deploy_compatibility_not_ok")
    if not loop["exists"] or loop["error"]:
        blockers.append("brain_watch_loop_invalid")
    elif loop["enabled"]:
        blockers.append("brain_watch_must_remain_disabled_for_initial_pilot")
    if not all(dependencies.values()):
        blockers.append("runtime_dependencies_missing")
    if recovery_lock.exists():
        blockers.append("recovery_lock_present")
    if watch_lock.exists():
        blockers.append("brain_watch_lock_present")
    if operational["active_processing_jobs"]:
        blockers.append("ai_jobs_currently_processing")

    blockers = list(dict.fromkeys(blockers))
    return {
        "ok": True,
        "action": "brain-os-pilot-check",
        "read_only": True,
        "pilot_ready": not blockers,
        "blockers": blockers,
        "config": {
            "mode": config.mode,
            "dry_run": config.dry_run,
            "brain_root": str(config.brain_root),
        },
        "recovery": recovery,
        "compatibility": compatibility,
        "brain_watch": loop,
        "runtime_dependencies": dependencies,
        "operational": operational,
        "locks": {
            "recovery": recovery_lock.exists(),
            "brain_watch": watch_lock.exists(),
        },
        "note": (
            "pilot_ready chỉ cho phép bắt đầu pilot ở dry-run với Brain Watch vẫn tắt; "
            "nó không tự bật automation hoặc thực thi INGEST."
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Brain OS real-vault pilot preflight")
    parser.add_argument("--brain-root", help="Override Brain/vault root.")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument(
        "command",
        choices=("check",),
        help="Run read-only pilot readiness checks.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        config = BrainOSConfig.load(resolve_root(args.brain_root))
        output = pilot_check(config)
        _emit(output, compact=bool(args.compact))
        return 0 if output["pilot_ready"] else 2
    except (
        PilotReadinessError,
        ControlledRecoveryError,
        BrainRecoveryError,
        BrainOSConfigError,
        BrainIndexError,
        FrontmatterError,
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
