from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = BRAIN_OS_ROOT / "template" / "skills" / "brain-manager" / "scripts"
TEMPLATE_SYSTEM = BRAIN_OS_ROOT / "template" / "System"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from brain_os_lib.classifier import (
    CLASSIFIER_VERSION,
    classify_brain,
    classify_document,
    list_classifications,
)
from brain_os_lib.config import BrainOSConfig, BrainOSConfigError
from brain_os_lib.db import BrainIndex
from brain_os_lib.models import BrainFile, DocumentType, ProcessingState
from brain_os_lib.reconcile import reconcile_brain


@pytest.fixture()
def brain(tmp_path: Path) -> Path:
    root = tmp_path / "Brain Default"
    root.mkdir()
    shutil.copytree(TEMPLATE_SYSTEM, root / "System")
    return root


def _file(root: Path, path: str) -> BrainFile:
    cfg = BrainOSConfig.load(root)
    with BrainIndex(cfg.db_path) as index:
        item = index.get_file_by_path(path)
        assert item is not None
        return item


def _write(root: Path, path: str, text: str = "# Note\n") -> Path:
    fp = root / path
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(text, encoding="utf-8")
    return fp


def _scan_and_classify(root: Path):
    cfg = BrainOSConfig.load(root)
    reconcile_brain(cfg, full_hash=True)
    return cfg, classify_brain(cfg)


def test_known_zones_are_high_confidence_deterministic(brain: Path):
    expected = {
        "00 - Dashboard/Home.md": DocumentType.SYSTEM,
        "01 - Daily Log/2026-08-17.md": DocumentType.DAILY,
        "02 - Weekly Log/2026-W34.md": DocumentType.WEEKLY,
        "03 - Monthly Log/2026-08.md": DocumentType.MONTHLY,
        "04 - Future Log/Later.md": DocumentType.FUTURE,
        "Notes/Living.md": DocumentType.LIVING_NOTE,
        "sources/Tax.md": DocumentType.REFERENCE_SOURCE,
        "Library/Original.md": DocumentType.REFERENCE_SOURCE,
        "wiki/Concept.md": DocumentType.DERIVED_WIKI,
        "memory/Person.md": DocumentType.MEMORY,
    }
    for path in expected:
        _write(brain, path)

    cfg, report = _scan_and_classify(brain)
    assert report.needs_ai == 0
    assert report.classified == len(expected)

    for path, doc_type in expected.items():
        item = _file(brain, path)
        assert item.document_type == doc_type
        assert item.state == ProcessingState.CLASSIFIED
        meta = item.metadata["classification"]
        assert meta["accepted"] is True
        assert meta["needs_ai"] is False
        assert meta["confidence"] == pytest.approx(0.98)
        assert meta["classifier_version"] == CLASSIFIER_VERSION
        assert meta["content_hash"] == item.content_hash
        assert meta["path"] == path
        assert meta["reason_codes"] == [f"zone:{path.split('/', 1)[0]}"]


def test_javis_type_explicitly_overrides_zone_and_generic_type(brain: Path):
    note = _write(
        brain,
        "Notes/Mixed.md",
        "---\njavis_type: reference-source\ntype: living-note\n---\n# Mixed\n",
    )
    before = note.read_bytes()
    cfg, report = _scan_and_classify(brain)

    assert report.classified == 1
    item = _file(brain, "Notes/Mixed.md")
    assert item.document_type == DocumentType.REFERENCE_SOURCE
    meta = item.metadata["classification"]
    assert meta["confidence"] == 1.0
    assert meta["explicit_type_field"] == "javis_type"
    assert meta["reason_codes"] == ["frontmatter:javis_type"]
    assert any("conflicting_type_fields" in warning for warning in meta["warnings"])
    assert any("explicit_type_overrides_zone" in warning for warning in meta["warnings"])
    assert note.read_bytes() == before


