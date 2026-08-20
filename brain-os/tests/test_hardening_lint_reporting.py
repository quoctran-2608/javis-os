from __future__ import annotations

from pathlib import Path


BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BRAIN_OS_ROOT.parent
ROOT_SKILL = REPO_ROOT / ".claude" / "skills" / "lint-wiki" / "SKILL.md"
TEMPLATE_SKILL = (
    BRAIN_OS_ROOT / "template" / ".claude" / "skills" / "lint-wiki" / "SKILL.md"
)


def test_lint_reporting_policy_is_shipped_verbatim():
    root_text = ROOT_SKILL.read_text(encoding="utf-8")
    template_text = TEMPLATE_SKILL.read_text(encoding="utf-8")

    assert root_text == template_text
    assert "## Kỷ luật kết luận" in root_text


def test_lint_reporting_calibrates_negative_findings_without_overclaiming():
    text = ROOT_SKILL.read_text(encoding="utf-8")

    assert "Không phát hiện issue material trong phạm vi đã kiểm tra." in text
    assert "`Không phát hiện` không đồng nghĩa với `đã chứng minh không tồn tại`." in text
    assert "`100% healthy`" in text
    assert "`fully verified`" in text
    assert "`0 contradiction`" in text
    assert "`0 missing concept candidate`" in text
    assert "`0 coverage gap`" in text
    assert "phải gắn rõ với **scope audit đã thực hiện**" in text
    assert "không dùng `fully verified` cho toàn bộ Wiki" in text


def test_lint_reporting_requires_scope_limit_when_evidence_is_incomplete():
    text = ROOT_SKILL.read_text(encoding="utf-8")

    assert "Nếu audit chỉ sample" in text
    assert "phải nêu giới hạn đó thay vì suy rộng" in text
    assert "Chỉ dùng `verified` cho một assertion cụ thể" in text
