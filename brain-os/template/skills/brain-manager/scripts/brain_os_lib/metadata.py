from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .frontmatter import FrontmatterError, parse_markdown_text


@dataclass(frozen=True)
class MarkdownProbe:
    """Bounded, read-only view of Markdown metadata and a small text excerpt.

    Stage 5 deliberately avoids loading an entire long Living Note merely to
    choose taxonomy. `body_excerpt` is capped by `max_text_probe_bytes`; YAML is
    parsed only when the closing frontmatter marker appears within
    `max_frontmatter_bytes`.
    """

    metadata: dict[str, Any]
    raw_tags: tuple[str, ...]
    title: str
    headings: tuple[str, ...]
    body_excerpt: str
    truncated: bool
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["raw_tags"] = list(self.raw_tags)
        data["headings"] = list(self.headings)
        data["warnings"] = list(self.warnings)
        return data


def _clean_tag(value: Any) -> str:
    return str(value or "").strip().lstrip("#").strip()


def _flatten_tag_values(value: Any) -> Iterable[str]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        flattened: list[str] = []
        for child in value:
            flattened.extend(_flatten_tag_values(child))
        return tuple(flattened)
    if isinstance(value, dict):
        return ()

    raw = str(value).strip()
    if not raw:
        return ()

    # YAML `tags: a, b` is common in exported notes. For a whitespace-only
    # string we split only when every token is an explicit #tag; otherwise the
    # whole value may legitimately be a legacy tag containing spaces.
    if "," in raw:
        return tuple(part.strip() for part in raw.split(",") if part.strip())
    parts = raw.split()
    if len(parts) > 1 and all(part.startswith("#") for part in parts):
        return tuple(parts)
    return (raw,)


def extract_tag_values(
    metadata: dict[str, Any],
    *,
    fields: Iterable[str] = ("tags", "tag"),
) -> tuple[str, ...]:
    """Extract frontmatter tags without mutating or canonicalizing them."""

    seen: set[str] = set()
    result: list[str] = []
    for field in fields:
        if field not in metadata:
            continue
        for value in _flatten_tag_values(metadata.get(field)):
            cleaned = _clean_tag(value)
            key = cleaned.casefold()
            if cleaned and key not in seen:
                seen.add(key)
                result.append(cleaned)
    return tuple(result)


def _parse_header_bytes(raw: bytes) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        warnings.append(f"frontmatter_not_utf8:{exc}")
        return {}, warnings

    try:
        document = parse_markdown_text(text)
    except FrontmatterError as exc:
        warnings.append(f"frontmatter_invalid:{exc}")
        return {}, warnings

    if not document.had_frontmatter:
        warnings.append("frontmatter_unclosed")
        return {}, warnings
    return dict(document.metadata or {}), warnings


def _decode_excerpt(raw: bytes, *, label: str) -> tuple[str, list[str]]:
    try:
        return raw.decode("utf-8"), []
    except UnicodeDecodeError as exc:
        # Taxonomy must not abort the whole batch because one Markdown file has
        # a broken byte. Replacement is safe here because this is only evidence;
        # the original file remains untouched and the warning is persisted.
        return raw.decode("utf-8", errors="replace"), [f"{label}_not_utf8:{exc}"]


def _extract_headings(body: str, *, limit: int = 32) -> tuple[str, ...]:
    headings: list[str] = []
    for line in body.splitlines():
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        if not match:
            continue
        value = match.group(1).strip().rstrip("#").strip()
        if value:
            headings.append(value)
        if len(headings) >= limit:
            break
    return tuple(headings)


def read_markdown_probe(
    path: Path | str,
    *,
    max_frontmatter_bytes: int = 65536,
    max_text_probe_bytes: int = 131072,
    tag_fields: Iterable[str] = ("tags", "tag"),
) -> MarkdownProbe:
    """Read bounded metadata + text evidence from one Markdown file.

    No writes are performed. A huge/unclosed frontmatter block is not treated as
    prose evidence because doing so would make taxonomy depend on YAML noise.
    """

    fp = Path(path)
    if max_frontmatter_bytes < 1024:
        raise ValueError("max_frontmatter_bytes phải >= 1024")
    if max_text_probe_bytes < 1024:
        raise ValueError("max_text_probe_bytes phải >= 1024")

    metadata: dict[str, Any] = {}
    warnings: list[str] = []
    body_bytes = b""
    truncated = False

    with fp.open("rb") as fh:
        first = fh.readline()
        if not first:
            body_bytes = b""
        else:
            marker = first[3:] if first.startswith(b"\xef\xbb\xbf") else first
            has_frontmatter = marker.rstrip(b"\r\n") == b"---"

            if has_frontmatter:
                header = [first]
                total = len(first)
                closed = False
                while True:
                    line = fh.readline()
                    if not line:
                        warnings.append("frontmatter_unclosed")
                        break
                    total += len(line)
                    if total > max_frontmatter_bytes:
                        warnings.append(
                            f"frontmatter_too_large:{total}>{max_frontmatter_bytes}"
                        )
                        break
                    header.append(line)
                    if line.rstrip(b"\r\n") == b"---":
                        closed = True
                        break

                if closed:
                    parsed, parse_warnings = _parse_header_bytes(b"".join(header))
                    metadata = parsed
                    warnings.extend(parse_warnings)
                    body_bytes = fh.read(max_text_probe_bytes + 1)
                    if len(body_bytes) > max_text_probe_bytes:
                        body_bytes = body_bytes[:max_text_probe_bytes]
                        truncated = True
                else:
                    # Intentionally do not scan the oversized/unclosed YAML-like
                    # prefix as semantic body text.
                    body_bytes = b""
            else:
                fh.seek(0)
                body_bytes = fh.read(max_text_probe_bytes + 1)
                if len(body_bytes) > max_text_probe_bytes:
                    body_bytes = body_bytes[:max_text_probe_bytes]
                    truncated = True

    body, body_warnings = _decode_excerpt(body_bytes, label="body_probe")
    warnings.extend(body_warnings)

    title_value = metadata.get("title")
    title = str(title_value).strip() if isinstance(title_value, (str, int, float)) else ""
    if not title:
        title = fp.stem

    return MarkdownProbe(
        metadata=metadata,
        raw_tags=extract_tag_values(metadata, fields=tag_fields),
        title=title,
        headings=_extract_headings(body),
        body_excerpt=body,
        truncated=truncated,
        warnings=tuple(warnings),
    )
