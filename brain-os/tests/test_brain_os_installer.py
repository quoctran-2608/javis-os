from __future__ import annotations

import importlib.util
from pathlib import Path


BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BRAIN_OS_ROOT.parent
TEMPLATE = BRAIN_OS_ROOT / "template"


def _installer():
    path = BRAIN_OS_ROOT / "install_brain_os.py"
    spec = importlib.util.spec_from_file_location("brain_os_installer", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_installer_plan_preserves_unrelated_existing_brain_content(tmp_path: Path):
    target = tmp_path / "brains" / "Existing Brain"
    target.mkdir(parents=True)
    user = target / "My Notes" / "Keep.md"
    user.parent.mkdir(parents=True)
    user.write_text("# keep me\n", encoding="utf-8")

    mod = _installer()
    p = mod.plan(TEMPLATE, target)
    assert p["conflicts"] == []
    assert "System/BrainOS/config.yml" in p["copy"]
    assert user.read_text(encoding="utf-8") == "# keep me\n"


def test_installer_detects_taxonomy_conflict_instead_of_overwrite(tmp_path: Path):
    target = tmp_path / "brains" / "Custom Taxonomy"
    custom = target / "System" / "Taxonomy" / "tags.yml"
    custom.parent.mkdir(parents=True)
    custom.write_text("schema_version: 999\ncustom: true\n", encoding="utf-8")

    p = _installer().plan(TEMPLATE, target)
    assert "System/Taxonomy/tags.yml" in p["conflicts"]


def test_installer_skips_system_skill_mirrors_owned_by_javis():
    mod = _installer()
    rels = {rel.as_posix() for _src, rel in mod.source_files(TEMPLATE)}
    assert not any(rel.startswith(".claude/") for rel in rels)
    for slug in mod.SYSTEM_SKILLS:
        assert f"skills/{slug}/SKILL.md" not in rels
    assert "skills/brain-manager/SKILL.md" in rels


def test_installer_requires_matching_javis_runtime():
    report = _installer().runtime_report(REPO_ROOT)
    assert report["compatible"] is True
    assert report["bridge_plugin"] is True
    assert report["document_dependency"] is True
    assert all(report["system_skills"].values())
