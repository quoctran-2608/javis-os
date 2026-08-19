#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from brain_os import resolve_root
from brain_os_lib.classifier import classify_brain
from brain_os_lib.config import BrainOSConfig, BrainOSConfigError
from brain_os_lib.frontmatter import FrontmatterError, update_frontmatter
from brain_os_lib.importer import MarkdownImportError, import_markdown
from brain_os_lib.originals import OriginalsError
from brain_os_lib.reconcile import reconcile_brain
from brain_os_lib.taxonomy import TaxonomyError, plan_brain_taxonomy


class NoteCaptureError(RuntimeError):
    """Fail-safe error for explicit Javis /notes capture."""


def _json(data: dict[str, Any], *, compact: bool = False) -> None:
    print(
        json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":") if compact else None,
            indent=None if compact else 2,
        )
    )


def _ascii_slug(text: str, *, max_words: int = 6) -> str:
    words = re.findall(r"[^\s]+", text.strip())[:max_words]
    seed = " ".join(words)
    normalized = unicodedata.normalize("NFKD", seed.casefold())
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug[:64].strip("-") or "quick-note"


def _title_from_body(body: str) -> str:
    for raw in body.splitlines():
        value = raw.strip().lstrip("#").strip()
        if value:
            return value[:120]
    return "Quick Note"


def _refresh(config: BrainOSConfig, working_path: str) -> None:
    reconcile_brain(config, full_hash=True)
    classify_brain(config, paths={working_path})
    plan_brain_taxonomy(config, paths={working_path})


def _staging_text(*, body: str, title: str, captured_at: str) -> str:
    metadata = {
        "title": title,
        "source_kind": "own-note",
        "captured_at": captured_at,
    }
    dumped = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=1000,
    ).strip()
    # The user message starts immediately after the frontmatter delimiter and is
    # preserved byte-for-byte as the Markdown body by import_markdown().
    return f"---\n{dumped}\n---\n{body}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Capture one user-authored quick note as a Brain OS managed Living Note. "
            "The note body is read from stdin and preserved verbatim."
        )
    )
    parser.add_argument("--brain-root", help="Override Brain/vault root.")
    parser.add_argument("--title", default="", help="Optional note title; body stays unchanged.")
    parser.add_argument(
        "--category",
        default="",
        help="Optional existing living_notes taxonomy category id/alias.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write immutable snapshot + managed Living Note. Default is preview only.",
    )
    parser.add_argument("--compact", action="store_true", help="Compact JSON output.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        body = sys.stdin.read()
        if not body or not body.strip():
            raise NoteCaptureError("Không có nội dung note trên stdin; từ chối tạo note rỗng.")

        config = BrainOSConfig.load(resolve_root(args.brain_root))
        now = datetime.now().astimezone().replace(microsecond=0)
        captured_at = now.isoformat()
        title = str(args.title or "").strip() or _title_from_body(body)
        filename = (
            f"note-{now.strftime('%Y-%m-%d-%H%M')}-"
            f"{_ascii_slug(body)}.md"
        )
        staging = _staging_text(body=body, title=title, captured_at=captured_at)

        with tempfile.TemporaryDirectory(prefix="brain-os-note-capture-") as temp_dir:
            source = Path(temp_dir) / filename
            source.write_text(staging, encoding="utf-8", newline="")
            result = import_markdown(
                config,
                source,
                document_type="living_note",
                category_id=str(args.category or ""),
                dry_run=not bool(args.apply),
            )

        if args.apply and not result.reused_working_copy:
            update_frontmatter(
                config.brain_root / result.working_path,
                updates={"origin": "javis_notes_capture"},
                dry_run=False,
            )
            _refresh(config, result.working_path)

        payload = {
            "ok": result.ok,
            "action": "capture-note",
            "dry_run": result.dry_run,
            "uses_ai": False,
            "executes_javis_ingest": False,
            "writes_wiki": False,
            "writes_memory": False,
            "document_type": "living_note",
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "body_bytes": len(body.encode("utf-8")),
            "result": result.to_dict(),
        }
        _json(payload, compact=bool(args.compact))
        return 0 if result.ok else 2
    except (
        BrainOSConfigError,
        FrontmatterError,
        MarkdownImportError,
        NoteCaptureError,
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
