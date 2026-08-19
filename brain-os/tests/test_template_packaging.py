from __future__ import annotations

from pathlib import Path


BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = BRAIN_OS_ROOT / "template"


def test_deploy_template_ships_runtime_dependencies():
    req = TEMPLATE / "requirements-brain-os.txt"
    assert req.is_file()
    text = req.read_text(encoding="utf-8").casefold()
    assert "pyyaml" in text
    assert "pypdf" in text


def test_deploy_template_ships_governed_javis_skills_at_canonical_and_mirror_paths():
    expected = {
        "ingest-source",
        "notes",
        "query-wiki",
        "lint-wiki",
    }
    for name in expected:
        canonical = TEMPLATE / "skills" / name / "SKILL.md"
        mirror = TEMPLATE / ".claude" / "skills" / name / "SKILL.md"
        assert canonical.is_file(), str(canonical)
        assert mirror.is_file(), str(mirror)
        canonical_text = canonical.read_text(encoding="utf-8")
        mirror_text = mirror.read_text(encoding="utf-8")
        assert "Brain OS" in canonical_text
        assert canonical_text == mirror_text


def test_deploy_template_has_no_runtime_state():
    assert not (TEMPLATE / ".javis").exists()
