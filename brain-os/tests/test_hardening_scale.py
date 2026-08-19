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
from brain_os_lib.reconcile import reconcile_brain


@pytest.fixture()
def brain(tmp_path: Path) -> Path:
    root = tmp_path / "Brain Scale"
    root.mkdir()
    shutil.copytree(TEMPLATE_SYSTEM, root / "System")
    return root


def test_sparse_scan_scales_to_10k_notes_and_rehashes_only_the_changed_file(brain: Path):
    notes = brain / "Notes" / "Scale"
    notes.mkdir(parents=True)
    total = 10_000

    for idx in range(total):
        source_id = f"note_scale{idx:08d}"
        (notes / f"N-{idx:05d}.md").write_text(
            f"---\njavis_id: {source_id}\n---\n# Note {idx}\nvalue: {idx}\n",
            encoding="utf-8",
        )

    cfg = BrainOSConfig.load(brain)
    first = reconcile_brain(cfg, full_hash=True)
    assert first.ok is True
    assert first.files_seen == total
    assert first.created == total
    assert first.hashed_files == total
    assert first.reused_hashes == 0

    second = reconcile_brain(cfg, full_hash=False)
    assert second.ok is True
    assert second.files_seen == total
    assert second.unchanged == total
    assert second.hashed_files == 0
    assert second.reused_hashes == total

    changed = notes / "N-05000.md"
    changed.write_text(
        "---\njavis_id: note_scale00005000\n---\n# Note 5000\nvalue: changed\n",
        encoding="utf-8",
    )

    third = reconcile_brain(cfg, full_hash=False)
    assert third.ok is True
    assert third.files_seen == total
    assert third.modified == 1
    assert third.unchanged == total - 1
    assert third.hashed_files == 1
    assert third.reused_hashes == total - 1
