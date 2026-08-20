from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .classifier import classify_brain
from .config import BrainOSConfig
from .frontmatter import load_markdown, update_frontmatter
from .importer import ImportResult, import_markdown
from .metadata import extract_tag_values
from .originals import sha256_file
from .reconcile import reconcile_brain
from .taxonomy import TaxonomyRegistry, plan_brain_taxonomy

MARKDOWN_SUFFIXES = {".md", ".markdown"}
MAX_ARCHIVE_ENTRIES = 20_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024


class AmplenoteMigrationError(RuntimeError):
    """Fail-safe error for Stage 7 Amplenote migration."""


@dataclass(frozen=True)
class AmplenoteNotePlan:
    source_entry: str
    title: str
    raw_tags: tuple[str, ...]
    canonical_tags: tuple[str, ...]
    legacy_tags: tuple[str, ...]
    document_type: str
    category_id: str
    working_path: str
    source_id: str
    reused_snapshot: bool
    reused_working_copy: bool

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("raw_tags", "canonical_tags", "legacy_tags"):
            data[key] = list(data[key])
        return data


@dataclass
class AmplenoteMigrationReport:
    ok: bool = True
    dry_run: bool = True
    source: str = ""
    source_kind: str = ""
    discovered_notes: int = 0
    migrated_notes: int = 0
    reused_notes: int = 0
    skipped_assets: int = 0
    archive_sha256: str = ""
    archive_snapshot_path: str = ""
    notes: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_zip_entry(name: str) -> str:
    raw = str(name or "").replace("\\", "/")
    if not raw or raw.startswith("/"):
        raise AmplenoteMigrationError(f"ZIP chứa path không an toàn: {name!r}")
    pure = PurePosixPath(raw)
    parts = pure.parts
    if any(part in ("", ".", "..") for part in parts):
        raise AmplenoteMigrationError(f"ZIP chứa path traversal: {name!r}")
    if parts and ":" in parts[0]:
        raise AmplenoteMigrationError(f"ZIP chứa absolute/drive path: {name!r}")
    return pure.as_posix()


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF)


def _validate_zip(path: Path) -> tuple[list[zipfile.ZipInfo], int]:
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise AmplenoteMigrationError(f"Amplenote ZIP không hợp lệ: {path}: {exc}") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise AmplenoteMigrationError(
                f"Amplenote ZIP có quá nhiều entries: {len(infos)}>{MAX_ARCHIVE_ENTRIES}"
            )
        total = 0
        normalized: set[str] = set()
        assets = 0
        for info in infos:
            safe = _safe_zip_entry(info.filename)
            key = safe.casefold()
            if key in normalized:
                raise AmplenoteMigrationError(
                    f"Amplenote ZIP có path trùng sau normalize: {safe}"
                )
            normalized.add(key)
            if info.flag_bits & 0x1:
                raise AmplenoteMigrationError(
                    f"Amplenote ZIP có entry mã hóa không hỗ trợ: {safe}"
                )
            if _is_zip_symlink(info):
                raise AmplenoteMigrationError(
                    f"Amplenote ZIP chứa symlink; migration từ chối fail-closed: {safe}"
                )
            total += int(info.file_size)
            if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise AmplenoteMigrationError(
                    "Amplenote ZIP vượt giới hạn uncompressed an toàn "
                    f"{MAX_ARCHIVE_UNCOMPRESSED_BYTES} bytes"
                )
            if not info.is_dir() and Path(safe).suffix.casefold() not in MARKDOWN_SUFFIXES:
                assets += 1
        return infos, assets


