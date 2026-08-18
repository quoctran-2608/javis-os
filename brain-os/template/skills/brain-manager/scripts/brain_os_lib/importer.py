from __future__ import annotations

import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .classifier import classify_brain
from .config import BrainOSConfig
from .db import BrainIndex
from .frontmatter import FrontmatterError, load_markdown, render_markdown
from .identity import new_source_id, valid_existing_id
from .models import DocumentType, ProcessingState
from .originals import OriginalSnapshot, OriginalStore, sha256_bytes
from .paths import safe_join
from .reconcile import reconcile_brain
from .taxonomy import TaxonomyRegistry, plan_brain_taxonomy


MARKDOWN_SUFFIXES = {".md", ".markdown"}


class MarkdownImportError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImportResult:
    ok: bool
    dry_run: bool
    source_id: str
    source_sha256: str
    document_type: str
    category_id: str
    working_path: str
    snapshot_path: str
    manifest_path: str
    reused_snapshot: bool
    reused_working_copy: bool
    indexed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _metadata_tags(metadata: dict[str, Any]) -> tuple[str, ...]:
    raw = metadata.get("tags")
    if raw is None:
        return ()
    if isinstance(raw, str):
        values: Iterable[Any] = (raw,)
    elif isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        return ()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip().lstrip("#")
        if text:
            result.append(text)
    return tuple(result)


def _type_from_text(value: Any) -> DocumentType | None:
    token = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "living_note": DocumentType.LIVING_NOTE,
        "livingnote": DocumentType.LIVING_NOTE,
        "note": DocumentType.LIVING_NOTE,
        "reference_source": DocumentType.REFERENCE_SOURCE,
        "referencesource": DocumentType.REFERENCE_SOURCE,
        "reference": DocumentType.REFERENCE_SOURCE,
        "source": DocumentType.REFERENCE_SOURCE,
    }
    return aliases.get(token)


def _matching_category_from_tags(
    registry: TaxonomyRegistry,
    scope: str,
    tags: Iterable[str],
):
    matches = {}
    for tag in tags:
        canonical = registry.resolve_tag(tag)
        if not canonical:
            continue
        for category in registry.categories_by_scope.get(scope, []):
            if canonical == category.slug_path or canonical.startswith(category.slug_path + "/"):
                matches[category.id] = category
    if not matches:
        return None
    deepest = max(category.depth for category in matches.values())
    finalists = [category for category in matches.values() if category.depth == deepest]
    if len(finalists) != 1:
        return None
    return finalists[0]


def _detect_type(
    registry: TaxonomyRegistry,
    source: Path,
    metadata: dict[str, Any],
    explicit: DocumentType | str | None,
) -> DocumentType:
    if explicit is not None:
        doc_type = explicit if isinstance(explicit, DocumentType) else _type_from_text(explicit)
        if doc_type not in {DocumentType.LIVING_NOTE, DocumentType.REFERENCE_SOURCE}:
            raise MarkdownImportError(
                "Stage 6 Markdown import chỉ nhận living_note hoặc reference_source."
            )
        return doc_type

    for field in ("javis_type", "type"):
        if field in metadata:
            parsed = _type_from_text(metadata.get(field))
            if parsed in {DocumentType.LIVING_NOTE, DocumentType.REFERENCE_SOURCE}:
                return parsed

    if registry.resolve_category("living_notes", source.stem) is not None:
        return DocumentType.LIVING_NOTE

    if _matching_category_from_tags(
        registry, "living_notes", _metadata_tags(metadata)
    ) is not None:
        return DocumentType.LIVING_NOTE

    return DocumentType.REFERENCE_SOURCE


def _select_category(
    registry: TaxonomyRegistry,
    *,
    document_type: DocumentType,
    source: Path,
    metadata: dict[str, Any],
    explicit_category_id: str = "",
):
    scope = "living_notes" if document_type == DocumentType.LIVING_NOTE else "knowledge"

    requested = str(explicit_category_id or "").strip()
    if not requested:
        requested = str(metadata.get("javis_category") or "").strip()
    if requested:
        category = registry.resolve_category(scope, requested)
        if category is None:
            raise MarkdownImportError(
                f"Category {requested!r} không tồn tại trong scope {scope!r}; "
                "Stage 6 không tự tạo category."
            )
        return scope, category

    by_title = registry.resolve_category(scope, source.stem)
    if by_title is not None:
        return scope, by_title

    by_tag = _matching_category_from_tags(registry, scope, _metadata_tags(metadata))
    if by_tag is not None:
        return scope, by_tag

    return scope, None


def _destination_rel(
    registry: TaxonomyRegistry,
    *,
    scope: str,
    category,
    filename: str,
) -> str:
    roots = registry.roots_for(scope)
    if not roots:
        raise MarkdownImportError(f"Taxonomy scope {scope!r} không có root.")
    root = "sources" if scope == "knowledge" and "sources" in roots else roots[0]
    directory = category.path if category is not None else registry.fallback_for(scope)
    return PurePosixPath(root, directory, filename).as_posix()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _find_indexed_working_path(config: BrainOSConfig, source_id: str) -> str:
    if not config.db_path.is_file():
        return ""
    with BrainIndex(config.db_path) as index:
        item = index.get_file(source_id)
        if item is None or item.state == ProcessingState.MISSING:
            return ""
        candidate = safe_join(config.brain_root, item.path)
        return item.path if candidate.is_file() else ""


def _existing_working_path(
    config: BrainOSConfig,
    snapshot: OriginalSnapshot,
) -> str:
    indexed = _find_indexed_working_path(config, snapshot.source_id)
    if indexed:
        return indexed
    if snapshot.working_path:
        candidate = safe_join(config.brain_root, snapshot.working_path)
        if candidate.is_file():
            return snapshot.working_path
    return ""


