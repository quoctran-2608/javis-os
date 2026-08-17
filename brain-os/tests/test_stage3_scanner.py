from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = BRAIN_OS_ROOT / "template" / "skills" / "brain-manager" / "scripts"
TEMPLATE_SYSTEM = BRAIN_OS_ROOT / "template" / "System"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from brain_os_lib.changes import FileObservation, choose_existing_match
from brain_os_lib.config import BrainOSConfig
from brain_os_lib.db import BrainIndex
from brain_os_lib.models import BrainFile, DocumentType, FileFingerprint, ProcessingState
from brain_os_lib.reconcile import list_events, reconcile_brain
from brain_os_lib.scanner import ScanCollection


@pytest.fixture()
def brain(tmp_path: Path) -> Path:
    root = tmp_path / "Brain Default"
    root.mkdir()
    shutil.copytree(TEMPLATE_SYSTEM, root / "System")
    return root


def _files(root: Path) -> dict[str, BrainFile]:
    cfg = BrainOSConfig.load(root)
    with BrainIndex(cfg.db_path) as index:
        rows = index.conn.execute("SELECT source_id FROM files ORDER BY path").fetchall()
        return {
            item.path: item
            for row in rows
            if (item := index.get_file(row["source_id"])) is not None
        }


def _events(root: Path) -> list[dict]:
    cfg = BrainOSConfig.load(root)
    with BrainIndex(cfg.db_path) as index:
        return list_events(index, limit=100)


def test_initial_scan_discovers_only_eligible_markdown(brain: Path):
    note = brain / "Notes/Điều tôi học được.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Điều tôi học được\n", encoding="utf-8")
    source = brain / "sources/Thuế.md"
    source.parent.mkdir(parents=True)
    source.write_text("# Thuế\n", encoding="utf-8")

    ignored = brain / "skills/private.md"
    ignored.parent.mkdir(parents=True)
    ignored.write_text("ignore", encoding="utf-8")
    hidden = brain / ".obsidian/workspace.md"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("ignore", encoding="utf-8")
    binary = brain / "Notes/data.pdf"
    binary.write_bytes(b"%PDF-test")

    before_note = note.read_bytes()
    cfg = BrainOSConfig.load(brain)
    report = reconcile_brain(cfg)

    assert report.ok is True
    assert report.files_seen == 2
    assert report.created == 2
    assert note.read_bytes() == before_note

    files = _files(brain)
    assert set(files) == {"Notes/Điều tôi học được.md", "sources/Thuế.md"}
    assert files["Notes/Điều tôi học được.md"].document_type == DocumentType.LIVING_NOTE
    assert files["sources/Thuế.md"].document_type == DocumentType.REFERENCE_SOURCE
    assert len(_events(brain)) == 2


def test_second_scan_reuses_fingerprint_and_emits_no_unchanged_event(brain: Path):
    note = brain / "Notes/A.md"
    note.parent.mkdir(parents=True)
    note.write_text("alpha\n", encoding="utf-8")
    cfg = BrainOSConfig.load(brain)

    first = reconcile_brain(cfg)
    second = reconcile_brain(cfg)

    assert first.created == 1
    assert second.unchanged == 1
    assert second.created == 0
    assert second.modified == 0
    assert second.reused_hashes == 1
    assert len(_events(brain)) == 1


def test_modify_generates_incremental_diff_without_rewriting_note(brain: Path):
    note = brain / "Notes/Living.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Living\n\nOld line\n", encoding="utf-8")
    cfg = BrainOSConfig.load(brain)
    reconcile_brain(cfg, full_hash=True)

    note.write_text("# Living\n\nOld line\nNew insight\n", encoding="utf-8")
    expected = note.read_bytes()
    report = reconcile_brain(cfg, full_hash=True)

    assert report.modified == 1
    assert report.diffs_generated == 1
    assert note.read_bytes() == expected

    event = _events(brain)[0]
    assert event["event_type"] == "modified"
    assert event["payload"]["diff"]["changed"] is True
    assert event["payload"]["diff"]["changed_new_lines"] >= 1


def test_rename_preserves_source_id(brain: Path):
    old = brain / "Notes/Old.md"
    old.parent.mkdir(parents=True)
    old.write_text("same content\n", encoding="utf-8")
    cfg = BrainOSConfig.load(brain)
    reconcile_brain(cfg, full_hash=True)
    source_id = _files(brain)["Notes/Old.md"].source_id

    new = brain / "Notes/New.md"
    old.rename(new)
    report = reconcile_brain(cfg, full_hash=True)

    assert report.renamed == 1
    assert report.deleted == 0
    files = _files(brain)
    assert "Notes/Old.md" not in files
    assert files["Notes/New.md"].source_id == source_id