def test_generic_type_is_only_used_when_value_is_recognized(brain: Path):
    recognized = _write(brain, "Loose.md", "---\ntype: living-note\n---\n# A\n")
    unrelated = _write(brain, "Meeting.md", "---\ntype: meeting\n---\n# B\n")
    cfg = BrainOSConfig.load(brain)
    reconcile_brain(cfg, full_hash=True)
    report = classify_brain(cfg)

    assert report.classified == 1
    assert report.needs_ai == 1
    assert _file(brain, "Loose.md").document_type == DocumentType.LIVING_NOTE
    assert _file(brain, "Meeting.md").document_type == DocumentType.UNKNOWN
    assert recognized.read_text(encoding="utf-8").startswith("---\ntype: living-note")
    assert unrelated.read_text(encoding="utf-8").startswith("---\ntype: meeting")


def test_invalid_explicit_type_falls_back_to_strong_zone_with_warning(brain: Path):
    _write(brain, "Notes/A.md", "---\njavis_type: banana\n---\n# A\n")
    cfg, report = _scan_and_classify(brain)

    assert report.classified == 1
    assert report.needs_ai == 0
    item = _file(brain, "Notes/A.md")
    assert item.document_type == DocumentType.LIVING_NOTE
    warnings = item.metadata["classification"]["warnings"]
    assert any("invalid_explicit_type" in warning for warning in warnings)


def test_weak_iso_filename_is_proposal_only_and_needs_ai(brain: Path):
    _write(brain, "2026-08-17.md")
    cfg, report = _scan_and_classify(brain)

    assert report.classified == 0
    assert report.needs_ai == 1
    assert report.unknown == 1
    item = _file(brain, "2026-08-17.md")
    assert item.document_type == DocumentType.UNKNOWN
    assert item.state == ProcessingState.UNCLASSIFIED
    meta = item.metadata["classification"]
    assert meta["proposed_type"] == DocumentType.DAILY.value
    assert meta["document_type"] == DocumentType.UNKNOWN.value
    assert meta["accepted"] is False
    assert meta["needs_ai"] is True
    assert meta["status"] == "needs_ai_candidate"
    assert meta["confidence"] == pytest.approx(0.68)


def test_strong_scratch_folder_hint_is_accepted_outside_managed_zones(brain: Path):
    _write(brain, "Scratch/Quick.md", "buy printer ink\n")
    cfg, report = _scan_and_classify(brain)

    assert report.classified == 1
    item = _file(brain, "Scratch/Quick.md")
    assert item.document_type == DocumentType.SCRATCH
    assert item.metadata["classification"]["reason_codes"] == ["path_hint:scratch"]
    assert item.metadata["classification"]["confidence"] == pytest.approx(0.90)


def test_manual_index_suppresses_ai_without_inventing_document_type(brain: Path):
    _write(brain, "Loose.md", "---\njavis: index\n---\n# Unknown but searchable\n")
    cfg, report = _scan_and_classify(brain)

    assert report.classified == 0
    assert report.needs_ai == 0
    item = _file(brain, "Loose.md")
    assert item.document_type == DocumentType.UNKNOWN
    meta = item.metadata["classification"]
    assert meta["manual_mode"] == "index"
    assert meta["status"] == "manual_no_ai"
    assert meta["needs_ai"] is False


def test_second_classification_uses_hash_path_policy_cache(brain: Path):
    _write(brain, "Notes/A.md")
    cfg = BrainOSConfig.load(brain)
    reconcile_brain(cfg, full_hash=True)

    first = classify_brain(cfg)
    second = classify_brain(cfg)
    forced = classify_brain(cfg, force=True)

    assert first.classified == 1
    assert first.cached == 0
    assert second.cached == 1
    assert second.classified == 0
    assert forced.cached == 0
    assert forced.classified == 1


def test_content_change_invalidates_classification_cache(brain: Path):
    note = _write(brain, "Notes/A.md", "# A\n")
    cfg = BrainOSConfig.load(brain)
    reconcile_brain(cfg, full_hash=True)
    classify_brain(cfg)

    note.write_text("# A\nnew line\n", encoding="utf-8")
    reconcile_brain(cfg, full_hash=True)
    report = classify_brain(cfg)

    assert report.cached == 0
    assert report.classified == 1
    assert _file(brain, "Notes/A.md").document_type == DocumentType.LIVING_NOTE


