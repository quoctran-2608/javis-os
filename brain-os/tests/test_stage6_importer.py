from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = BRAIN_OS_ROOT / "template" / "skills" / "brain-manager" / "scripts"
TEMPLATE_SYSTEM = BRAIN_OS_ROOT / "template" / "System"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from brain_os_lib.config import BrainOSConfig
from brain_os_lib.db import BrainIndex
from brain_os_lib.frontmatter import load_markdown
from brain_os_lib.importer import MarkdownImportError, import_markdown
from brain_os_lib.originals import OriginalsError, sha256_file
from brain_os_lib.reconcile import reconcile_brain


@pytest.fixture()
def brain(tmp_path: Path) -> Path:
    root = tmp_path / "Brain Default"
    root.mkdir()
    shutil.copytree(TEMPLATE_SYSTEM, root / "System")
    return root


def _source(tmp_path: Path, name: str, text: str) -> Path:
    root = tmp_path / "incoming"
    root.mkdir(exist_ok=True)
    path = root / name
    path.write_text(text, encoding="utf-8", newline="")
    return path


def test_living_note_import_preserves_original_and_routes_to_registered_home(
    brain: Path, tmp_path: Path
):
    source = _source(
        tmp_path,
        "ĐIỀU TÔI HỌC ĐƯỢC.md",
        "---\r\ntags:\r\n  - dieutoihocduoc\r\n  - mylife\r\n---\r\n"
        "# ĐIỀU TÔI HỌC ĐƯỢC\r\nMột ghi chép sống lâu dài.\r\n",
    )
    before = source.read_bytes()
    cfg = BrainOSConfig.load(brain)

    result = import_markdown(cfg, source, dry_run=False)

    assert result.document_type == "living_note"
    assert result.category_id == "notes_personal_learning"
    assert result.working_path == "Notes/Personal/Learning/ĐIỀU TÔI HỌC ĐƯỢC.md"
    assert result.source_id.startswith("note_")
    assert source.read_bytes() == before

    assert not Path(result.snapshot_path).is_absolute()
    assert not Path(result.manifest_path).is_absolute()
    snapshot = brain / result.snapshot_path
    assert snapshot.read_bytes() == before
    assert sha256_file(snapshot) == result.source_sha256

    working = brain / result.working_path
    parsed = load_markdown(working)
    assert parsed.metadata["javis_id"] == result.source_id
    assert parsed.metadata["javis_type"] == "living_note"
    assert parsed.metadata["origin"] == "markdown_import"
    assert parsed.metadata["javis_category"] == "notes_personal_learning"
    assert parsed.metadata["tags"] == ["dieutoihocduoc", "mylife"]
    assert "Một ghi chép sống lâu dài." in parsed.body
    with working.open("r", encoding="utf-8", newline="") as fh:
        assert "\r\n" in fh.read()


def test_working_copy_is_editable_and_exact_reimport_is_idempotent(
    brain: Path, tmp_path: Path
):
    source = _source(tmp_path, "Learning.md", "# Learning\nOriginal.\n")
    cfg = BrainOSConfig.load(brain)

    first = import_markdown(
        cfg,
        source,
        document_type="living_note",
        category_id="notes_personal_learning",
        dry_run=False,
    )
    working = brain / first.working_path
    snapshot_before = (brain / first.snapshot_path).read_bytes()
    working.write_text(
        working.read_text(encoding="utf-8") + "\nUser edit after import.\n",
        encoding="utf-8",
    )
    edited = working.read_bytes()

    second = import_markdown(cfg, source, dry_run=False)

    assert second.source_id == first.source_id
    assert second.working_path == first.working_path
    assert second.reused_snapshot is True
    assert second.reused_working_copy is True
    assert working.read_bytes() == edited
    assert (brain / first.snapshot_path).read_bytes() == snapshot_before


