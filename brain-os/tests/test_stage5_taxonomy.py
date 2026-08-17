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

from brain_os_lib.classifier import classify_brain
from brain_os_lib.config import BrainOSConfig
from brain_os_lib.db import BrainIndex
from brain_os_lib.models import BrainFile, DocumentType
from brain_os_lib.reconcile import reconcile_brain
from brain_os_lib.taxonomy import (
    TAXONOMY_VERSION,
    TaxonomyError,
    TaxonomyRegistry,
    list_taxonomy_plans,
    plan_brain_taxonomy,
    plan_taxonomy_for_file,
)


@pytest.fixture()
def brain(tmp_path: Path) -> Path:
    root = tmp_path / "Brain Default"
    root.mkdir()
    shutil.copytree(TEMPLATE_SYSTEM, root / "System")
    return root


def _write(root: Path, path: str, text: str = "# Note\n") -> Path:
    fp = root / path
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(text, encoding="utf-8")
    return fp


def _file(root: Path, path: str) -> BrainFile:
    cfg = BrainOSConfig.load(root)
    with BrainIndex(cfg.db_path) as index:
        item = index.get_file_by_path(path)
        assert item is not None
        return item


def _scan_classify(root: Path) -> BrainOSConfig:
    cfg = BrainOSConfig.load(root)
    reconcile_brain(cfg, full_hash=True)
    classify_brain(cfg)
    return cfg


def _scan_classify_taxonomy(root: Path):
    cfg = _scan_classify(root)
    report = plan_brain_taxonomy(cfg)
    return cfg, report


def test_registry_loads_existing_categories_tags_and_aliases(brain: Path):
    cfg = BrainOSConfig.load(brain)
    registry = TaxonomyRegistry.from_config(cfg)

    assert registry.resolve_tag("VAT") == "accounting/tax/vat"
    assert registry.resolve_tag("thuế GTGT") == "accounting/tax/vat"
    assert registry.resolve_tag("dieutoihocduoc") == "personal/learning"
    assert registry.resolve_category("knowledge", "knowledge_accounting_tax").path == "Accounting/Tax"
    assert registry.resolve_category("knowledge", "thuế").id == "knowledge_accounting_tax"
    assert registry.resolve_category("living_notes", "điều tôi học được").id == "notes_personal_learning"


def test_registry_rejects_alias_target_that_is_not_canonical(brain: Path):
    aliases_path = brain / "System/Taxonomy/tag-aliases.yml"
    data = yaml.safe_load(aliases_path.read_text(encoding="utf-8"))
    data["aliases"]["broken"] = "does/not/exist"
    aliases_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    cfg = BrainOSConfig.load(brain)
    with pytest.raises(TaxonomyError):
        TaxonomyRegistry.from_config(cfg)


def test_known_living_note_location_wins_even_if_content_is_cross_domain(brain: Path):
    note = _write(
        brain,
        "Notes/Personal/Learning/Journal.md",
        "# Thuế và AI\nAI, VAT, dòng tiền, nhưng đây vẫn là sổ học tập của tôi.\n",
    )
    before = note.read_bytes()
    cfg, report = _scan_classify_taxonomy(brain)

    item = _file(brain, "Notes/Personal/Learning/Journal.md")
    plan = item.metadata["taxonomy"]
    assert report.accepted == 1
    assert item.category_id == "notes_personal_learning"
    assert plan["status"] == "location_locked"
    assert plan["confidence"] == pytest.approx(1.0)
    assert plan["would_move_to"] == ""
    assert note.read_bytes() == before


def test_registered_location_keeps_unregistered_deeper_manual_subfolder(brain: Path):
    _write(brain, "sources/Accounting/Tax/VAT/A.md", "# VAT\n")
    cfg, report = _scan_classify_taxonomy(brain)

    item = _file(brain, "sources/Accounting/Tax/VAT/A.md")
    plan = item.metadata["taxonomy"]
    assert item.category_id == "knowledge_accounting_tax"
    assert plan["current_location_category_id"] == "knowledge_accounting_tax"
    assert plan["target_directory"] == "sources/Accounting/Tax"
    assert plan["would_move_to"] == ""


def test_explicit_category_is_used_only_when_location_does_not_already_lock(brain: Path):
    _write(
        brain,
        "sources/_Unsorted/Operations.md",
        "---\njavis_category: knowledge_business_operations\n---\n# Misc\n",
    )
    cfg, report = _scan_classify_taxonomy(brain)

    item = _file(brain, "sources/_Unsorted/Operations.md")
    plan = item.metadata["taxonomy"]
    assert item.category_id == "knowledge_business_operations"
    assert plan["status"] == "explicit_category"
    assert plan["confidence"] == 1.0
    assert plan["would_move_to"] == "sources/Business/Operations/Operations.md"


