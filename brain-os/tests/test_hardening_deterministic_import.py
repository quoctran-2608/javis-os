from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest


BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = BRAIN_OS_ROOT / "template" / "skills" / "brain-manager" / "scripts"
TEMPLATE_SYSTEM = BRAIN_OS_ROOT / "template" / "System"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from brain_os_lib.amplenote import migrate_amplenote
from brain_os_lib.config import BrainOSConfig
from brain_os_lib.identity import deterministic_import_source_id
from brain_os_lib.importer import import_markdown
from brain_os_lib.models import DocumentType
from brain_os_lib.originals import sha256_bytes


@pytest.fixture()
def brain(tmp_path: Path) -> Path:
    root = tmp_path / "Brain Deterministic Import"
    root.mkdir()
    shutil.copytree(TEMPLATE_SYSTEM, root / "System")
    return root


def _amplenote_source(tmp_path: Path) -> Path:
    path = tmp_path / "ĐIỀU TÔI HỌC ĐƯỢC.md"
    path.write_text(
        "---\n"
        "title: ĐIỀU TÔI HỌC ĐƯỢC\n"
        "uuid: deterministic-fixture\n"
        "tags:\n"
        "  - dieutoihocduoc\n"
        "  - mylife\n"
        "---\n"
        "# ĐIỀU TÔI HỌC ĐƯỢC\n"
        "Một ghi chú sống.\n",
        encoding="utf-8",
    )
    return path


def test_markdown_preview_and_apply_share_exact_source_id(brain: Path, tmp_path: Path):
    source = tmp_path / "Learning.md"
    source.write_text("# Learning\nStable bytes.\n", encoding="utf-8")
    cfg = BrainOSConfig.load(brain)

    preview = import_markdown(
        cfg,
        source,
        document_type="living_note",
        category_id="notes_personal_learning",
        dry_run=True,
    )
    applied = import_markdown(
        cfg,
        source,
        document_type="living_note",
        category_id="notes_personal_learning",
        dry_run=False,
    )

    assert preview.source_id == applied.source_id
    assert preview.snapshot_path == applied.snapshot_path
    assert preview.manifest_path == applied.manifest_path
    assert preview.source_sha256 == applied.source_sha256


def test_amplenote_preview_and_apply_share_exact_source_id(brain: Path, tmp_path: Path):
    source = _amplenote_source(tmp_path)
    cfg = BrainOSConfig.load(brain)

    preview = migrate_amplenote(cfg, source, apply=False)
    applied = migrate_amplenote(cfg, source, apply=True)

    assert preview.notes[0]["source_id"] == applied.notes[0]["source_id"]
    assert preview.notes[0]["working_path"] == applied.notes[0]["working_path"]
    assert applied.notes[0]["reused_snapshot"] is False
    assert applied.notes[0]["reused_working_copy"] is False


def test_import_id_is_content_and_type_stable_but_not_global_authoring_id():
    first_hash = sha256_bytes(b"same source")
    second_hash = sha256_bytes(b"different source")

    note_first = deterministic_import_source_id(DocumentType.LIVING_NOTE, first_hash)
    note_again = deterministic_import_source_id(DocumentType.LIVING_NOTE, first_hash.upper())
    note_second = deterministic_import_source_id(DocumentType.LIVING_NOTE, second_hash)
    reference_first = deterministic_import_source_id(
        DocumentType.REFERENCE_SOURCE,
        first_hash,
    )

    assert note_first == note_again
    assert note_first.startswith("note_")
    assert note_first != note_second
    assert reference_first.startswith("src_")
    assert reference_first != note_first


def test_deterministic_import_id_rejects_non_sha256_input():
    with pytest.raises(ValueError):
        deterministic_import_source_id(DocumentType.LIVING_NOTE, "not-a-sha256")