def test_move_and_edit_same_time_is_conservative(brain: Path):
    old = brain / "Notes/A.md"
    old.parent.mkdir(parents=True)
    old.write_text("one\n", encoding="utf-8")
    cfg = BrainOSConfig.load(brain)
    reconcile_brain(cfg, full_hash=True)
    source_id = _files(brain)["Notes/A.md"].source_id

    target = brain / "Notes/Personal/A.md"
    target.parent.mkdir(parents=True)
    old.rename(target)
    target.write_text("one\ntwo\n", encoding="utf-8")
    report = reconcile_brain(cfg, full_hash=True)

    files = _files(brain)
    if report.moved == 1:
        assert files["Notes/Personal/A.md"].source_id == source_id
        assert report.diffs_generated == 1
    else:
        # Nếu filesystem không cung cấp inode ổn định, không được đoán.
        assert report.created == 1
        assert report.deleted == 1


def test_delete_marks_missing_and_restore_keeps_identity(brain: Path):
    note = brain / "Notes/A.md"
    note.parent.mkdir(parents=True)
    note.write_text("alpha\n", encoding="utf-8")
    cfg = BrainOSConfig.load(brain)
    reconcile_brain(cfg, full_hash=True)
    source_id = _files(brain)["Notes/A.md"].source_id
    snapshot_dir = brain / ".javis/snapshots"
    snapshots_before = list(snapshot_dir.glob("*.txt"))
    assert snapshots_before

    note.unlink()
    deleted = reconcile_brain(cfg, full_hash=True)
    assert deleted.deleted == 1
    item = _files(brain)["Notes/A.md"]
    assert item.source_id == source_id
    assert item.state == ProcessingState.MISSING
    assert list(snapshot_dir.glob("*.txt")) == snapshots_before

    note.write_text("alpha\n", encoding="utf-8")
    restored = reconcile_brain(cfg, full_hash=True)
    assert restored.restored == 1
    item = _files(brain)["Notes/A.md"]
    assert item.source_id == source_id
    assert item.state == ProcessingState.DISCOVERED


def test_explicit_javis_id_survives_rename_and_edit(brain: Path):
    note = brain / "Notes/A.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\njavis_id: note_stable123\n---\n# A\n", encoding="utf-8")
    cfg = BrainOSConfig.load(brain)
    reconcile_brain(cfg, full_hash=True)
    assert _files(brain)["Notes/A.md"].source_id == "note_stable123"

    new = brain / "Notes/B.md"
    note.rename(new)
    new.write_text("---\njavis_id: note_stable123\n---\n# B\n", encoding="utf-8")
    report = reconcile_brain(cfg, full_hash=True)
    assert report.renamed == 1
    assert _files(brain)["Notes/B.md"].source_id == "note_stable123"


def test_ambiguous_hash_match_is_never_guessed():
    obs = FileObservation(
        fingerprint=FileFingerprint(
            path="Notes/New.md", size=4, mtime_ns=1, sha256="same", suffix=".md"
        ),
        zone="Notes",
    )
    a = BrainFile(
        source_id="a", path="Notes/A.md", file_type=".md", size=4, content_hash="same"
    )
    b = BrainFile(
        source_id="b", path="Notes/B.md", file_type=".md", size=4, content_hash="same"
    )
    match = choose_existing_match(obs, [a, b])
    assert match.file is None
    assert match.ambiguous is True
    assert match.method == "content_hash"


def test_traversal_error_suppresses_deletion(brain: Path, monkeypatch: pytest.MonkeyPatch):
    note = brain / "Notes/A.md"
    note.parent.mkdir(parents=True)
    note.write_text("alpha\n", encoding="utf-8")
    cfg = BrainOSConfig.load(brain)
    reconcile_brain(cfg, full_hash=True)
    source_id = _files(brain)["Notes/A.md"].source_id

    import brain_os_lib.reconcile as module

    fake = ScanCollection(observations=[], traversal_errors=["PermissionError: simulated"])
    monkeypatch.setattr(module, "collect_files", lambda *a, **k: fake)
    report = module.reconcile_brain(cfg, full_hash=True)

    assert report.deletions_suppressed is True
    assert _files(brain)["Notes/A.md"].source_id == source_id
    assert _files(brain)["Notes/A.md"].state != ProcessingState.MISSING


def test_cli_scan_writes_only_derived_state(brain: Path):
    note = brain / "Notes/A.md"
    note.parent.mkdir(parents=True)
    note.write_text("alpha\n", encoding="utf-8")
    before = note.read_bytes()

    cli = SCRIPTS / "brain_os.py"
    result = subprocess.run(
        [sys.executable, str(cli), "--brain-root", str(brain), "scan", "--full-hash"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["ok"] is True
    assert payload["writes_user_files"] is False
    assert payload["derived_state_only"] is True
    assert note.read_bytes() == before
    assert (brain / ".javis/brain-index.db").is_file()
