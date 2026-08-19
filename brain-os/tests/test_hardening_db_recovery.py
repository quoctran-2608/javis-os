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
from brain_os_lib.recovery import read_lifecycle_checkpoint
from record_ingest import record_ingest


@pytest.fixture()
def brain(tmp_path: Path) -> Path:
    root = tmp_path / "Brain DB Recovery"
    root.mkdir()
    shutil.copytree(TEMPLATE_SYSTEM, root / "System")
    return root


def _run(brain: Path, command: str, *args: str) -> subprocess.CompletedProcess[str]:
    script = SCRIPTS / "brain_recovery.py"
    return subprocess.run(
        [
            sys.executable,
            str(script),
            "--brain-root",
            str(brain),
            "--compact",
            command,
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _item(cfg: BrainOSConfig, path: str):
    with BrainIndex(cfg.db_path) as index:
        item = index.get_file_by_path(path)
        assert item is not None
        return item


def test_prepare_migrates_identity_backfills_lifecycle_and_marks_ready(brain: Path):
    note = brain / "Notes" / "Prepared.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Prepared\nKnowledge.\n", encoding="utf-8")
    cfg = BrainOSConfig.load(brain)
    assert reconcile_brain(cfg, full_hash=True).ok is True
    original_id = _item(cfg, "Notes/Prepared.md").source_id
    record_ingest(cfg, path="Notes/Prepared.md", compounded=True)

    preview = _run(brain, "prepare")
    assert preview.returncode == 0
    preview_payload = json.loads(preview.stdout)
    assert preview_payload["result"]["can_apply"] is True
    assert note.read_text(encoding="utf-8").startswith("# Prepared")

    applied = _run(brain, "prepare", "--apply")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    payload = json.loads(applied.stdout)
    assert payload["ok"] is True
    assert payload["result"]["audit"]["rebuild_ready"] is True
    assert payload["result"]["ready_marker"]["contract"] == "brain-os-recovery-ready-v1"
    assert load_markdown(note).metadata["javis_id"] == original_id

    item = _item(cfg, "Notes/Prepared.md")
    assert item.source_id == original_id
    assert item.state.value == "compounded"
    assert item.last_ingested_hash == item.content_hash
    checkpoint = read_lifecycle_checkpoint(cfg, original_id)
    assert checkpoint is not None
    assert checkpoint["last_ingested_hash"] == item.content_hash
    assert checkpoint["state_hint"] == "compounded"


def test_rebuild_restores_current_and_stale_lifecycle_without_fake_events(brain: Path):
    notes = brain / "Notes"
    notes.mkdir(parents=True)
    current = notes / "Current.md"
    stale = notes / "Stale.md"
    current.write_text("# Current\nA.\n", encoding="utf-8")
    stale.write_text("# Stale\nV1.\n", encoding="utf-8")
    cfg = BrainOSConfig.load(brain)
    assert reconcile_brain(cfg, full_hash=True).ok is True
    current_id = _item(cfg, "Notes/Current.md").source_id
    stale_id = _item(cfg, "Notes/Stale.md").source_id
    record_ingest(cfg, path="Notes/Current.md", compounded=True)
    stale_ingest = record_ingest(cfg, path="Notes/Stale.md")
    stale_hash = stale_ingest["last_ingested_hash"]

    stale.write_text("# Stale\nV2.\n", encoding="utf-8")
    assert reconcile_brain(cfg, full_hash=True).ok is True
    assert _item(cfg, "Notes/Stale.md").state.value == "stale"

    prepared = _run(brain, "prepare", "--apply")
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    before_current_hash = _item(cfg, "Notes/Current.md").content_hash
    before_stale_hash = _item(cfg, "Notes/Stale.md").content_hash

    rebuilt = _run(brain, "rebuild", "--apply")
    assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
    payload = json.loads(rebuilt.stdout)
    assert payload["result"]["audit"]["rebuild_ready"] is True
    assert payload["result"]["handled_recovery_events"] >= 2
    assert payload["result"]["archive"]["files"]

    current_item = _item(cfg, "Notes/Current.md")
    stale_item = _item(cfg, "Notes/Stale.md")
    assert current_item.source_id == current_id
    assert current_item.content_hash == before_current_hash
    assert current_item.state.value == "compounded"
    assert current_item.last_ingested_hash == current_item.content_hash
    assert stale_item.source_id == stale_id
    assert stale_item.content_hash == before_stale_hash
    assert stale_item.state.value == "stale"
    assert stale_item.last_ingested_hash == stale_hash
    assert stale_item.content_hash != stale_hash

    with BrainIndex(cfg.db_path) as index:
        unhandled = int(
            index._require().execute(
                "SELECT COUNT(*) FROM events WHERE handled_at=''"
            ).fetchone()[0]
        )
        assert unhandled == 0
        assert index.get_meta("last_recovery_archive", "")


def test_corrupted_sqlite_rebuilds_only_after_prepared_marker_and_archives_exact_bytes(brain: Path):
    note = brain / "Notes" / "Corrupt DB.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Corrupt DB\nRecover me.\n", encoding="utf-8")
    cfg = BrainOSConfig.load(brain)
    assert reconcile_brain(cfg, full_hash=True).ok is True
    source_id = _item(cfg, "Notes/Corrupt DB.md").source_id
    record_ingest(cfg, path="Notes/Corrupt DB.md", compounded=True)
    prepared = _run(brain, "prepare", "--apply")
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    migrated_hash = _item(cfg, "Notes/Corrupt DB.md").content_hash

    for sidecar in (Path(str(cfg.db_path) + "-wal"), Path(str(cfg.db_path) + "-shm")):
        sidecar.unlink(missing_ok=True)
    corrupt_bytes = b"not-a-sqlite-database\x00brain-os-recovery-proof"
    cfg.db_path.write_bytes(corrupt_bytes)

    audit = _run(brain, "audit")
    assert audit.returncode == 0, audit.stdout + audit.stderr
    audited = json.loads(audit.stdout)
    assert audited["db"]["integrity_ok"] is False
    assert audited["ready_marker"] is not None
    assert audited["rebuild_ready"] is True

    rebuilt = _run(brain, "rebuild", "--apply")
    assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
    payload = json.loads(rebuilt.stdout)
    archive_rel = payload["result"]["archive"]["path"]
    archive = brain / archive_rel
    archived_db = archive / cfg.db_path.name
    assert archived_db.read_bytes() == corrupt_bytes

    item = _item(cfg, "Notes/Corrupt DB.md")
    assert item.source_id == source_id
    assert item.content_hash == migrated_hash
    assert item.state.value == "compounded"
    assert item.last_ingested_hash == migrated_hash


