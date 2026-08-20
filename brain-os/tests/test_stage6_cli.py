from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
CLI = BRAIN_OS_ROOT / "template" / "skills" / "brain-manager" / "scripts" / "brain_os.py"
TEMPLATE_SYSTEM = BRAIN_OS_ROOT / "template" / "System"


@pytest.fixture()
def brain(tmp_path: Path) -> Path:
    root = tmp_path / "Brain CLI"
    root.mkdir()
    shutil.copytree(TEMPLATE_SYSTEM, root / "System")
    return root


def _run(brain: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--brain-root",
            str(brain),
            "--compact",
            *args,
        ],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def test_cli_import_defaults_to_safe_preview(brain: Path, tmp_path: Path):
    source = tmp_path / "Preview CLI.md"
    source.write_text("# Preview CLI\n", encoding="utf-8")

    proc = _run(
        brain,
        "import",
        str(source),
        "--type",
        "living_note",
        "--category",
        "notes_personal_learning",
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["action"] == "import"
    assert payload["dry_run"] is True
    assert payload["uses_ai"] is False
    assert payload["executes_javis_ingest"] is False
    assert payload["writes_wiki"] is False
    assert payload["writes_memory"] is False
    assert payload["result"]["working_path"] == "Notes/Personal/Learning/Preview CLI.md"
    assert not (brain / ".javis").exists()
    assert not (brain / payload["result"]["working_path"]).exists()


def test_cli_import_apply_creates_snapshot_and_working_copy(brain: Path, tmp_path: Path):
    source = tmp_path / "Apply CLI.md"
    source.write_text("# Apply CLI\nEditable.\n", encoding="utf-8")

    proc = _run(
        brain,
        "import",
        str(source),
        "--type",
        "living_note",
        "--category",
        "notes_personal_learning",
        "--apply",
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["dry_run"] is False
    result = payload["result"]
    assert result["indexed"] is True
    assert (brain / result["working_path"]).is_file()
    assert not Path(result["snapshot_path"]).is_absolute()
    assert (brain / result["snapshot_path"]).read_bytes() == source.read_bytes()


def test_cli_import_rejects_unregistered_category_without_writes(
    brain: Path, tmp_path: Path
):
    source = tmp_path / "Bad category.md"
    source.write_text("# Bad\n", encoding="utf-8")

    proc = _run(
        brain,
        "import",
        str(source),
        "--type",
        "living_note",
        "--category",
        "invented/category",
        "--apply",
    )

    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert "không tồn tại" in payload["error"]
    assert not (brain / ".javis").exists()
