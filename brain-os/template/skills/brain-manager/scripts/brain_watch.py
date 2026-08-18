#!/usr/bin/env python3
"""Stage 9 deterministic Brain Watch cycle entrypoint.

Javis Loop owns scheduling. This script never schedules itself, never calls an LLM,
and never executes Javis INGEST/Wiki/Memory writes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from brain_os_lib.config import BrainOSConfig, BrainOSConfigError
from brain_os_lib.db import BrainIndexError
from brain_os_lib.taxonomy import TaxonomyError
from brain_os_lib.watch import BrainWatchError, fail_handoff_job, run_brain_watch_cycle


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Brain OS Stage 9 deterministic Brain Watch cycle"
    )
    parser.add_argument("--brain-root")
    parser.add_argument("--compact", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    cycle = sub.add_parser(
        "cycle",
        help="Run one scan/classify/taxonomy/AI-queue cycle; no scheduler and no model call.",
    )
    cycle.add_argument(
        "--max-ai-jobs",
        type=int,
        default=0,
        help="Override watch.max_ai_jobs_per_cycle for this cycle (1..20).",
    )
    cycle.add_argument(
        "--full-hash",
        action="store_true",
        help="Force full-hash reconcile instead of sparse schedule decision.",
    )

    fail = sub.add_parser(
        "fail",
        help="Mark a claimed AI handoff failed when Javis/model execution failed before apply.",
    )
    fail.add_argument("job_id")
    fail.add_argument("--error", required=True)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        config = BrainOSConfig.load(resolve_root(args.brain_root))
        if args.command == "cycle":
            report = run_brain_watch_cycle(
                config,
                max_ai_jobs=(args.max_ai_jobs or None),
                force_full_hash=(True if args.full_hash else None),
            )
            output = {
                "ok": report.ok,
                "action": "brain-watch-cycle",
                "scheduler_owner": "javis_loop",
                "uses_ai": False,
                "writes_user_files": False,
                "moves_user_files": False,
                "mutates_frontmatter": False,
                "executes_javis_ingest": False,
                "writes_wiki": False,
                "writes_memory": False,
                "report": report.to_dict(),
            }
        elif args.command == "fail":
            job = fail_handoff_job(config, args.job_id, error=args.error)
            output = {
                "ok": True,
                "action": "brain-watch-fail-handoff",
                "uses_ai": False,
                "writes_user_files": False,
                "job": job,
            }
        else:  # pragma: no cover
            parser.error(f"Unknown command: {args.command}")
            return 2
        emit(output, compact=bool(args.compact))
        return 0 if output.get("ok") else 2
    except (
        BrainWatchError,
        BrainOSConfigError,
        BrainIndexError,
        TaxonomyError,
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
