from __future__ import annotations

from pathlib import Path


BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BRAIN_OS_ROOT.parent
ROOT_SKILL = REPO_ROOT / ".claude" / "skills" / "lint-wiki" / "SKILL.md"
TEMPLATE_SKILL = (
    BRAIN_OS_ROOT / "template" / ".claude" / "skills" / "lint-wiki" / "SKILL.md"
)
CONTRACT = BRAIN_OS_ROOT / "template" / "System" / "BrainOS" / "javis-integration.md"


def test_lint_wiki_skill_is_shipped_in_template_verbatim():
    root_text = ROOT_SKILL.read_text(encoding="utf-8")
    template_text = TEMPLATE_SKILL.read_text(encoding="utf-8")

    assert root_text == template_text
    assert "Brain OS governed Wiki audit" in root_text
    assert "Mặc định chỉ AUDIT" in root_text
    assert "brain_os.py scan --compact" in root_text


def test_lint_wiki_is_read_only_by_default_and_does_not_auto_repair():
    text = ROOT_SKILL.read_text(encoding="utf-8")

    assert "không tự sửa, merge, rename, delete hoặc tạo Wiki" in text
    assert "không tự append `_open-questions.md`" in text
    assert "không tự re-ingest source/Living Note" in text
    assert "không ingest `wiki/**`" in text
    assert "Người dùng phải chọn issue hoặc scope sửa rõ ràng" in text


def test_lint_wiki_stale_and_orphan_rules_are_brain_os_aware():
    text = ROOT_SKILL.read_text(encoding="utf-8")

    assert "Chỉ gọi là `stale claim`" in text
    assert "Không dùng tuổi file hay `mtime` một mình" in text
    assert "Brain OS lifecycle/state" in text
    assert "`wiki/index.md` được tính là inbound navigation hợp lệ" in text
    assert "Provenance weakness" in text
    assert "Derived-boundary violation" in text


def test_lint_wiki_repairs_delegate_source_reingest_and_never_record_wiki_ingest():
    text = ROOT_SKILL.read_text(encoding="utf-8")

    assert "delegate cho Brain OS-governed `ingest-source`" in text
    assert "không gọi `record_ingest.py` cho Wiki" in text
    assert "brain_os.py scan --compact" in text


def test_integration_contract_covers_wiki_lint_governance():
    text = CONTRACT.read_text(encoding="utf-8")

    assert "## Wiki lint / health audit" in text
    assert "A lint/health-check request is an audit request, not a repair request." in text
    assert "report `stale risk`" in text
    assert "do not label it orphan" in text
    assert "delegate to the governed `ingest-source` skill" in text
    assert "never call `record_ingest.py` on Wiki output" in text
