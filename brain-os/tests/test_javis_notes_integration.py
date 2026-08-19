from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BRAIN_OS_ROOT.parent
SCRIPTS = BRAIN_OS_ROOT / "template" / "skills" / "brain-manager" / "scripts"
TEMPLATE_SYSTEM = BRAIN_OS_ROOT / "template" / "System"
CAPTURE = SCRIPTS / "capture_note.py"
ROOT_NOTES_SKILL = REPO_ROOT / ".claude" / "skills" / "notes" / "SKILL.md"
TEMPLATE_NOTES_SKILL = (
    BRAIN_OS_ROOT / "template" / ".claude" / "skills" / "notes" / "SKILL.md"
)
CONTRACT = BRAIN_OS_ROOT / "template" / "System" / "BrainOS" / "javis-integration.md"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from brain_os_lib.frontmatter import load_markdown


@pytest.fixture()
def brain(tmp_path: Path) -> Path:
    root = tmp_path / "Brain Notes"
    root.mkdir()
    shutil.copytree(TEMPLATE_SYSTEM, root / "System")
    return root


def _run_capture(brain: Path, body: bytes, *, apply: bool) -> tuple[subprocess.CompletedProcess[bytes], dict]:
    cmd = [
        sys.executable,
        str(CAPTURE),
        "--brain-root",
        str(brain),
        "--compact",
    ]
    if apply:
        cmd.append("--apply")
    proc = subprocess.run(
        cmd,
        input=body,
        check=False,
        capture_output=True,
    )
    stdout = proc.stdout.decode("utf-8")
    data = json.loads(stdout)
    return proc, data


def test_notes_capture_defaults_to_preview_and_writes_nothing(brain: Path):
    body = "Một note nhanh để nhớ điều quan trọng.\n".encode("utf-8")

    proc, data = _run_capture(brain, body, apply=False)

    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    assert data["ok"] is True
    assert data["action"] == "capture-note"
    assert data["dry_run"] is True
    assert data["document_type"] == "living_note"
    assert data["executes_javis_ingest"] is False
    assert data["writes_wiki"] is False
    assert data["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert data["result"]["working_path"].startswith("Notes/")
    assert not (brain / "Notes").exists()
    assert not (brain / ".javis").exists()


def test_notes_capture_apply_preserves_utf8_body_and_crlf(brain: Path):
    body = (
        "Tôi học được một điều hôm nay.\r\n"
        "Dòng thứ hai giữ nguyên dấu tiếng Việt.\r\n"
        "Không tự biến reflection này thành source."
    ).encode("utf-8")

    proc, data = _run_capture(brain, body, apply=True)

    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    assert data["ok"] is True
    assert data["dry_run"] is False
    assert data["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert data["body_bytes"] == len(body)

    working = brain / data["result"]["working_path"]
    assert working.is_file()
    assert working.relative_to(brain).as_posix().startswith("Notes/")
    parsed = load_markdown(working)
    assert parsed.body.encode("utf-8") == body
    assert parsed.metadata["javis_type"] == "living_note"
    assert parsed.metadata["origin"] == "javis_notes_capture"
    assert parsed.metadata["source_kind"] == "own-note"
    assert str(parsed.metadata["javis_id"]).startswith("note_")
    assert "status" not in parsed.metadata
    assert "processed_at" not in parsed.metadata
    assert (brain / ".javis" / "brain-index.db").is_file()


def test_notes_capture_rejects_empty_body_without_writes(brain: Path):
    proc, data = _run_capture(brain, b"   \r\n", apply=True)

    assert proc.returncode == 2
    assert data["ok"] is False
    assert "NoteCaptureError" in data["error"]
    assert not (brain / "Notes").exists()
    assert not (brain / ".javis").exists()


def test_notes_skill_is_shipped_in_template_and_delegates_compounding():
    root_text = ROOT_NOTES_SKILL.read_text(encoding="utf-8")
    template_text = TEMPLATE_NOTES_SKILL.read_text(encoding="utf-8")

    assert root_text == template_text
    assert "capture_note.py --apply --compact" in root_text
    assert "managed `living_note`" in root_text
    assert "skill `ingest-source`" in root_text
    assert "KHÔNG tự tạo `sources/note-...md`" in root_text
    assert "KHÔNG ghi `status: unprocessed/processed`" in root_text
    assert "pipeline Wiki riêng" in root_text


def test_integration_contract_covers_quick_capture():
    text = CONTRACT.read_text(encoding="utf-8")

    assert "## Quick capture / Notes" in text
    assert "capture_note.py" in text
    assert "managed `living_note`" in text
    assert "delegate compounding to the governed `ingest-source` skill" in text
    assert "Do not write `status: unprocessed`" in text