def _avoid_collision(
    config: BrainOSConfig,
    rel_path: str,
    source_id: str,
) -> str:
    target = safe_join(config.brain_root, rel_path)
    if not target.exists():
        return rel_path

    try:
        existing = load_markdown(target).metadata
    except FrontmatterError:
        existing = {}
    if str(existing.get("javis_id") or "").strip() == source_id:
        return rel_path

    pure = PurePosixPath(rel_path)
    suffix = pure.suffix
    stem = pure.name[: -len(suffix)] if suffix else pure.name
    alt_name = f"{stem} (import-{source_id[-6:]}){suffix}"
    return pure.with_name(alt_name).as_posix()


def _render_working(
    source: Path,
    *,
    source_id: str,
    document_type: DocumentType,
    category_id: str,
) -> str:
    document = load_markdown(source)
    metadata = dict(document.metadata)
    metadata["javis_id"] = source_id
    metadata["javis_type"] = document_type.value
    metadata["origin"] = "markdown_import"
    if category_id:
        metadata["javis_category"] = category_id
    return render_markdown(document, metadata=metadata)


def _refresh_index(config: BrainOSConfig, working_path: str) -> None:
    reconcile_brain(config, full_hash=True)
    classify_brain(config, paths={working_path})
    plan_brain_taxonomy(config, paths={working_path})


def import_markdown(
    config: BrainOSConfig,
    source_path: Path | str,
    *,
    document_type: DocumentType | str | None = None,
    category_id: str = "",
    dry_run: bool | None = None,
) -> ImportResult:
    """Import one Markdown file without changing the source.

    The immutable snapshot is byte-for-byte. The editable working copy receives
    only lifecycle metadata required by Brain OS. Exact re-import is idempotent
    and never overwrites edits made to the working copy.
    """

    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise MarkdownImportError(f"Không tìm thấy Markdown nguồn: {source}")
    if source.suffix.casefold() not in MARKDOWN_SUFFIXES:
        raise MarkdownImportError(
            f"Stage 6 chỉ import Markdown (.md/.markdown): {source.name}"
        )

    try:
        source_bytes = source.read_bytes()
        source_bytes.decode("utf-8")
        document = load_markdown(source)
    except (OSError, UnicodeDecodeError, FrontmatterError) as exc:
        raise MarkdownImportError(f"Markdown nguồn không hợp lệ: {source}: {exc}") from exc

    source_hash = sha256_bytes(source_bytes)
    effective_dry_run = config.dry_run if dry_run is None else bool(dry_run)
    registry = TaxonomyRegistry.from_config(config)
    store = OriginalStore(config.brain_root)

    existing_snapshot = store.find_by_hash(source_hash)
    if existing_snapshot is not None:
        existing_working = _existing_working_path(config, existing_snapshot)
        if existing_working:
            return ImportResult(
                ok=True,
                dry_run=effective_dry_run,
                source_id=existing_snapshot.source_id,
                source_sha256=source_hash,
                document_type=existing_snapshot.document_type,
                category_id=existing_snapshot.category_id,
                working_path=existing_working,
                snapshot_path=existing_snapshot.snapshot_path,
                manifest_path=existing_snapshot.manifest_path,
                reused_snapshot=True,
                reused_working_copy=True,
                indexed=bool(config.db_path.is_file()),
            )

    resolved_type = _detect_type(registry, source, document.metadata, document_type)
    scope, category = _select_category(
        registry,
        document_type=resolved_type,
        source=source,
        metadata=document.metadata,
        explicit_category_id=category_id,
    )
    resolved_category = category.id if category is not None else ""

    existing_id = str(document.metadata.get("javis_id") or "").strip()
    source_id = (
        existing_id
        if valid_existing_id(existing_id)
        else new_source_id(resolved_type)
    )

    rel_path = _destination_rel(
        registry,
        scope=scope,
        category=category,
        filename=source.name,
    )
    rel_path = _avoid_collision(config, rel_path, source_id)

    if effective_dry_run:
        return ImportResult(
            ok=True,
            dry_run=True,
            source_id=source_id,
            source_sha256=source_hash,
            document_type=resolved_type.value,
            category_id=resolved_category,
            working_path=rel_path,
            snapshot_path=f".javis/originals/imports/{source_id}/original.md",
            manifest_path=f".javis/originals/imports/{source_id}/manifest.json",
            reused_snapshot=existing_snapshot is not None,
            reused_working_copy=False,
            indexed=False,
        )

    target = safe_join(config.brain_root, rel_path)
    rendered = _render_working(
        source,
        source_id=source_id,
        document_type=resolved_type,
        category_id=resolved_category,
    )

    snapshot = store.preserve(
        source_id=source_id,
        source_bytes=source_bytes,
        source_path=source,
        working_path=rel_path,
        document_type=resolved_type.value,
        category_id=resolved_category,
    )

    reused_working = target.is_file()
    if not reused_working:
        _atomic_write_text(target, rendered)

    _refresh_index(config, rel_path)

    return ImportResult(
        ok=True,
        dry_run=False,
        source_id=snapshot.source_id,
        source_sha256=snapshot.source_sha256,
        document_type=resolved_type.value,
        category_id=resolved_category,
        working_path=rel_path,
        snapshot_path=snapshot.snapshot_path,
        manifest_path=snapshot.manifest_path,
        reused_snapshot=snapshot.reused,
        reused_working_copy=reused_working,
        indexed=True,
    )
