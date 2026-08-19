#!/usr/bin/env python3
"""Stage 7 Amplenote migration adapter CLI."""

from __future__ import annotations

import argparse
import json
from typing import Any

from brain_os import resolve_root
from brain_os_lib.amplenote import AmplenoteMigrationError, migrate_amplenote
from brain_os_lib.config import BrainOSConfig, BrainOSConfigError
from brain_os_lib.frontmatter import FrontmatterError
from brain_os_lib.importer import MarkdownImportError
from brain_os_lib.originals import OriginalsError
from brain_os_lib.taxonomy import TaxonomyError


def _json(data: dict[str, Any], *, compact: bool = False) -> None:
    if compact:
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Brain OS Stage 7 — migrate one Amplenote Markdown note, "
            "an export directory, or a ZIP."
        )
    )
    parser.add_argument(
        "source",
        help="Standalone Amplenote Markdown file, export directory, or ZIP.",
    )
    parser.add_argument("--brain-root", help="Override Brain/vault root.")
    parser.add_argument("--compact", action="store_true", help="Compact JSON output.")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only (default); writes nothing.",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Preserve provenance + create/reuse editable working notes.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        root = resolve_root(args.brain_root)
        config = BrainOSConfig.load(root)
        report = migrate_amplenote(
            config,
            args.source,
            apply=bool(args.apply),
        )
        payload = {
            "ok": report.ok,
            "action": "import-amplenote",
            "dry_run": report.dry_run,
            "uses_ai": False,
            "executes_javis_ingest": False,
            "writes_wiki": False,
            "writes_memory": False,
            "report": report.to_dict(),
        }
        _json(payload, compact=bool(args.compact))
        return 0 if payload["ok"] else 2
    except (
        AmplenoteMigrationError,
        BrainOSConfigError,
        FrontmatterError,
        MarkdownImportError,
        OriginalsError,
        TaxonomyError,
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