def test_move_across_zones_reclassifies_same_source_id(brain: Path):
    old = _write(brain, "Notes/A.md", "# A\n")
    cfg = BrainOSConfig.load(brain)
    reconcile_brain(cfg, full_hash=True)
    classify_brain(cfg)
    old_item = _file(brain, "Notes/A.md")
    source_id = old_item.source_id
    assert old_item.document_type == DocumentType.LIVING_NOTE

    new = brain / "sources/A.md"
    new.parent.mkdir(parents=True, exist_ok=True)
    old.rename(new)
    reconcile_brain(cfg, full_hash=True)
    report = classify_brain(cfg)

    item = _file(brain, "sources/A.md")
    assert item.source_id == source_id
    assert item.document_type == DocumentType.REFERENCE_SOURCE
    assert report.cached == 0
    assert item.metadata["classification"]["reason_codes"] == ["zone:sources"]


def test_missing_record_is_not_reclassified(brain: Path):
    note = _write(brain, "Notes/A.md")
    cfg = BrainOSConfig.load(brain)
    reconcile_brain(cfg, full_hash=True)
    classify_brain(cfg)
    note.unlink()
    reconcile_brain(cfg, full_hash=True)

    report = classify_brain(cfg, force=True)
    assert report.missing == 1
    item = _file(brain, "Notes/A.md")
    assert item.state == ProcessingState.MISSING


def test_malformed_frontmatter_does_not_kill_batch_and_zone_still_wins(brain: Path):
    _write(brain, "Notes/Bad.md", "---\ninvalid: [yaml\n---\n# Body\n")
    cfg, report = _scan_and_classify(brain)

    assert report.ok is True
    assert report.classified == 1
    item = _file(brain, "Notes/Bad.md")
    assert item.document_type == DocumentType.LIVING_NOTE
    warnings = item.metadata["classification"]["warnings"]
    assert any("frontmatter_unreadable" in warning for warning in warnings)


def test_needs_ai_listing_is_read_only_and_filtered(brain: Path):
    _write(brain, "Notes/Good.md")
    _write(brain, "2026-08-17.md")
    cfg, _ = _scan_and_classify(brain)

    rows = list_classifications(cfg, needs_ai_only=True, limit=10)
    assert [row["path"] for row in rows] == ["2026-08-17.md"]
    assert rows[0]["classification"]["proposed_type"] == "daily"


def test_classify_path_filter_only_touches_selected_records(brain: Path):
    _write(brain, "Notes/A.md")
    _write(brain, "Notes/B.md")
    cfg = BrainOSConfig.load(brain)
    reconcile_brain(cfg, full_hash=True)

    report = classify_brain(cfg, paths={"Notes/A.md"})
    assert report.scanned_records == 1
    assert report.path_filtered_out == 1
    assert "classification" in _file(brain, "Notes/A.md").metadata
    assert "classification" not in _file(brain, "Notes/B.md").metadata


def test_cli_classify_writes_only_derived_state_and_uses_no_ai(brain: Path):
    note = _write(brain, "Notes/A.md", "# A\n")
    before = note.read_bytes()
    cli = SCRIPTS / "brain_os.py"

    subprocess.run(
        [sys.executable, str(cli), "--brain-root", str(brain), "scan", "--full-hash"],
        check=True,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        [sys.executable, str(cli), "--brain-root", str(brain), "classify"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["ok"] is True
    assert payload["writes_user_files"] is False
    assert payload["derived_state_only"] is True
    assert payload["uses_ai"] is False
    assert payload["report"]["classified"] == 1
    assert note.read_bytes() == before


def test_config_rejects_inverted_classification_thresholds(brain: Path):
    path = brain / "System/BrainOS/config.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["classification"]["candidate_confidence"] = 0.90
    data["classification"]["accept_confidence"] = 0.80
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    with pytest.raises(BrainOSConfigError):
        BrainOSConfig.load(brain)


def test_pure_classifier_does_not_mutate_brain_file(brain: Path):
    note = _write(brain, "Scratch/A.md", "temporary\n")
    cfg = BrainOSConfig.load(brain)
    item = BrainFile(source_id="x", path="Scratch/A.md", content_hash="abc")
    before = note.read_bytes()

    decision = classify_document(cfg, item)
    assert decision.document_type == DocumentType.SCRATCH
    assert decision.accepted is True
    assert note.read_bytes() == before
