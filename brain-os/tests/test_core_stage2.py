from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    BRAIN_OS_ROOT
    / "template"
    / "skills"
    / "brain-manager"
    / "scripts"
)
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from brain_os_lib.config import BrainOSConfig, BrainOSConfigError
from brain_os_lib.db import BrainIndex, BrainIndexError
from brain_os_lib.frontmatter import load_markdown, update_frontmatter
from brain_os_lib.hashing import fingerprint_file
from brain_os_lib.identity import ensure_javis_id, read_javis_id
from brain_os_lib.models import BrainFile, DocumentType, ProcessingState
from brain_os_lib.paths import BrainPathError, BrainPaths, safe_join


def _dump(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


@pytest.fixture()
def brain(tmp_path: Path) -> Path:
    root = tmp_path / "Brain Default"
    root.mkdir()

    _dump(
        root / "System/BrainOS/config.yml",
        {
            "schema_version": 1,
            "mode": "balanced",
            "dry_run": True,
            "index": {
                "database": ".javis/brain-index.db",
                "use_fts5_if_available": True,
            },
            "paths": {
                "dashboard": "00 - Dashboard",
                "daily": "01 - Daily Log",
                "weekly": "02 - Weekly Log",
                "monthly": "03 - Monthly Log",
                "future": "04 - Future Log",
                "notes": "Notes",
                "sources": "sources",
                "library": "Library",
                "wiki": "wiki",
                "memory": "memory",
                "skills": "skills",
                "javis": "Javis",
                "system": "System",
                "state": ".javis",
            },
            "zones": {
                "00 - Dashboard": {"ingest": "never"},
                "01 - Daily Log": {"ingest": "selective"},
                "02 - Weekly Log": {"ingest": "selective"},
                "03 - Monthly Log": {"ingest": "selective"},
                "04 - Future Log": {"ingest": "never"},
                "Notes": {"ingest": "auto_selective"},
                "sources": {"ingest": "auto"},
                "Library": {"ingest": "via_normalized_source"},
                "wiki": {"ingest": "never"},
                "memory": {"ingest": "memory_pipeline"},
                ".javis": {"ingest": "never"},
            },
            "ignore_paths": [
                ".git",
                ".obsidian",
                ".javis",
                "attachments",
                "inbox",
                "Javis/loop-log",
            ],
        },
    )
    _dump(
        root / "System/Taxonomy/folders.yml",
        {
            "schema_version": 1,
            "scopes": {
                "living_notes": {
                    "roots": ["Notes"],
                    "fallback": "_Unsorted",
                    "categories": {},
                }
            },
        },
    )
    _dump(
        root / "System/Taxonomy/tags.yml",
        {
            "schema_version": 1,
            "canonical_tags": {
                "personal/learning": {"id": "personal_learning"}
            },
        },
    )
    _dump(
        root / "System/Taxonomy/tag-aliases.yml",
        {
            "schema_version": 1,
            "aliases": {"dieutoihocduoc": "personal/learning"},
        },
    )
    return root


def test_config_load_and_safe_paths(brain: Path):
    cfg = BrainOSConfig.load(brain)
    assert cfg.dry_run is True
    assert cfg.mode == "balanced"
    assert cfg.db_path == brain / ".javis/brain-index.db"
    assert cfg.path("notes") == brain / "Notes"

    paths = BrainPaths(cfg)
    assert paths.zone_for("Notes/Test.md") == "Notes"
    assert paths.is_ignored(".obsidian/workspace.json") is True
    assert paths.is_ignored("Notes/Test.md") is False
    assert paths.decision("wiki/X.md").policy["ingest"] == "never"


def test_safe_join_blocks_escape(brain: Path):
    assert safe_join(brain, "Notes/A.md") == brain / "Notes/A.md"
    with pytest.raises(BrainPathError):
        safe_join(brain, "../outside.md")


def test_config_rejects_unprotected_wiki(brain: Path):
    path = brain / "System/BrainOS/config.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["zones"]["wiki"]["ingest"] = "auto"
    _dump(path, data)
    with pytest.raises(BrainOSConfigError):
        BrainOSConfig.load(brain)


def test_database_is_rebuildable_and_idempotent(brain: Path):
    cfg = BrainOSConfig.load(brain)
    with BrainIndex(cfg.db_path) as index:
        assert index.status()["schema_version"] == 1
        assert index.counts()["files"] == 0

        item = BrainFile(
            source_id="note_123456789abc",
            path="Notes/Test.md",
            file_type=".md",
            document_type=DocumentType.LIVING_NOTE,
            state=ProcessingState.INDEXED,
            content_hash="abc",
            last_seen_hash="abc",
            metadata={"x": 1},
        )
        index.upsert_file(item)
        got = index.get_file("note_123456789abc")
        assert got is not None
        assert got.path == "Notes/Test.md"
        assert got.document_type == DocumentType.LIVING_NOTE
        assert got.metadata == {"x": 1}

        moved = BrainFile(
            **{
                **got.__dict__,
                "path": "Notes/Renamed.md",
                "updated_at": "",
            }
        )
        index.upsert_file(moved)
        assert index.get_file_by_path("Notes/Test.md") is None
        assert index.get_file_by_path("Notes/Renamed.md").source_id == got.source_id

    with BrainIndex(cfg.db_path) as reopened:
        assert reopened.counts()["files"] == 1


def test_database_refuses_newer_schema(brain: Path):
    db = brain / ".javis/brain-index.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version = 99")
    conn.close()

    with pytest.raises(BrainIndexError):
        BrainIndex(db).open()