def test_location_beats_stale_conflicting_explicit_category(brain: Path):
    _write(
        brain,
        "sources/Accounting/Tax/A.md",
        "---\njavis_category: knowledge_ai\n---\n# A\n",
    )
    cfg, report = _scan_classify_taxonomy(brain)

    item = _file(brain, "sources/Accounting/Tax/A.md")
    plan = item.metadata["taxonomy"]
    assert item.category_id == "knowledge_accounting_tax"
    assert plan["status"] == "location_locked"
    assert any("explicit_category_conflicts_with_location" in w for w in plan["warnings"])


def test_unknown_generic_category_is_not_claimed_by_brain_os(brain: Path):
    _write(
        brain,
        "sources/_Unsorted/A.md",
        "---\ncategory: meeting\n---\n# completely neutral\n",
    )
    cfg, report = _scan_classify_taxonomy(brain)

    item = _file(brain, "sources/_Unsorted/A.md")
    plan = item.metadata["taxonomy"]
    assert item.category_id == ""
    assert plan["accepted"] is False
    assert plan["status"] == "unsorted"
    assert plan["fallback_directory"] == "sources/_Unsorted"


def test_amplenote_style_learning_note_reuses_legacy_tag_aliases(brain: Path):
    path = "Notes/_Unsorted/ĐIỀU TÔI HỌC ĐƯỢC.md"
    _write(
        brain,
        path,
        "---\ntags:\n  - dieutoihocduoc\n  - mylife\n---\n"
        "# ĐIỀU TÔI HỌC ĐƯỢC\n"
        "Một ghi chép dài theo thời gian về những điều đã học.\n",
    )
    cfg, report = _scan_classify_taxonomy(brain)

    item = _file(brain, path)
    plan = item.metadata["taxonomy"]
    assert item.category_id == "notes_personal_learning"
    assert plan["accepted"] is True
    assert plan["canonical_existing_tags"] == ["personal/learning", "personal/life"]
    assert plan["legacy_tags"] == []
    assert plan["proposed_tags"][:2] == ["personal/learning", "personal/life"]
    assert plan["would_move_to"].startswith("Notes/Personal/Learning/")


def test_tax_vat_title_selects_existing_tax_folder_and_specific_hierarchical_tag(brain: Path):
    path = "sources/_Unsorted/Thuế GTGT đầu vào.md"
    _write(
        brain,
        path,
        "# Thuế GTGT đầu vào\nHóa đơn VAT đầu vào và cách xử lý thuế GTGT.\n",
    )
    cfg, report = _scan_classify_taxonomy(brain)

    item = _file(brain, path)
    plan = item.metadata["taxonomy"]
    assert item.category_id == "knowledge_accounting_tax"
    assert plan["accepted"] is True
    assert plan["ambiguous"] is False
    assert plan["confidence"] >= 0.80
    assert plan["would_move_to"] == "sources/Accounting/Tax/Thuế GTGT đầu vào.md"
    assert "accounting/tax/vat" in plan["proposed_tags"]
    # The more specific VAT tag makes an auto-added ancestor redundant.
    assert "accounting/tax" not in plan["proposed_tags"]


def test_ai_title_selects_existing_ai_category_without_creating_new_folder(brain: Path):
    path = "sources/_Unsorted/AI cho doanh nghiệp.md"
    _write(brain, path, "# AI cho doanh nghiệp\nỨng dụng trí tuệ nhân tạo.\n")
    cfg, report = _scan_classify_taxonomy(brain)

    item = _file(brain, path)
    plan = item.metadata["taxonomy"]
    assert item.category_id == "knowledge_ai"
    assert plan["target_directory"] == "sources/AI"
    assert "ai" in plan["proposed_tags"]
    assert report.would_move == 1


def test_cross_domain_tax_and_ai_tie_abstains_to_unsorted(brain: Path):
    path = "sources/_Unsorted/Thuế và AI.md"
    _write(brain, path, "# Thuế và AI\nMột ghi chú cân bằng giữa hai chủ đề.\n")
    cfg, report = _scan_classify_taxonomy(brain)

    item = _file(brain, path)
    plan = item.metadata["taxonomy"]
    assert item.category_id == ""
    assert plan["accepted"] is False
    assert plan["ambiguous"] is True
    assert plan["status"] == "ambiguous_unsorted"
    assert plan["fallback_directory"] == "sources/_Unsorted"
    assert plan["would_move_to"] == ""
    assert report.ambiguous == 1


