#!/usr/bin/env python3
"""Validate Brain OS V1 foundation config and taxonomy.

This is a repository/development tool. It is not copied into the user's Brain.

Exit codes:
  0: validation passed
  2: validation errors found
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*(?:/[a-z0-9][a-z0-9-]*)*$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def _yaml_module():
    for name in ("yaml", "fastyaml"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    raise RuntimeError("Cần PyYAML (`yaml`) hoặc `fastyaml` để validate Brain OS foundation.")


def load_yaml(path: Path) -> dict[str, Any]:
    module = _yaml_module()
    text = path.read_text(encoding="utf-8")
    data = module.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: YAML root phải là mapping")
    return data


def norm_alias(value: str) -> str:
    return str(value or "").strip().lstrip("#").casefold()


def valid_relative_path(value: str) -> bool:
    raw = str(value or "").strip()
    if not raw or raw.startswith(("/", "\\")) or "\\" in raw:
        return False
    parts = raw.split("/")
    return all(part not in ("", ".", "..") for part in parts)


def path_depth(value: str) -> int:
    return len([p for p in str(value).split("/") if p])


def require_schema(path: Path, data: dict[str, Any], report: ValidationReport) -> None:
    if data.get("schema_version") != SCHEMA_VERSION:
        report.error(
            f"{path}: schema_version phải là {SCHEMA_VERSION}, nhận {data.get('schema_version')!r}"
        )


def validate_config(path: Path, data: dict[str, Any], report: ValidationReport) -> None:
    require_schema(path, data, report)

    if data.get("mode") not in {"conservative", "balanced", "aggressive"}:
        report.error(f"{path}: mode không hợp lệ: {data.get('mode')!r}")

    # Foundation branch must remain fail-safe until later gates explicitly change this.
    if data.get("dry_run") is not True:
        report.error(f"{path}: Foundation bắt buộc dry_run: true")

    watch = data.get("watch") or {}
    if not isinstance(watch, dict):
        report.error(f"{path}: watch phải là mapping")
    else:
        try:
            if int(watch.get("interval_min", 0)) < 5:
                report.error(f"{path}: watch.interval_min phải >= 5")
        except (TypeError, ValueError):
            report.error(f"{path}: watch.interval_min phải là số nguyên")
        try:
            if int(watch.get("debounce_seconds", 0)) < 0:
                report.error(f"{path}: watch.debounce_seconds phải >= 0")
        except (TypeError, ValueError):
            report.error(f"{path}: watch.debounce_seconds phải là số nguyên")

    cls = data.get("classification") or {}
    try:
        auto = float(cls.get("auto_move_confidence"))
        cand = float(cls.get("candidate_confidence"))
        if not (0 <= cand <= auto <= 1):
            report.error(
                f"{path}: cần 0 <= candidate_confidence <= auto_move_confidence <= 1"
            )
    except (TypeError, ValueError):
        report.error(f"{path}: classification confidence phải là số")

    folders = data.get("folders") or {}
    if folders.get("allow_auto_move") is not False:
        report.error(f"{path}: Foundation bắt buộc folders.allow_auto_move: false")
    if folders.get("allow_auto_create") is not False:
        report.error(f"{path}: Foundation bắt buộc folders.allow_auto_create: false")

    tags = data.get("tags") or {}
    if tags.get("allow_auto_create") is not False:
        report.error(f"{path}: Foundation bắt buộc tags.allow_auto_create: false")

    paths = data.get("paths") or {}
    if not isinstance(paths, dict):
        report.error(f"{path}: paths phải là mapping")
    else:
        for key, value in paths.items():
            if not valid_relative_path(value):
                report.error(f"{path}: paths.{key} không phải relative path an toàn: {value!r}")

    zones = data.get("zones") or {}
    required_zones = {
        "00 - Dashboard",
        "01 - Daily Log",
        "02 - Weekly Log",
        "03 - Monthly Log",
        "04 - Future Log",
        "Notes",
        "sources",
        "Library",
        "wiki",
        "memory",
        ".javis",
    }
    missing_zones = sorted(required_zones - set(zones)) if isinstance(zones, dict) else sorted(required_zones)
    for zone in missing_zones:
        report.error(f"{path}: thiếu zone policy {zone!r}")

    if isinstance(zones, dict):
        for protected in ("00 - Dashboard", "wiki", ".javis"):
            z = zones.get(protected) or {}
            if z.get("ingest") != "never":
                report.error(f"{path}: zone {protected!r} bắt buộc ingest: never")

    ignore_paths = data.get("ignore_paths") or []
    if ".javis" not in ignore_paths:
        report.error(f"{path}: ignore_paths bắt buộc chứa .javis")

    override = data.get("manual_override") or {}
    if override.get("field") != "javis":
        report.error(f"{path}: manual_override.field phải là 'javis'")
    required_values = {"auto", "ignore", "index", "ingest", "wiki"}
    values = set(override.get("allowed_values") or [])
    if values != required_values:
        report.error(
            f"{path}: manual_override.allowed_values phải đúng {sorted(required_values)}"
        )


def _walk_categories(
    node: dict[str, Any],
    *,
    scope: str,
    parent_path: str,
    max_depth: int,
    ids: set[str],
    paths: set[tuple[str, str]],
    aliases: dict[tuple[str, str], str],
    report: ValidationReport,
) -> int:
    count = 0
    for key, raw in node.items():
        if not isinstance(raw, dict):
            report.error(f"folders.yml: {scope}.{key} phải là mapping")
            continue

        cid = str(raw.get("id") or "").strip()
        cpath = str(raw.get("path") or "").strip()
        label = str(raw.get("label") or "").strip()

        if not cid or not ID_RE.match(cid):
            report.error(f"folders.yml: category {scope}.{key} có id không hợp lệ: {cid!r}")
        elif cid in ids:
            report.error(f"folders.yml: category id bị trùng: {cid}")
        else:
            ids.add(cid)

        if not label:
            report.error(f"folders.yml: category {scope}.{key} thiếu label")

        if not valid_relative_path(cpath):
            report.error(f"folders.yml: category {cid or key} có path không an toàn: {cpath!r}")
        else:
            depth = path_depth(cpath)
            if depth > max_depth:
                report.error(
                    f"folders.yml: {cpath!r} sâu {depth} tầng, vượt max_depth={max_depth}"
                )
            if parent_path and not cpath.startswith(parent_path + "/"):
                report.error(
                    f"folders.yml: child path {cpath!r} không nằm dưới parent {parent_path!r}"
                )
            path_key = (scope, cpath.casefold())
            if path_key in paths:
                report.error(f"folders.yml: path bị trùng trong scope {scope}: {cpath}")
            else:
                paths.add(path_key)

        for alias in raw.get("aliases") or []:
            n = norm_alias(alias)
            if not n:
                report.error(f"folders.yml: alias rỗng ở category {cid or key}")
                continue
            alias_key = (scope, n)
            previous = aliases.get(alias_key)
            if previous and previous != cid:
                report.error(
                    f"folders.yml: alias {alias!r} trong scope {scope} trỏ cả {previous} và {cid}"
                )
            else:
                aliases[alias_key] = cid

        children = raw.get("children") or {}
        if children and not isinstance(children, dict):
            report.error(f"folders.yml: children của {cid or key} phải là mapping")
        elif children:
            count += _walk_categories(
                children,
                scope=scope,
                parent_path=cpath,
                max_depth=max_depth,
                ids=ids,
                paths=paths,
                aliases=aliases,
                report=report,
            )
        count += 1
    return count


def validate_folders(path: Path, data: dict[str, Any], report: ValidationReport) -> None:
    require_schema(path, data, report)
    policy = data.get("creation_policy") or {}
    try:
        max_depth = int(policy.get("max_depth", 3))
    except (TypeError, ValueError):
        max_depth = 3
        report.error(f"{path}: creation_policy.max_depth phải là số nguyên")

    if policy.get("default") != "propose_only":
        report.error(f"{path}: Foundation creation_policy.default phải là propose_only")

    scopes = data.get("scopes") or {}
    if not isinstance(scopes, dict) or not scopes:
        report.error(f"{path}: scopes phải là mapping không rỗng")
        return

    ids: set[str] = set()
    paths: set[tuple[str, str]] = set()
    aliases: dict[tuple[str, str], str] = {}
    count = 0

    for scope, spec in scopes.items():
        if not isinstance(spec, dict):
            report.error(f"{path}: scope {scope!r} phải là mapping")
            continue
        roots = spec.get("roots") or []
        if not isinstance(roots, list) or not roots:
            report.error(f"{path}: scope {scope!r} phải có roots không rỗng")
        for root in roots:
            if not valid_relative_path(root):
                report.error(f"{path}: scope {scope!r} có root không an toàn: {root!r}")
        fallback = spec.get("fallback")
        if not valid_relative_path(fallback):
            report.error(f"{path}: scope {scope!r} có fallback không an toàn: {fallback!r}")
        categories = spec.get("categories") or {}
        if not isinstance(categories, dict) or not categories:
            report.error(f"{path}: scope {scope!r} thiếu categories")
            continue
        count += _walk_categories(
            categories,
            scope=scope,
            parent_path="",
            max_depth=max_depth,
            ids=ids,
            paths=paths,
            aliases=aliases,
            report=report,
        )

    report.stats["folder_categories"] = count
    report.stats["folder_aliases"] = len(aliases)


def validate_tags(path: Path, data: dict[str, Any], report: ValidationReport) -> set[str]:
    require_schema(path, data, report)
    tags = data.get("canonical_tags") or {}
    if not isinstance(tags, dict) or not tags:
        report.error(f"{path}: canonical_tags phải là mapping không rỗng")
        return set()

    policy = data.get("policy") or {}
    try:
        max_depth = int(policy.get("max_depth", 3))
    except (TypeError, ValueError):
        max_depth = 3
        report.error(f"{path}: policy.max_depth phải là số nguyên")

    if policy.get("create_new") != "propose_only":
        report.error(f"{path}: Foundation policy.create_new phải là propose_only")

    ids: set[str] = set()
    canonical: set[str] = set()
    for tag, spec in tags.items():
        if not isinstance(tag, str) or not TAG_RE.match(tag):
            report.error(f"{path}: canonical tag không hợp lệ: {tag!r}")
            continue
        if path_depth(tag) > max_depth:
            report.error(f"{path}: tag {tag!r} vượt max_depth={max_depth}")
        if not isinstance(spec, dict):
            report.error(f"{path}: metadata của tag {tag!r} phải là mapping")
            continue
        tid = str(spec.get("id") or "").strip()
        if not tid or not ID_RE.match(tid):
            report.error(f"{path}: tag {tag!r} có id không hợp lệ: {tid!r}")
        elif tid in ids:
            report.error(f"{path}: tag id bị trùng: {tid}")
        else:
            ids.add(tid)
        canonical.add(tag)

    report.stats["canonical_tags"] = len(canonical)
    return canonical


def validate_tag_aliases(
    path: Path,
    data: dict[str, Any],
    canonical: set[str],
    report: ValidationReport,
) -> None:
    require_schema(path, data, report)
    aliases = data.get("aliases") or {}
    if not isinstance(aliases, dict):
        report.error(f"{path}: aliases phải là mapping")
        return

    normalized: dict[str, str] = {}
    for alias, target in aliases.items():
        n = norm_alias(alias)
        t = str(target or "").strip()
        if not n:
            report.error(f"{path}: alias rỗng")
            continue
        if t not in canonical:
            report.error(f"{path}: alias {alias!r} trỏ tới canonical tag không tồn tại: {t!r}")
        previous = normalized.get(n)
        if previous and previous != t:
            report.error(f"{path}: alias normalize {n!r} trỏ tới nhiều tag")
        normalized[n] = t
        if n == t.casefold():
            report.warn(f"{path}: alias {alias!r} giống hệt canonical target {t!r}, có thể bỏ")

    report.stats["tag_aliases"] = len(normalized)


def validate_required_files(template_root: Path, report: ValidationReport) -> None:
    required = [
        "System/BrainOS/config.yml",
        "System/Taxonomy/folders.yml",
        "System/Taxonomy/tags.yml",
        "System/Taxonomy/tag-aliases.yml",
        "skills/brain-manager/references/brain-policy.md",
        "skills/brain-manager/references/metadata-policy.md",
        "skills/brain-manager/references/folder-taxonomy-policy.md",
        "skills/brain-manager/references/tag-taxonomy-policy.md",
        "skills/brain-manager/references/ingest-policy.md",
        "skills/brain-manager/scripts/probe_runtime.py",
        "Javis/loops/brain-os-probe.md",
    ]
    for rel in required:
        if not (template_root / rel).is_file():
            report.error(f"Thiếu foundation/template file: {rel}")


def run(template_root: Path) -> ValidationReport:
    report = ValidationReport()
    validate_required_files(template_root, report)

    files = {
        "config": template_root / "System/BrainOS/config.yml",
        "folders": template_root / "System/Taxonomy/folders.yml",
        "tags": template_root / "System/Taxonomy/tags.yml",
        "aliases": template_root / "System/Taxonomy/tag-aliases.yml",
    }

    loaded: dict[str, dict[str, Any]] = {}
    for name, path in files.items():
        if not path.is_file():
            continue
        try:
            loaded[name] = load_yaml(path)
        except Exception as exc:
            report.error(f"{path}: không đọc được YAML: {type(exc).__name__}: {exc}")

    if "config" in loaded:
        validate_config(files["config"], loaded["config"], report)
    if "folders" in loaded:
        validate_folders(files["folders"], loaded["folders"], report)

    canonical: set[str] = set()
    if "tags" in loaded:
        canonical = validate_tags(files["tags"], loaded["tags"], report)
    if "aliases" in loaded:
        validate_tag_aliases(files["aliases"], loaded["aliases"], canonical, report)

    report.stats["template_root"] = str(template_root)
    return report


def parse_args() -> argparse.Namespace:
    here = Path(__file__).resolve()
    default_root = here.parents[1] / "template"
    parser = argparse.ArgumentParser(description="Validate Brain OS V1 foundation files.")
    parser.add_argument(
        "--template-root",
        default=str(default_root),
        help="Path to brain-os/template (defaults to repository layout).",
    )
    parser.add_argument("--compact", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.template_root).expanduser().resolve()
    report = run(root)
    payload = {
        "ok": report.ok,
        "errors": report.errors,
        "warnings": report.warnings,
        "stats": report.stats,
    }
    if args.compact:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
