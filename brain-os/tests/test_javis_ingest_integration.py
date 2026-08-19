from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest


BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BRAIN_OS_ROOT.parent
SCRIPTS = BRAIN_OS_ROOT / "template" / "skills" / "brain-manager" / "scripts"
TEMPLATE_SYSTEM = BRAIN_OS_ROOT / "template" / "System"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from brain_os_lib.amplenote import migrate_amplenote
from brain_os_lib.config import BrainOSConfig
from brain_os_lib.db import BrainIndex
from brain_os_lib.frontmatter import load_markdown
from record_ingest import record_ingest


@pytest.fixture()
def brain(tmp_path: Path) -> Path:
    root = tmp_path / "Brain Integration"
    root.mkdir()
    shutil.copytree(TEMPLATE_SYSTEM, root / "System")
    return root


def _single_amplenote_note(tmp_path: Path) -> Path:
    note = tmp_path / "ĐIỀU TÔI HỌC ĐƯỢC.md"
    note.write_text(
        "---\n"
        "title: ĐIỀU TÔI HỌC ĐƯỢC\n"
        "uuid: amplenote-single-note-001\n"
        "version: 7\n"
        "tags:\n"
        "  - mylife\n"
        "  - dieutoihocduoc\n"
        "---\n"
        "# ĐIỀU TÔI HỌC ĐƯỢC\n\n"
        "Một Living Note tiếp tục thay đổi theo thời gian.\n",
        encoding="utf-8",
    )
    return note


def test_single_amplenote_markdown_is_managed_as_living_note_with_provenance(
    brain: Path, tmp_path: Path
):
    source = _single_amplenote_note(tmp_path)
    original = source.read_bytes()
    config = BrainOSConfig.load(brain)

    preview = migrate_amplenote(config, source, apply=False)
    assert preview.ok is True
    assert preview.source_kind == "markdown"
    assert preview.discovered_notes == 1
    assert preview.notes[0]["document_type"] == "living_note"
    assert preview.notes[0]["category_id"] == "notes_personal_learning"
    assert preview.notes[0]["working_path"] == (
        "Notes/Personal/Learning/ĐIỀU TÔI HỌC ĐƯỢC.md"
    )
    assert not (brain / ".javis").exists()

    applied = migrate_amplenote(config, source, apply=True)
    item = applied.notes[0]
    working = brain / item["working_path"]
    parsed = load_markdown(working)

    assert source.read_bytes() == original
    assert applied.source_kind == "markdown"
    assert parsed.metadata["javis_id"] == item["source_id"]
    assert parsed.metadata["javis_type"] == "living_note"
    assert parsed.metadata["javis_category"] == "notes_personal_learning"
    assert parsed.metadata["origin"] == "amplenote_import"
    assert parsed.metadata["tags"] == ["personal/life", "personal/learning"]
    assert parsed.metadata["legacy_tags"] == ["mylife", "dieutoihocduoc"]
    assert parsed.metadata["uuid"] == "amplenote-single-note-001"
    assert parsed.metadata["version"] == 7

    manifest_path = next(
        (brain / ".javis" / "originals" / "imports").glob("*/manifest.json")
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["migration_provenance"] == [
        {
            "source_system": "amplenote",
            "source_entry": "ĐIỀU TÔI HỌC ĐƯỢC.md",
        }
    ]


def test_record_ingest_updates_only_derived_state(brain: Path, tmp_path: Path):
    source = _single_amplenote_note(tmp_path)
    config = BrainOSConfig.load(brain)
    applied = migrate_amplenote(config, source, apply=True)
    rel = applied.notes[0]["working_path"]
    working = brain / rel
    before = working.read_bytes()

    result = record_ingest(config, path=rel, compounded=True)

    assert result["ok"] is True
    assert result["state"] == "compounded"
    assert result["last_ingested_hash"] == result["content_hash"]
    assert result["writes_user_files"] is False
    assert result["derived_state_only"] is True
    assert working.read_bytes() == before

    with BrainIndex(config.db_path) as index:
        item = index.get_file_by_path(rel)
        assert item is not None
        assert item.state.value == "compounded"
        assert item.last_ingested_hash == item.content_hash
        assert item.last_ingested_at


def test_ingest_skill_is_brain_os_governed_and_contract_is_packaged():
    contract = BRAIN_OS_ROOT / "template" / "System" / "BrainOS" / "javis-integration.md"
    skill = REPO_ROOT / ".claude" / "skills" / "ingest-source" / "SKILL.md"

    assert contract.is_file()
    contract_text = contract.read_text(encoding="utf-8")
    skill_text = skill.read_text(encoding="utf-8")

    assert "Brain OS ↔ Javis Integration Contract" in contract_text
    assert "Living Note" in contract_text
    assert "record_ingest.py" in contract_text
    assert "System/BrainOS/javis-integration.md" in skill_text
    assert "import_amplenote.py" in skill_text
    assert "import_document.py" in skill_text
    assert "record_ingest.py" in skill_text
    assert "không ghi `status: processed`" in skill_text
    assert "không chuyển sang `sources/`" in skill_text
