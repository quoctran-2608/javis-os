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

from brain_os_lib.config import BrainOSConfig
from brain_os_lib.db import BrainIndex
from brain_os_lib.frontmatter import load_markdown
from brain_os_lib.reconcile import reconcile_brain
from brain_os_lib.recovery import read_lifecycle_checkpoint, restore_lifecycle_checkpoints
from record_ingest import record_ingest


@pytest.fixture()
def brain(tmp_path: Path) -> Path:
    root = tmp_path / "Brain Recovery Identity"
    root.mkdir()
    shutil.copytree(TEMPLATE_SYSTEM, root / "System")
    return root


def _indexed_source_id(brain: Path, relative_path: str) -> str:
    cfg = BrainOSConfig.load(brain)
    with BrainIndex(cfg.db_path) as index:
        item = index.get_file_by_path(relative_path)
        assert item is not None
        return item.source_id


def _run(brain: Path, *args: str) -> subprocess.CompletedProcess[str]:
    script = SCRIPTS / "brain_identity.py"
    return subprocess.run(
        [
            sys.executable,
            str(script),
            "--brain-root",
            str(brain),
            "--compact",
            "materialize",
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _drop_db(cfg: BrainOSConfig) -> None:
    for path in (
        cfg.db_path,
        Path(str(cfg.db_path) + "-wal"),
        Path(str(cfg.db_path) + "-shm"),
    ):
        path.unlink(missing_ok=True)


def test_materialize_preview_is_read_only_and_identity_survives_db_rebuild(brain: Path):
    note = brain / "Notes" / "Legacy.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Legacy\nBody remains user-owned.\n", encoding="utf-8")
    before = note.read_bytes()

    cfg = BrainOSConfig.load(brain)
    first_scan = reconcile_brain(cfg, full_hash=True)
    assert first_scan.ok is True
    source_id = _indexed_source_id(brain, "Notes/Legacy.md")
    assert source_id.startswith("note_")
    assert "javis_id" not in load_markdown(note).metadata

    preview = _run(brain)
    assert preview.returncode == 0, preview.stderr
    payload = json.loads(preview.stdout)
    assert payload["ok"] is True
    assert payload["writes_user_files"] is False
    assert payload["plan"]["ready_to_apply"] is True
    assert payload["plan"]["counts"]["candidates"] == 1
    assert note.read_bytes() == before

    applied = _run(brain, "--apply")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    result = json.loads(applied.stdout)
    assert result["ok"] is True
    assert result["result"]["applied"] == 1
    assert load_markdown(note).metadata["javis_id"] == source_id
    assert load_markdown(note).body == "# Legacy\nBody remains user-owned.\n"

    backup = (
        brain
        / ".javis"
        / "originals"
        / "identity-bootstrap"
        / source_id
        / "original.md"
    )
    assert backup.read_bytes() == before

    _drop_db(cfg)
    rebuilt = reconcile_brain(cfg, full_hash=True)
    assert rebuilt.ok is True
    assert _indexed_source_id(brain, "Notes/Legacy.md") == source_id


def test_materialize_refuses_user_identity_conflict_without_overwrite(brain: Path):
    note = brain / "Notes" / "Conflict.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Conflict\n", encoding="utf-8")
    cfg = BrainOSConfig.load(brain)
    assert reconcile_brain(cfg, full_hash=True).ok is True
    database_id = _indexed_source_id(brain, "Notes/Conflict.md")

    note.write_text(
        "---\njavis_id: note_userchosen123\n---\n# Conflict\n",
        encoding="utf-8",
    )
    before = note.read_bytes()
    applied = _run(brain, "--apply")

    assert applied.returncode == 2
    payload = json.loads(applied.stdout)
    assert payload["ok"] is False
    assert "xung đột" in payload["error"]
    assert note.read_bytes() == before
    assert load_markdown(note).metadata["javis_id"] == "note_userchosen123"
    assert database_id != "note_userchosen123"