def test_single_body_keyword_is_candidate_not_auto_category(brain: Path):
    path = "sources/_Unsorted/Random.md"
    _write(brain, path, "# Random\nChỉ nhắc thuế đúng một lần.\n")
    cfg, report = _scan_classify_taxonomy(brain)

    item = _file(brain, path)
    plan = item.metadata["taxonomy"]
    assert item.category_id == ""
    assert plan["accepted"] is False
    assert plan["candidate"] is True
    assert plan["status"] == "candidate_unsorted"
    assert plan["confidence"] < 0.80


def test_daily_weekly_wiki_memory_and_scratch_do_not_enter_folder_taxonomy(brain: Path):
    paths = [
        "01 - Daily Log/2026-08-17.md",
        "02 - Weekly Log/2026-W34.md",
        "wiki/Concept.md",
        "memory/Profile.md",
        "Scratch/Temp.md",
    ]
    for path in paths:
        _write(brain, path)

    cfg, report = _scan_classify_taxonomy(brain)
    assert report.not_applicable == len(paths)
    for path in paths:
        item = _file(brain, path)
        plan = item.metadata["taxonomy"]
        assert plan["applicable"] is False
        assert plan["status"] == "not_applicable"
        assert item.category_id == ""


def test_manual_ignore_skips_taxonomy_even_for_living_note(brain: Path):
    _write(
        brain,
        "Notes/_Unsorted/Ignore.md",
        "---\njavis: ignore\n---\n# Thuế GTGT\n",
    )
    cfg, report = _scan_classify_taxonomy(brain)

    item = _file(brain, "Notes/_Unsorted/Ignore.md")
    plan = item.metadata["taxonomy"]
    assert plan["status"] == "manual_ignore"
    assert plan["applicable"] is False
    assert item.category_id == ""


def test_previous_accepted_category_stabilizes_after_small_content_shift(brain: Path):
    path = "sources/_Unsorted/Tax note.md"
    note = _write(
        brain,
        path,
        "# Thuế GTGT\nVAT VAT thuế thuế.\n",
    )
    cfg = _scan_classify(brain)
    first = plan_brain_taxonomy(cfg)
    assert first.accepted == 1
    assert _file(brain, path).category_id == "knowledge_accounting_tax"

    note.write_text(
        "# AI\nAI AI trí tuệ nhân tạo. Nội dung vừa được bổ sung.\n",
        encoding="utf-8",
    )
    reconcile_brain(cfg, full_hash=True)
    classify_brain(cfg)
    second = plan_brain_taxonomy(cfg)

    item = _file(brain, path)
    plan = item.metadata["taxonomy"]
    assert item.category_id == "knowledge_accounting_tax"
    assert plan["status"] == "stable_existing_category"
    assert plan["reason_codes"] == ["previous_category:knowledge_accounting_tax"]


def test_second_taxonomy_run_uses_hash_path_policy_cache(brain: Path):
    _write(brain, "sources/_Unsorted/AI.md", "# AI\n")
    cfg = _scan_classify(brain)

    first = plan_brain_taxonomy(cfg)
    second = plan_brain_taxonomy(cfg)
    forced = plan_brain_taxonomy(cfg, force=True)

    assert first.analyzed == 1
    assert first.cached == 0
    assert second.cached == 1
    assert second.analyzed == 0
    assert forced.cached == 0
    assert forced.analyzed == 1


def test_taxonomy_path_filter_only_touches_selected_records(brain: Path):
    _write(brain, "sources/_Unsorted/AI.md", "# AI\n")
    _write(brain, "sources/_Unsorted/Tax.md", "# Thuế\n")
    cfg = _scan_classify(brain)

    report = plan_brain_taxonomy(cfg, paths={"sources/_Unsorted/AI.md"})
    assert report.scanned_records == 1
    assert report.path_filtered_out == 1
    assert "taxonomy" in _file(brain, "sources/_Unsorted/AI.md").metadata
    assert "taxonomy" not in _file(brain, "sources/_Unsorted/Tax.md").metadata


def test_legacy_unknown_tags_are_preserved_not_invented_as_canonical(brain: Path):
    _write(
        brain,
        "Notes/_Unsorted/A.md",
        "---\ntags:\n  - mylife\n  - very-old-custom-tag\n---\n# A\n",
    )
    cfg, report = _scan_classify_taxonomy(brain)

    plan = _file(brain, "Notes/_Unsorted/A.md").metadata["taxonomy"]
    assert plan["canonical_existing_tags"] == ["personal/life"]
    assert plan["legacy_tags"] == ["very-old-custom-tag"]
    assert "very-old-custom-tag" not in plan["proposed_tags"]