def test_reference_source_defaults_to_sources_unsorted(brain: Path, tmp_path: Path):
    source = _source(tmp_path, "Vendor manual.md", "# Vendor manual\nReference material.\n")
    cfg = BrainOSConfig.load(brain)

    result = import_markdown(cfg, source, dry_run=False)

    assert result.document_type == "reference_source"
    assert result.category_id == ""
    assert result.working_path == "sources/_Unsorted/Vendor manual.md"
    assert (brain / result.working_path).is_file()


def test_reference_source_honors_existing_category_only(brain: Path, tmp_path: Path):
    source = _source(
        tmp_path,
        "AI handbook.md",
        "---\njavis_category: knowledge_ai\n---\n# AI handbook\n",
    )
    cfg = BrainOSConfig.load(brain)

    result = import_markdown(cfg, source, dry_run=False)
    assert result.working_path == "sources/AI/AI handbook.md"
    assert result.category_id == "knowledge_ai"

    bad = _source(
        tmp_path,
        "Bad.md",
        "---\njavis_category: invented/new/category\n---\n# Bad\n",
    )
    with pytest.raises(MarkdownImportError):
        import_markdown(cfg, bad, dry_run=False)


def test_existing_valid_javis_id_is_preserved(brain: Path, tmp_path: Path):
    source = _source(
        tmp_path,
        "Existing.md",
        "---\njavis_id: note_existing_123\njavis_type: living_note\n---\n# Existing\n",
    )
    cfg = BrainOSConfig.load(brain)

    result = import_markdown(
        cfg,
        source,
        category_id="notes_personal_learning",
        dry_run=False,
    )
    assert result.source_id == "note_existing_123"
    assert load_markdown(brain / result.working_path).metadata["javis_id"] == "note_existing_123"


def test_rename_after_import_keeps_stable_identity(brain: Path, tmp_path: Path):
    source = _source(tmp_path, "Rename me.md", "# Rename me\n")
    cfg = BrainOSConfig.load(brain)
    result = import_markdown(
        cfg,
        source,
        document_type="living_note",
        category_id="notes_personal_learning",
        dry_run=False,
    )

    old = brain / result.working_path
    renamed = old.with_name("Đã đổi tên.md")
    old.rename(renamed)
    reconcile_brain(cfg, full_hash=True)

    with BrainIndex(cfg.db_path) as index:
        item = index.get_file(result.source_id)
        assert item is not None
        assert item.path == "Notes/Personal/Learning/Đã đổi tên.md"

    again = import_markdown(cfg, source, dry_run=False)
    assert again.source_id == result.source_id
    assert again.working_path == "Notes/Personal/Learning/Đã đổi tên.md"
    assert again.reused_working_copy is True


def test_dry_run_writes_nothing(brain: Path, tmp_path: Path):
    source = _source(tmp_path, "Preview.md", "# Preview\n")
    cfg = BrainOSConfig.load(brain)

    result = import_markdown(cfg, source, dry_run=True)

    assert result.dry_run is True
    assert not (brain / ".javis").exists()
    assert not (brain / result.working_path).exists()


def test_snapshot_tamper_fails_closed(brain: Path, tmp_path: Path):
    source = _source(tmp_path, "Immutable.md", "# Immutable\n")
    cfg = BrainOSConfig.load(brain)
    first = import_markdown(cfg, source, dry_run=False)

    (brain / first.snapshot_path).write_text("# tampered\n", encoding="utf-8")

    with pytest.raises(OriginalsError):
        import_markdown(cfg, source, dry_run=False)


def test_manifest_contains_provenance_not_working_note_hash_state(
    brain: Path, tmp_path: Path
):
    source = _source(tmp_path, "Provenance.md", "# Provenance\n")
    cfg = BrainOSConfig.load(brain)
    result = import_markdown(cfg, source, dry_run=False)

    manifest = json.loads((brain / result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["source_id"] == result.source_id
    assert manifest["source_sha256"] == result.source_sha256
    assert manifest["working_path"] == result.working_path
    assert "last_seen_hash" not in load_markdown(brain / result.working_path).metadata
    assert "last_ingested_hash" not in load_markdown(brain / result.working_path).metadata
