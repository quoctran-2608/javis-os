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

from brain_os_lib.config import BrainOSConfig
from brain_os_lib.frontmatter import load_markdown
from brain_os_lib.importer import import_markdown


@pytest.fixture()
def brain(tmp_path: Path) -> Path:
    root = tmp_path / "Brain Reimport"
    root.mkdir()
    shutil.copytree(TEMPLATE_SYSTEM, root / "System")
    return root


def test_reimport_after_working_copy_deleted_reuses_original_identity(
    brain: Path, tmp_path: Path
):
    source = tmp_path / "Durable identity.md"
    source.write_text("# Durable identity\n", encoding="utf-8")
    cfg = BrainOSConfig.load(brain)

    first = import_markdown(
        cfg,
        source,
        document_type="living_note",
        category_id="notes_personal_learning",
        dry_run=False,
    )
    first_snapshot = Path(first.snapshot_path)
    first_working = brain / first.working_path
    first_working.unlink()

    second = import_markdown(cfg, source, dry_run=False)

    assert second.source_id == first.source_id
    assert second.snapshot_path == first.snapshot_path
    assert second.reused_snapshot is True
    assert second.reused_working_copy is False
    restored = brain / second.working_path
    assert restored.is_file()
    assert load_markdown(restored).metadata["javis_id"] == first.source_id
    assert first_snapshot.is_file()
    assert len(list((brain / ".javis" / "originals" / "imports").glob("*/manifest.json"))) == 1


def test_exact_same_source_from_different_external_filename_does_not_fork_identity(
    brain: Path, tmp_path: Path
):
    source_a = tmp_path / "Original external name.md"
    source_a.write_text("# Same bytes\n", encoding="utf-8")
    cfg = BrainOSConfig.load(brain)
    first = import_markdown(
        cfg,
        source_a,
        document_type="living_note",
        category_id="notes_personal_learning",
        dry_run=False,
    )

    source_b = tmp_path / "Renamed external source.md"
    source_b.write_bytes(source_a.read_bytes())
    second = import_markdown(cfg, source_b, dry_run=False)

    assert second.source_id == first.source_id
    assert second.working_path == first.working_path
    assert second.reused_snapshot is True
    assert second.reused_working_copy is True
    assert len(list((brain / ".javis" / "originals" / "imports").glob("*/manifest.json"))) == 1