def test_user_tags_over_limit_are_preserved_and_only_warned(brain: Path):
    tags_path = brain / "System/Taxonomy/tags.yml"
    tags = yaml.safe_load(tags_path.read_text(encoding="utf-8"))
    # Existing registry has enough canonical tags for this safety test.
    existing = list(tags["canonical_tags"].keys())[:7]
    body = "---\ntags:\n" + "".join(f"  - {tag}\n" for tag in existing) + "---\n# A\n"
    _write(brain, "sources/_Unsorted/A.md", body)
    cfg, report = _scan_classify_taxonomy(brain)

    plan = _file(brain, "sources/_Unsorted/A.md").metadata["taxonomy"]
    assert all(tag in plan["proposed_tags"] for tag in existing)
    assert any("preserve_user_tags" in warning for warning in plan["warnings"])


def test_bounded_probe_does_not_rewrite_large_living_note(brain: Path):
    config_path = brain / "System/BrainOS/config.yml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["taxonomy"]["max_text_probe_bytes"] = 4096
    config_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    note = _write(
        brain,
        "Notes/Personal/Learning/Long.md",
        "# Long\n" + ("nội dung học tập\n" * 5000),
    )
    before = note.read_bytes()
    cfg, report = _scan_classify_taxonomy(brain)

    plan = _file(brain, "Notes/Personal/Learning/Long.md").metadata["taxonomy"]
    assert plan["accepted"] is True
    assert note.read_bytes() == before


def test_pure_taxonomy_planner_does_not_mutate_source_file(brain: Path):
    note = _write(brain, "sources/_Unsorted/Thuế.md", "# Thuế GTGT\n")
    cfg = BrainOSConfig.load(brain)
    item = BrainFile(
        source_id="x",
        path="sources/_Unsorted/Thuế.md",
        document_type=DocumentType.REFERENCE_SOURCE,
        content_hash="abc",
    )
    before = note.read_bytes()

    decision = plan_taxonomy_for_file(cfg, item)
    assert decision.accepted is True
    assert decision.category_id == "knowledge_accounting_tax"
    assert note.read_bytes() == before


def test_taxonomy_plan_listing_filters_without_writing(brain: Path):
    _write(brain, "sources/_Unsorted/AI.md", "# AI\n")
    _write(brain, "sources/_Unsorted/Neutral.md", "# Neutral\n")
    cfg, report = _scan_classify_taxonomy(brain)

    moving = list_taxonomy_plans(cfg, would_move_only=True, limit=10)
    unresolved = list_taxonomy_plans(cfg, unresolved_only=True, limit=10)
    assert [row["path"] for row in moving] == ["sources/_Unsorted/AI.md"]
    assert [row["path"] for row in unresolved] == ["sources/_Unsorted/Neutral.md"]


def test_cli_taxonomy_is_explicitly_dry_run_and_never_mutates_note(brain: Path):
    note = _write(brain, "sources/_Unsorted/AI.md", "# AI\n")
    before = note.read_bytes()
    cli = SCRIPTS / "brain_os.py"

    for command in (
        ["scan", "--full-hash"],
        ["classify"],
    ):
        subprocess.run(
            [sys.executable, str(cli), "--brain-root", str(brain), *command],
            check=True,
            capture_output=True,
            text=True,
        )

    result = subprocess.run(
        [sys.executable, str(cli), "--brain-root", str(brain), "taxonomy"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["writes_user_files"] is False
    assert payload["moves_user_files"] is False
    assert payload["mutates_frontmatter"] is False
    assert payload["derived_state_only"] is True
    assert payload["uses_ai"] is False
    assert payload["report"]["would_move"] == 1
    assert note.read_bytes() == before


def test_persisted_taxonomy_metadata_has_cache_provenance(brain: Path):
    _write(brain, "sources/_Unsorted/AI.md", "# AI\n")
    cfg, report = _scan_classify_taxonomy(brain)

    item = _file(brain, "sources/_Unsorted/AI.md")
    plan = item.metadata["taxonomy"]
    assert plan["taxonomy_version"] == TAXONOMY_VERSION
    assert plan["content_hash"] == item.content_hash
    assert plan["path"] == item.path
    assert plan["document_type"] == "reference_source"
    assert plan["committed_category_id"] == "knowledge_ai"
    assert plan["dry_run"] is True
    assert plan["writes_user_files"] is False