def test_corrupted_db_without_ready_marker_fails_closed(brain: Path):
    note = brain / "Notes" / "No marker.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\njavis_id: note_nomarker123\n---\n# No marker\n",
        encoding="utf-8",
    )
    cfg = BrainOSConfig.load(brain)
    assert reconcile_brain(cfg, full_hash=True).ok is True
    record_ingest(cfg, path="Notes/No marker.md")
    cfg.db_path.write_bytes(b"corrupt-before-prepare")

    preview = _run(brain, "rebuild")
    assert preview.returncode == 0
    payload = json.loads(preview.stdout)
    assert payload["result"]["rebuild_ready"] is False
    assert "recovery_ready_marker_missing" in payload["result"]["blockers"]

    applied = _run(brain, "rebuild", "--apply")
    assert applied.returncode == 2
    failed = json.loads(applied.stdout)
    assert failed["ok"] is False
    assert cfg.db_path.read_bytes() == b"corrupt-before-prepare"


def test_new_db_only_note_after_prepare_blocks_rebuild_until_prepared_again(brain: Path):
    note = brain / "Notes" / "First.md"
    note.parent.mkdir(parents=True)
    note.write_text("# First\n", encoding="utf-8")
    cfg = BrainOSConfig.load(brain)
    assert reconcile_brain(cfg, full_hash=True).ok is True
    prepared = _run(brain, "prepare", "--apply")
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr

    second = brain / "Notes" / "Second.md"
    second.write_text("# Second\n", encoding="utf-8")
    assert reconcile_brain(cfg, full_hash=True).ok is True

    audit = _run(brain, "audit")
    assert audit.returncode == 0
    payload = json.loads(audit.stdout)
    assert payload["rebuild_ready"] is False
    assert "missing_durable_identity" in payload["blockers"]
    assert "Notes/Second.md" in payload["identity"]["missing_identity"]

    applied = _run(brain, "rebuild", "--apply")
    assert applied.returncode == 2
    again = _run(brain, "prepare", "--apply")
    assert again.returncode == 0, again.stdout + again.stderr
    final = _run(brain, "audit")
    assert json.loads(final.stdout)["rebuild_ready"] is True


def test_tampered_lifecycle_checkpoint_blocks_recovery(brain: Path):
    note = brain / "Notes" / "Tamper.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Tamper\n", encoding="utf-8")
    cfg = BrainOSConfig.load(brain)
    assert reconcile_brain(cfg, full_hash=True).ok is True
    source_id = _item(cfg, "Notes/Tamper.md").source_id
    record_ingest(cfg, path="Notes/Tamper.md")
    assert _run(brain, "prepare", "--apply").returncode == 0

    checkpoint = read_lifecycle_checkpoint(cfg, source_id)
    assert checkpoint is not None
    root = brain / ".javis" / "recovery" / "lifecycle"
    checkpoint_path = next(root.glob("*.json"))
    raw = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    raw["last_ingested_hash"] = "0" * 64
    checkpoint_path.write_text(json.dumps(raw), encoding="utf-8")

    audit = _run(brain, "audit")
    assert audit.returncode == 2
    payload = json.loads(audit.stdout)
    assert payload["ok"] is False
    assert "checksum mismatch" in payload["error"]
