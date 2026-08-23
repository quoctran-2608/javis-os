"""Deterministic change guard for Second Brain managed Markdown.

This module deliberately contains no LLM calls. It only:
- scans explicitly allowed user roots;
- ignores Javis/system areas;
- computes a canonical SHA-256 from user-meaningful content;
- marks a note after a successful ingest.

The goal is simple: unchanged notes must be rejected before any AI work starts.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

# Metadata written by Javis during ingestion. These keys must not make a note look
# changed merely because Javis recorded its own bookkeeping.
_OPERATIONAL_KEYS = {
    "status",
    "processed_at",
    "wiki_links",
    "javis_last_checked",
    "javis_last_ingested_at",
    "javis_last_ingested_hash",
    "javis_managed",
}

# Never recurse into these areas while scanning user knowledge roots. Matching is
# case-insensitive and applies to every path segment, not just the vault root.
_SYSTEM_SEGMENTS = {
    ".git",
    ".obsidian",
    ".javis",
    "javis",
    "system",
    "skills",
    "wiki",
    "workflows",
    "agents",
    "memory",
    "plugins",
    "attachments",
}

# Safe defaults cover the source folder names currently used by Javis while still
# allowing callers/Brain config to provide their own roots explicitly.
_DEFAULT_ROOT_CANDIDATES = (
    "sources",
    "Sources",
    "06 - Sources",
    "notes",
    "Notes",
)


def _parse(text: str) -> tuple[dict[str, Any], str, bool]:
    """Return (frontmatter, body, had_frontmatter). Invalid YAML is treated as no FM.

    We intentionally do not try to rewrite malformed user frontmatter here. In that
    case the entire file is hashed as user body so the guard remains fail-safe.
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    m = _FM_RE.match(normalized)
    if not m:
        return {}, normalized, False
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except Exception:
        return {}, normalized, False
    if not isinstance(data, dict):
        return {}, normalized, False
    return data, m.group(2), True


def _semantic_frontmatter(frontmatter: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in frontmatter.items():
        skey = str(key)
        if skey.startswith("javis_") or skey in _OPERATIONAL_KEYS:
            continue
        out[skey] = value
    return out


def canonical_payload(text: str) -> bytes:
    """Stable representation used for hashing.

    User body + semantic user metadata participate in the hash. Javis operational
    metadata does not. Dict keys are sorted to avoid false changes caused by YAML key
    ordering, while list order is preserved because it may be meaningful to the user.
    """
    fm, body, had_fm = _parse(text)
    if not had_fm:
        return body.encode("utf-8")
    semantic = _semantic_frontmatter(fm)
    stable_fm = json.dumps(
        semantic,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return (stable_fm + "\n---BODY---\n" + body).encode("utf-8")


def canonical_hash_text(text: str) -> str:
    return hashlib.sha256(canonical_payload(text)).hexdigest()


def canonical_hash_file(path: Path) -> str:
    return canonical_hash_text(Path(path).read_text(encoding="utf-8"))


def _safe_relative_root(vault_root: Path, raw: str) -> Optional[Path]:
    raw = str(raw or "").strip().replace("\\", "/")
    if not raw:
        return None
    p = (vault_root / raw).resolve()
    try:
        p.relative_to(vault_root.resolve())
    except ValueError:
        return None
    return p


def discover_roots(vault_root: str, roots: Optional[Iterable[str]] = None) -> list[Path]:
    root = Path(vault_root).resolve()
    raw_roots = list(roots or [])
    if not raw_roots:
        raw_roots = [name for name in _DEFAULT_ROOT_CANDIDATES if (root / name).is_dir()]
    out: list[Path] = []
    seen: set[str] = set()
    for raw in raw_roots:
        p = _safe_relative_root(root, str(raw))
        if p is None or not p.is_dir():
            continue
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _is_system_path(vault_root: Path, path: Path) -> bool:
    try:
        rel = path.resolve().relative_to(vault_root.resolve())
    except ValueError:
        return True
    return any(part.casefold() in _SYSTEM_SEGMENTS for part in rel.parts[:-1])


def _managed_info(path: Path) -> tuple[bool, str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False, "", ""
    fm, _body, had_fm = _parse(text)
    if not had_fm or fm.get("javis_managed") is not True:
        return False, "", ""
    stored = str(fm.get("javis_last_ingested_hash") or "").strip()
    current = canonical_hash_text(text)
    return True, stored, current


def scan_managed_notes(vault_root: str, roots: Optional[Iterable[str]] = None) -> dict[str, Any]:
    """Scan only configured user roots and return managed notes whose canonical hash changed."""
    root = Path(vault_root).resolve()
    resolved_roots = discover_roots(str(root), roots)
    changed: list[dict[str, str]] = []
    checked = 0
    managed = 0
    errors: list[str] = []

    for source_root in resolved_roots:
        for path in source_root.rglob("*.md"):
            if _is_system_path(root, path):
                continue
            checked += 1
            try:
                is_managed, stored, current = _managed_info(path)
            except Exception as e:
                errors.append(f"{path}: {type(e).__name__}: {e}")
                continue
            if not is_managed:
                continue
            managed += 1
            if not stored or stored != current:
                try:
                    rel = path.resolve().relative_to(root).as_posix()
                except ValueError:
                    continue
                changed.append({
                    "path": rel,
                    "stored_hash": stored,
                    "current_hash": current,
                })

    return {
        "roots": [p.relative_to(root).as_posix() for p in resolved_roots],
        "checked_markdown": checked,
        "managed_markdown": managed,
        "changed": changed,
        "changed_count": len(changed),
        "errors": errors,
    }


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def mark_ingested(vault_root: str, relative_path: str) -> dict[str, str]:
    """Persist javis_managed + canonical last-ingested hash after successful ingestion.

    The hash is computed *before* adding/updating operational keys; because those keys are
    excluded from canonicalization, an immediate second scan is guaranteed to compare the
    same semantic payload.
    """
    root = Path(vault_root).resolve()
    path = _safe_relative_root(root, relative_path)
    if path is None or not path.is_file() or path.suffix.lower() != ".md":
        raise ValueError("Markdown path không hợp lệ hoặc nằm ngoài vault")
    if _is_system_path(root, path):
        raise ValueError("Từ chối quản lý Markdown trong vùng hệ thống")

    text = path.read_text(encoding="utf-8")
    digest = canonical_hash_text(text)
    fm, body, had_fm = _parse(text)
    if not had_fm:
        fm = {}
        body = text.replace("\r\n", "\n").replace("\r", "\n")

    fm["javis_managed"] = True
    fm["javis_last_ingested_hash"] = digest
    rendered = yaml.safe_dump(
        fm,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=1000,
    ).strip()
    new_text = f"---\n{rendered}\n---\n\n{body}"
    _atomic_write(path, new_text)
    return {
        "path": path.relative_to(root).as_posix(),
        "hash": digest,
    }
