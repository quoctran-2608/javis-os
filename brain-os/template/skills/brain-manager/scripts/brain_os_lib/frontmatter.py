from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from .models import FrontmatterUpdateResult


class FrontmatterError(RuntimeError):
    pass


@dataclass(frozen=True)
class MarkdownDocument:
    metadata: dict[str, Any]
    body: str
    newline: str = "\n"
    had_frontmatter: bool = False
    had_bom: bool = False


def _require_yaml() -> None:
    if yaml is None:
        raise FrontmatterError("Brain OS cần PyYAML (`yaml`) để xử lý frontmatter.")


def _detect_newline(text: str) -> str:
    first_lf = text.find("\n")
    if first_lf == -1:
        return "\r\n" if "\r" in text else "\n"
    return "\r\n" if first_lf > 0 and text[first_lf - 1] == "\r" else "\n"


def parse_markdown_text(text: str) -> MarkdownDocument:
    _require_yaml()
    had_bom = text.startswith("\ufeff")
    if had_bom:
        text = text[1:]

    newline = _detect_newline(text)
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return MarkdownDocument(
            metadata={},
            body=text,
            newline=newline,
            had_frontmatter=False,
            had_bom=had_bom,
        )

    closing = None
    for idx in range(1, len(lines)):
        if lines[idx].rstrip("\r\n") == "---":
            closing = idx
            break

    if closing is None:
        return MarkdownDocument(
            metadata={},
            body=text,
            newline=newline,
            had_frontmatter=False,
            had_bom=had_bom,
        )

    yaml_text = "".join(lines[1:closing])
    try:
        metadata = yaml.safe_load(yaml_text) or {}
    except Exception as exc:
        raise FrontmatterError(
            f"Frontmatter YAML không hợp lệ: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(metadata, dict):
        raise FrontmatterError("Frontmatter YAML phải là mapping.")

    body = "".join(lines[closing + 1 :])
    return MarkdownDocument(
        metadata=metadata,
        body=body,
        newline=newline,
        had_frontmatter=True,
        had_bom=had_bom,
    )


def load_markdown(path: Path | str) -> MarkdownDocument:
    fp = Path(path)
    try:
        raw = fp.read_bytes()
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FrontmatterError(f"Markdown không phải UTF-8: {fp}") from exc
    return parse_markdown_text(text)


def _dump_metadata(metadata: dict[str, Any], newline: str) -> str:
    _require_yaml()
    dumped = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=1000,
    ).strip()
    if "\n" != newline:
        dumped = dumped.replace("\n", newline)
    return dumped


def render_markdown(
    document: MarkdownDocument,
    *,
    metadata: dict[str, Any] | None = None,
) -> str:
    meta = document.metadata if metadata is None else metadata
    newline = document.newline or "\n"
    prefix = "\ufeff" if document.had_bom else ""

    if not meta and not document.had_frontmatter:
        return prefix + document.body

    dumped = _dump_metadata(meta, newline)
    rendered = (
        f"---{newline}"
        f"{dumped}{newline}"
        f"---{newline}"
        f"{document.body}"
    )
    return prefix + rendered


def atomic_write_text(path: Path, text: str) -> None:
    """Atomic same-directory replacement, preserving file mode where possible."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    old_mode = None
    if path.exists():
        try:
            old_mode = path.stat().st_mode & 0o777
        except OSError:
            old_mode = None

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.brain-os-",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if old_mode is not None:
            os.chmod(tmp, old_mode)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def update_frontmatter(
    path: Path | str,
    *,
    updates: dict[str, Any] | None = None,
    remove: Iterable[str] = (),
    dry_run: bool = True,
) -> FrontmatterUpdateResult:
    fp = Path(path)
    document = load_markdown(fp)
    before = dict(document.metadata)
    after = dict(before)

    for key, value in (updates or {}).items():
        after[str(key)] = value
    for key in remove:
        after.pop(str(key), None)

    if after == before:
        return FrontmatterUpdateResult(
            changed=False,
            dry_run=bool(dry_run),
            path=str(fp),
            before=before,
            after=after,
        )

    if not dry_run:
        rendered = render_markdown(document, metadata=after)
        current = fp.read_text(encoding="utf-8")
        if rendered != current:
            atomic_write_text(fp, rendered)

    return FrontmatterUpdateResult(
        changed=True,
        dry_run=bool(dry_run),
        path=str(fp),
        before=before,
        after=after,
    )
