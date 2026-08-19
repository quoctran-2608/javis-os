from __future__ import annotations

from pathlib import Path


BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BRAIN_OS_ROOT.parent
ROOT_SKILL = REPO_ROOT / ".claude" / "skills" / "query-wiki" / "SKILL.md"
TEMPLATE_SKILL = (
    BRAIN_OS_ROOT / "template" / ".claude" / "skills" / "query-wiki" / "SKILL.md"
)
CONTRACT = BRAIN_OS_ROOT / "template" / "System" / "BrainOS" / "javis-integration.md"


def test_query_wiki_skill_is_shipped_in_template_and_matches_root():
    root_text = ROOT_SKILL.read_text(encoding="utf-8")
    template_text = TEMPLATE_SKILL.read_text(encoding="utf-8")

    assert root_text == template_text
    assert "## Mặc định là READ-ONLY" in root_text
    assert "không tự append `wiki/_open-questions.md`" in root_text
    assert "Chỉ ghi khi người dùng yêu cầu rõ" in root_text
    assert "Source-backed" in root_text
    assert "Synthesis" in root_text
    assert "Hypothesis" in root_text
    assert "Không gọi `ingest-source` hay `record_ingest.py` trên Wiki" in root_text
    assert "brain_os.py scan --compact" in root_text
    assert "brain_os.py classify --path \"wiki/<page>.md\" --compact" in root_text


def test_query_wiki_contract_is_read_only_by_default_and_provenance_safe():
    text = CONTRACT.read_text(encoding="utf-8")

    assert "## Query / retrieval / synthesis" in text
    assert "Query is read-only by default" in text
    assert "append `wiki/_open-questions.md`" in text
    assert "Distinguish source-backed statements from new synthesis and hypothesis" in text
    assert "Every persisted claim must retain provenance/backlinks" in text
    assert "Never INGEST a Wiki page" in text
    assert "Do not call `record_ingest.py` for the derived Wiki page" in text


def test_query_wiki_does_not_reintroduce_legacy_auto_write_language():
    text = ROOT_SKILL.read_text(encoding="utf-8")

    legacy_auto_append = "Vẫn thiếu -> append 1 dòng vào `wiki/_open-questions.md`"
    legacy_auto_save = "Luôn trả lời\ncó trích dẫn và lưu lại kết quả giá trị."

    assert legacy_auto_append not in text
    assert legacy_auto_save not in text
