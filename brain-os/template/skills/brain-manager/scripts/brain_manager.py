#!/usr/bin/env python3
"""Stage 8 Brain Manager bridge.

Python remains deterministic: it queues unresolved work, validates structured AI
output, and records routing/candidates. The actual model call belongs to Javis.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from brain_os_lib.ai_manager import BrainManagerError, apply_ai_result, queue_ai_jobs
from brain_os_lib.candidates import list_candidates
from brain_os_lib.config import BrainOSConfig, BrainOSConfigError
from brain_os_lib.db import BrainIndex, BrainIndexError
from brain_os_lib.jobs import list_jobs
from brain_os_lib.taxonomy import TaxonomyError


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


def read_result(value: str) -> dict[str, Any]:
    if value == "-":
        text = sys.stdin.read()
    else:
        text = Path(value).expanduser().read_text(encoding="utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise BrainManagerError("AI result JSON phải là object")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Brain OS Stage 8 deterministic AI policy/routing bridge"
    )
    parser.add_argument("--brain-root")
    parser.add_argument("--compact", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    queue = sub.add_parser(
        "queue", help="Queue unresolved classification/taxonomy cases for Javis AI review."
    )
    queue.add_argument("--limit", type=int, default=100)
    queue.add_argument("--force", action="store_true")
    jobs = sub.add_parser("jobs", help="Read Brain Manager jobs.")
    jobs.add_argument(
        "--status", choices=("pending", "processing", "completed", "failed"),
        default="pending",
    )
    jobs.add_argument("--limit", type=int, default=100)
    apply_cmd = sub.add_parser(
        "apply", help="Validate and apply one structured AI result to derived state only."
    )
    apply_cmd.add_argument("result", help="Path to JSON result, or '-' for stdin.")
    candidates = sub.add_parser(
        "candidates", help="Read candidate records created by Brain Manager."
    )
    candidates.add_argument(
        "--kind", choices=("wiki", "memory", "ai_review"), default=""
    )
    candidates.add_argument(
        "--status", choices=("pending", "accepted", "rejected"), default="pending"
    )
    candidates.add_argument("--limit", type=int, default=100)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        config = BrainOSConfig.load(resolve_root(args.brain_root))
        if args.command == "queue":
            report = queue_ai_jobs(config, limit=args.limit, force=bool(args.force))
            output = {
                "ok": report.ok,
                "action": "brain-manager-queue",
                "uses_ai": False,
                "writes_user_files": False,
                "report": report.to_dict(),
            }
        elif args.command == "jobs":
            if not config.db_path.is_file():
                rows, initialized = [], False
            else:
                with BrainIndex(config.db_path) as index:
                    rows = list_jobs(index, status=str(args.status or ""), limit=args.limit)
                initialized = True
            output = {
                "ok": True,
                "action": "brain-manager-jobs",
                "initialized": initialized,
                "read_only": True,
                "jobs": rows,
            }
        elif args.command == "apply":
            result = apply_ai_result(config, read_result(args.result))
            output = {
                "ok": True,
                "action": "brain-manager-apply",
                "uses_ai": False,
                "writes_user_files": False,
                "executes_javis_ingest": False,
                "writes_wiki": False,
                "writes_memory": False,
                "result": result.to_dict(),
            }
        elif args.command == "candidates":
            if not config.db_path.is_file():
                rows, initialized = [], False
            else:
                with BrainIndex(config.db_path) as index:
                    rows = list_candidates(
                        index, kind=str(args.kind or ""),
                        status=str(args.status or ""), limit=args.limit,
                    )
                initialized = True
            output = {
                "ok": True,
                "action": "brain-manager-candidates",
                "initialized": initialized,
                "read_only": True,
                "candidates": rows,
            }
        else:  # pragma: no cover
            parser.error(f"Unknown command: {args.command}")
            return 2
        emit(output, compact=bool(args.compact))
        return 0 if output.get("ok") else 2
    except (
        BrainManagerError, BrainOSConfigError, BrainIndexError, TaxonomyError,
        OSError, ValueError, json.JSONDecodeError,
    ) as exc:
        emit(
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            compact=bool(getattr(args, "compact", False)),
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