def test_fingerprint_is_relative_and_content_based(brain: Path):
    note = brain / "Notes/Test.md"
    note.parent.mkdir(parents=True)
    note.write_text("hello", encoding="utf-8")

    a = fingerprint_file(note, brain_root=brain)
    note.write_text("hello world", encoding="utf-8")
    b = fingerprint_file(note, brain_root=brain)

    assert a.path == "Notes/Test.md"
    assert a.sha256 != b.sha256
    assert a.size != b.size


def test_frontmatter_noop_does_not_rewrite_and_body_is_preserved(brain: Path):
    note = brain / "Notes/Living.md"
    note.parent.mkdir(parents=True)
    raw = (
        "\ufeff---\r\n"
        "type: living-note\r\n"
        "tags:\r\n"
        "  - personal/learning\r\n"
        "---\r\n"
        "# Điều tôi học được\r\n\r\n"
        "Nội dung gốc.\r\n"
    )
    note.write_text(raw, encoding="utf-8", newline="")
    before = note.read_bytes()

    result = update_frontmatter(
        note,
        updates={"type": "living-note"},
        dry_run=False,
    )
    assert result.changed is False
    assert note.read_bytes() == before

    result = update_frontmatter(
        note,
        updates={"category": "personal/learning"},
        dry_run=False,
    )
    assert result.changed is True
    parsed = load_markdown(note)
    assert parsed.metadata["category"] == "personal/learning"
    assert parsed.body == "# Điều tôi học được\r\n\r\nNội dung gốc.\r\n"
    assert parsed.had_bom is True
    assert parsed.newline == "\r\n"


def test_identity_dry_run_then_apply(brain: Path):
    note = brain / "Notes/Living.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Living\n", encoding="utf-8")
    original = note.read_bytes()

    preview = ensure_javis_id(
        note,
        document_type=DocumentType.LIVING_NOTE,
        dry_run=True,
    )
    assert preview.generated is True
    assert preview.source_id.startswith("note_")
    assert note.read_bytes() == original
    assert read_javis_id(note) == ""

    applied = ensure_javis_id(
        note,
        document_type=DocumentType.LIVING_NOTE,
        dry_run=False,
    )
    assert applied.generated is True
    assert read_javis_id(note) == applied.source_id
    parsed = load_markdown(note)
    assert parsed.body == "# Living\n"


def test_cli_status_and_doctor_are_read_only_before_init(brain: Path):
    cli = SCRIPTS / "brain_os.py"
    db = brain / ".javis/brain-index.db"
    assert not db.exists()

    status = subprocess.run(
        [sys.executable, str(cli), "--brain-root", str(brain), "status"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(status.stdout)
    assert payload["database"]["initialized"] is False
    assert not db.exists()

    doctor = subprocess.run(
        [sys.executable, str(cli), "--brain-root", str(brain), "doctor"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(doctor.stdout)
    assert payload["ok"] is True
    assert not db.exists()


def test_cli_init_creates_only_state_db(brain: Path):
    cli = SCRIPTS / "brain_os.py"
    result = subprocess.run(
        [sys.executable, str(cli), "--brain-root", str(brain), "init"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert (brain / ".javis/brain-index.db").is_file()
    assert not (brain / "Notes").exists()
    assert not (brain / "wiki").exists()