def _write_zip_markdown_to_staging(
    archive_path: Path,
    staging: Path,
    infos: Iterable[zipfile.ZipInfo],
) -> list[tuple[str, Path]]:
    notes: list[tuple[str, Path]] = []
    with zipfile.ZipFile(archive_path) as archive:
        for info in infos:
            if info.is_dir():
                continue
            entry = _safe_zip_entry(info.filename)
            if Path(entry).suffix.casefold() not in MARKDOWN_SUFFIXES:
                continue
            target = staging.joinpath(*PurePosixPath(entry).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                raw = archive.read(info)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise AmplenoteMigrationError(
                    f"Không đọc được Markdown từ Amplenote ZIP: {entry}: {exc}"
                ) from exc
            target.write_bytes(raw)
            notes.append((entry, target))
    return sorted(notes, key=lambda item: item[0].casefold())


def _directory_notes(source: Path) -> tuple[list[tuple[str, Path]], int]:
    notes: list[tuple[str, Path]] = []
    assets = 0
    root = source.resolve()
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirnames[:] = [
            d for d in sorted(dirnames)
            if not (current_path / d).is_symlink()
        ]
        for filename in sorted(filenames):
            path = current_path / filename
            if path.is_symlink():
                raise AmplenoteMigrationError(
                    f"Amplenote export directory chứa symlink file: {path}"
                )
            try:
                rel = path.resolve().relative_to(root).as_posix()
            except ValueError as exc:
                raise AmplenoteMigrationError(
                    f"Amplenote export path thoát khỏi root: {path}"
                ) from exc
            if path.suffix.casefold() in MARKDOWN_SUFFIXES:
                notes.append((rel, path))
            else:
                assets += 1
    return sorted(notes, key=lambda item: item[0].casefold()), assets


def _tag_plan(
    registry: TaxonomyRegistry, raw_tags: Iterable[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    canonical: list[str] = []
    legacy: list[str] = []
    seen_canonical: set[str] = set()
    seen_legacy: set[str] = set()
    for raw in raw_tags:
        value = str(raw or "").strip().lstrip("#").strip()
        if not value:
            continue
        resolved = registry.resolve_tag(value)
        if resolved:
            key = resolved.casefold()
            if key not in seen_canonical:
                seen_canonical.add(key)
                canonical.append(resolved)
            if value.casefold() != resolved.casefold():
                legacy_key = value.casefold()
                if legacy_key not in seen_legacy:
                    seen_legacy.add(legacy_key)
                    legacy.append(value)
        else:
            legacy_key = value.casefold()
            if legacy_key not in seen_legacy:
                seen_legacy.add(legacy_key)
                legacy.append(value)
    return tuple(canonical), tuple(legacy)


def _note_title(path: Path) -> tuple[str, tuple[str, ...]]:
    document = load_markdown(path)
    raw_title = document.metadata.get("title")
    title = (
        str(raw_title).strip()
        if isinstance(raw_title, (str, int, float)) and str(raw_title).strip()
        else path.stem
    )
    return title, extract_tag_values(document.metadata)


def _plan_from_import(
    registry: TaxonomyRegistry,
    entry: str,
    path: Path,
    result: ImportResult,
) -> AmplenoteNotePlan:
    title, raw_tags = _note_title(path)
    canonical, legacy = _tag_plan(registry, raw_tags)
    return AmplenoteNotePlan(
        source_entry=entry,
        title=title,
        raw_tags=raw_tags,
        canonical_tags=canonical,
        legacy_tags=legacy,
        document_type=result.document_type,
        category_id=result.category_id,
        working_path=result.working_path,
        source_id=result.source_id,
        reused_snapshot=result.reused_snapshot,
        reused_working_copy=result.reused_working_copy,
    )


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.brain-os-", suffix=".tmp", dir=str(path.parent)
    )
    tmp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _augment_note_manifest(
    manifest_path: Path, *, source_entry: str, archive_sha256: str
) -> None:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AmplenoteMigrationError(
            f"Không đọc được manifest provenance: {manifest_path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise AmplenoteMigrationError(
            f"Manifest provenance phải là object: {manifest_path}"
        )
    provenance = payload.get("migration_provenance")
    if provenance is None:
        provenance = []
    if not isinstance(provenance, list):
        raise AmplenoteMigrationError(
            f"migration_provenance hỏng trong manifest: {manifest_path}"
        )
    record = {"source_system": "amplenote", "source_entry": source_entry}
    if archive_sha256:
        record["export_sha256"] = archive_sha256
    if record not in provenance:
        provenance.append(record)
        payload["migration_provenance"] = provenance
        _atomic_write_json(manifest_path, payload)


def _preserve_archive(brain_root: Path, source_zip: Path) -> tuple[str, str]:
    archive_hash = sha256_file(source_zip)
    root = brain_root / ".javis" / "originals" / "amplenote-exports" / archive_hash
    target = root / "export.zip"
    manifest = root / "manifest.json"
    if target.exists():
        actual = sha256_file(target)
        if actual != archive_hash:
            raise AmplenoteMigrationError(
                f"Immutable Amplenote export snapshot bị thay đổi: {target}"
            )
    else:
        root.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=".export.zip.brain-os-", suffix=".tmp", dir=str(root)
        )
        tmp = Path(temp_name)
        try:
            with source_zip.open("rb") as src, os.fdopen(fd, "wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
            if sha256_file(tmp) != archive_hash:
                raise AmplenoteMigrationError(
                    f"Copy Amplenote export ZIP không khớp SHA-256: {source_zip}"
                )
            os.replace(tmp, target)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    if manifest.exists():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AmplenoteMigrationError(
                f"Manifest Amplenote export snapshot hỏng: {manifest}: {exc}"
            ) from exc
        if not isinstance(payload, dict) or str(payload.get("sha256") or "") != archive_hash:
            raise AmplenoteMigrationError(
                f"Manifest Amplenote export snapshot sai hash: {manifest}"
            )
    else:
        _atomic_write_json(
            manifest,
            {
                "schema_version": 1,
                "source_system": "amplenote",
                "sha256": archive_hash,
                "source_path": str(source_zip.resolve()),
                "snapshot_path": str(target),
                "preserved_at": _utc_now(),
            },
        )
    return archive_hash, str(target)


def _normalize_working_metadata(
    config: BrainOSConfig, plan: AmplenoteNotePlan
) -> None:
    updates: dict[str, Any] = {"origin": "amplenote_import"}
    if plan.raw_tags:
        updates["tags"] = list(plan.canonical_tags)
        updates["legacy_tags"] = list(plan.legacy_tags)
    update_frontmatter(
        config.brain_root / plan.working_path,
        updates=updates,
        dry_run=False,
    )


def _batch_refresh(config: BrainOSConfig, working_paths: set[str]) -> None:
    if not working_paths:
        return
    reconcile_brain(config, full_hash=True)
    classify_brain(config, paths=working_paths)
    plan_brain_taxonomy(config, paths=working_paths)


def migrate_amplenote(
    config: BrainOSConfig,
    source_path: Path | str,
    *,
    apply: bool = False,
) -> AmplenoteMigrationReport:
    """Migrate an Amplenote single Markdown note, export directory, or ZIP.

    Stage 7 is a deterministic adapter around Stage 6. It never calls AI or Javis
    INGEST/Wiki/Memory. Every note is preflighted in dry-run mode before any write.
    ZIP input is additionally preserved byte-for-byte under `.javis/originals/`.
    A standalone Markdown export gets the same per-note immutable snapshot,
    stable identity, tag canonicalization, and Amplenote provenance as batch input.
    """
    source = Path(source_path).expanduser().resolve()
    if not source.exists():
        raise AmplenoteMigrationError(f"Không tìm thấy Amplenote export: {source}")
    registry = TaxonomyRegistry.from_config(config)
    report = AmplenoteMigrationReport(dry_run=not apply, source=str(source))

    with tempfile.TemporaryDirectory(prefix="brain-os-amplenote-") as temp_dir:
        staging = Path(temp_dir)
        if source.is_file():
            suffix = source.suffix.casefold()
            if suffix == ".zip":
                archive_infos, assets = _validate_zip(source)
                note_sources = _write_zip_markdown_to_staging(
                    source, staging, archive_infos
                )
                report.source_kind = "zip"
                report.skipped_assets = assets
                report.archive_sha256 = sha256_file(source)
                report.archive_snapshot_path = str(
                    config.brain_root / ".javis" / "originals" / "amplenote-exports"
                    / report.archive_sha256 / "export.zip"
                )
            elif suffix in MARKDOWN_SUFFIXES:
                note_sources = [(source.name, source)]
                report.source_kind = "markdown"
                report.skipped_assets = 0
            else:
                raise AmplenoteMigrationError(
                    "Stage 7 nhận Amplenote single Markdown, export directory hoặc .zip, "
                    f"không nhận file {source.name!r}"
                )
        elif source.is_dir():
            note_sources, assets = _directory_notes(source)
            report.source_kind = "directory"
            report.skipped_assets = assets
        else:
            raise AmplenoteMigrationError(
                f"Amplenote export phải là Markdown, directory hoặc ZIP thường: {source}"
            )
        if not note_sources:
            raise AmplenoteMigrationError(
                f"Không tìm thấy Markdown note trong Amplenote export: {source}"
            )
        report.discovered_notes = len(note_sources)

        preview: list[tuple[str, Path, AmplenoteNotePlan]] = []
        for entry, note_path in note_sources:
            imported = import_markdown(config, note_path, dry_run=True)
            preview.append(
                (entry, note_path, _plan_from_import(registry, entry, note_path, imported))
            )

        if not apply:
            report.notes = [plan.to_dict() for _, _, plan in preview]
            report.migrated_notes = len(preview)
            return report

        if report.source_kind == "zip":
            report.archive_sha256, report.archive_snapshot_path = _preserve_archive(
                config.brain_root, source
            )

        applied_plans: list[AmplenoteNotePlan] = []
        refresh_paths: set[str] = set()
        for entry, note_path, _preview_plan in preview:
            imported = import_markdown(config, note_path, dry_run=False)
            plan = _plan_from_import(registry, entry, note_path, imported)
            _augment_note_manifest(
                config.brain_root / Path(imported.manifest_path),
                source_entry=entry,
                archive_sha256=report.archive_sha256,
            )
            if not imported.reused_working_copy:
                _normalize_working_metadata(config, plan)
            else:
                report.reused_notes += 1
            if (config.brain_root / imported.working_path).is_file():
                refresh_paths.add(imported.working_path)
            applied_plans.append(plan)

        _batch_refresh(config, refresh_paths)
        report.notes = [plan.to_dict() for plan in applied_plans]
        report.migrated_notes = len(applied_plans)
        if report.skipped_assets:
            report.warnings.append(
                f"{report.skipped_assets} non-Markdown export assets được giữ trong "
                "ZIP provenance nếu source là ZIP; Stage 7 chưa materialize chúng "
                "vào working Library."
            )
        return report
