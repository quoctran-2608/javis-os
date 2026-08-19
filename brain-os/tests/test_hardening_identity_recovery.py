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

    # The SQLite index is explicitly rebuildable only after stable identity lives
    # outside that DB. Keep recovery/originals and delete SQLite sidecars only.
    for path in (
        cfg.db_path,
        Path(str(cfg.db_path) + "-wal"),
        Path(str(cfg.db_path) + "-shm"),
    ):
        path.unlink(missing_ok=True)

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


def test_materialize_refuses_db_only_identity_that_was_already_ingested(brain: Path):
    note = brain / "Notes" / "Already ingested.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Already ingested\n", encoding="utf-8")
    before = note.read_bytes()
    cfg = BrainOSConfig.load(brain)
    assert reconcile_brain(cfg, full_hash=True).ok is True

    with BrainIndex(cfg.db_path) as index:
        item = index.get_file_by_path("Notes/Already ingested.md")
        assert item is not None
        with index._require():
            index._require().execute(
                "UPDATE files SET last_ingested_hash=?, last_ingested_at=? WHERE source_id=?",
                (item.content_hash, "2026-08-19T00:00:00+00:00", item.source_id),
            )

    preview = _run(brain)
    assert preview.returncode == 0
    planned = json.loads(preview.stdout)
    assert planned["plan"]["ready_to_apply"] is False
    assert planned["plan"]["counts"]["blocked_ingested"] == 1

    applied = _run(brain, "--apply")
    assert applied.returncode == 2
    payload = json.loads(applied.stdout)
    assert payload["ok"] is False
    assert "đã từng ingest" in payload["error"]
    assert note.read_bytes() == before
    assert "javis_id" not in load_markdown(note).metadata