def test_materialize_migrates_current_ingested_identity_without_false_stale(brain: Path):
    note = brain / "Notes" / "Already ingested.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Already ingested\nKnowledge body.\n", encoding="utf-8")
    cfg = BrainOSConfig.load(brain)
    assert reconcile_brain(cfg, full_hash=True).ok is True
    source_id = _indexed_source_id(brain, "Notes/Already ingested.md")

    recorded = record_ingest(cfg, path="Notes/Already ingested.md", compounded=True)
    old_ingested_hash = recorded["last_ingested_hash"]
    assert recorded["state"] == "compounded"
    assert "javis_id" not in load_markdown(note).metadata

    preview = _run(brain)
    assert preview.returncode == 0
    planned = json.loads(preview.stdout)
    assert planned["plan"]["ready_to_apply"] is True
    assert planned["plan"]["counts"]["lifecycle_migrations"] == 1

    applied = _run(brain, "--apply")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    payload = json.loads(applied.stdout)
    assert payload["result"]["lifecycle_migrated"] == 1
    assert load_markdown(note).metadata["javis_id"] == source_id
    assert load_markdown(note).body == "# Already ingested\nKnowledge body.\n"

    with BrainIndex(cfg.db_path) as index:
        item = index.get_file_by_path("Notes/Already ingested.md")
        assert item is not None
        assert item.state.value == "compounded"
        assert item.last_ingested_hash == item.content_hash
        assert item.last_ingested_hash != old_ingested_hash
        migrated_hash = item.content_hash

    checkpoint = read_lifecycle_checkpoint(cfg, source_id)
    assert checkpoint is not None
    assert checkpoint["last_ingested_hash"] == migrated_hash
    assert checkpoint["state_hint"] == "compounded"

    _drop_db(cfg)
    assert reconcile_brain(cfg, full_hash=True).ok is True
    restored = restore_lifecycle_checkpoints(cfg)
    assert restored["restored_current"] == 1
    with BrainIndex(cfg.db_path) as index:
        item = index.get_file(source_id)
        assert item is not None
        assert item.state.value == "compounded"
        assert item.last_ingested_hash == item.content_hash == migrated_hash


def test_materialize_preserves_already_stale_lifecycle(brain: Path):
    note = brain / "Notes" / "Stale.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Stale\nVersion one.\n", encoding="utf-8")
    cfg = BrainOSConfig.load(brain)
    assert reconcile_brain(cfg, full_hash=True).ok is True
    source_id = _indexed_source_id(brain, "Notes/Stale.md")
    recorded = record_ingest(cfg, path="Notes/Stale.md")
    ingested_hash = recorded["last_ingested_hash"]

    note.write_text("# Stale\nVersion two.\n", encoding="utf-8")
    assert reconcile_brain(cfg, full_hash=True).ok is True
    with BrainIndex(cfg.db_path) as index:
        item = index.get_file(source_id)
        assert item is not None
        assert item.state.value == "stale"
        assert item.last_ingested_hash == ingested_hash
        assert item.content_hash != ingested_hash

    applied = _run(brain, "--apply")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    checkpoint = read_lifecycle_checkpoint(cfg, source_id)
    assert checkpoint is not None
    assert checkpoint["last_ingested_hash"] == ingested_hash

    with BrainIndex(cfg.db_path) as index:
        item = index.get_file(source_id)
        assert item is not None
        assert item.state.value == "stale"
        assert item.last_ingested_hash == ingested_hash
        assert item.content_hash != ingested_hash

    _drop_db(cfg)
    assert reconcile_brain(cfg, full_hash=True).ok is True
    restored = restore_lifecycle_checkpoints(cfg)
    assert restored["restored_stale"] == 1
    with BrainIndex(cfg.db_path) as index:
        item = index.get_file(source_id)
        assert item is not None
        assert item.state.value == "stale"
        assert item.last_ingested_hash == ingested_hash
