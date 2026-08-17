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
from brain_os_lib.db import BrainIndex
from brain_os_lib.reconcile import reconcile_brain


@pytest.fixture()
def brain(tmp_path: Path) -> Path:
    root = tmp_path / "Brain Default"
    root.mkdir()
    shutil.copytree(TEMPLATE_SYSTEM, root / "System")
    return root


def _indexed_files(root: Path):
    cfg = BrainOSConfig.load(root)
    with BrainIndex(cfg.db_path) as index:
        rows = index.conn.execute(
            "SELECT source_id FROM files ORDER BY path"
        ).fetchall()
        return [
            item
            for row in rows
            if (item := index.get_file(row["source_id"])) is not None
        ]


def test_duplicate_javis_id_in_same_scan_is_never_claimed_by_iteration_order(brain: Path):
    notes = brain / "Notes"
    notes.mkdir(parents=True)

    duplicate_id = "note_shared123"
    (notes / "A.md").write_text(
        f"---\njavis_id: {duplicate_id}\n---\n# A\n",
        encoding="utf-8",
    )
    (notes / "B.md").write_text(
        f"---\njavis_id: {duplicate_id}\n---\n# B\n",
        encoding="utf-8",
    )

    cfg = BrainOSConfig.load(brain)
    report = reconcile_brain(cfg, full_hash=True)

    assert report.created == 2
    assert any("duplicate javis_id" in warning for warning in report.warnings)

    files = _indexed_files(brain)
    assert len(files) == 2
    ids = {item.source_id for item in files}
    assert len(ids) == 2
    assert duplicate_id not in ids
    assert all(item.source_id.startswith("note_") for item in files)

    # Scanner must never repair/rewrite the duplicate frontmatter at Stage 3.
    assert f"javis_id: {duplicate_id}" in (notes / "A.md").read_text(encoding="utf-8")
    assert f"javis_id: {duplicate_id}" in (notes / "B.md").read_text(encoding="utf-8")


def test_markdown_extension_uses_explicit_javis_id_and_keeps_it_on_rename(brain: Path):
    notes = brain / "Notes"
    notes.mkdir(parents=True)

    source_id = "note_markdown123"
    old = notes / "Living.markdown"
    old.write_text(
        f"---\njavis_id: {source_id}\n---\n# Living\n",
        encoding="utf-8",
    )

    cfg = BrainOSConfig.load(brain)
    first = reconcile_brain(cfg, full_hash=True)
    assert first.created == 1
    assert _indexed_files(brain)[0].source_id == source_id

    new = notes / "Renamed.markdown"
    old.rename(new)
    new.write_text(
        f"---\njavis_id: {source_id}\n---\n# Living renamed\n",
        encoding="utf-8",
    )

    second = reconcile_brain(cfg, full_hash=True)
    assert second.renamed == 1
    assert second.deleted == 0

    files = _indexed_files(brain)
    assert len(files) == 1
    assert files[0].path == "Notes/Renamed.markdown"
    assert files[0].source_id == source_id
