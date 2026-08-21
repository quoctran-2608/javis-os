from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BRAIN_OS_ROOT.parent
SERVER = REPO_ROOT / "server"
BUILDER = BRAIN_OS_ROOT / "tools" / "build_portable_package.py"
ARCHIVE_NAME = "BrainOS-V1-Portable.zip"
LAUNCHER_NAME = "INSTALL-BRAIN-OS.bat"


def _brain_under_real_javis(tmp_path: Path) -> Path:
    brains = REPO_ROOT / "brains"
    brains.mkdir(exist_ok=True)
    brain = brains / f"_BrainOSWinEncodingTest-{tmp_path.name}"
    if brain.exists():
        shutil.rmtree(brain)
    brain.mkdir()
    return brain


def _scaffold(brain: Path) -> None:
    if str(SERVER) not in sys.path:
        sys.path.insert(0, str(SERVER))
    import main as javis_main

    javis_main._ensure_brain_scaffold(brain)


def _build_extract(brain: Path, tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--output-dir",
            str(dist),
            "--source-sha",
            "windows-legacy-encoding-sha",
            "--compact",
        ],
        cwd=str(REPO_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    source_zip = dist / ARCHIVE_NAME
    target_zip = brain / ARCHIVE_NAME
    shutil.copy2(source_zip, target_zip)
    with zipfile.ZipFile(target_zip) as zf:
        zf.extractall(brain)
    launcher = brain / LAUNCHER_NAME
    assert launcher.is_file()
    return launcher


@pytest.mark.skipif(sys.platform != "win32", reason="Windows cmd.exe encoding regression")
def test_one_click_launcher_overrides_legacy_python_encoding(tmp_path: Path):
    """Reproduce the real-vault failure mode from a legacy Windows charmap environment.

    The launcher must override hostile inherited Python encoding settings before preview,
    apply, system_sync, doctor and verify. If it does not, Vietnamese JSON emitted by
    Brain OS doctor can raise UnicodeEncodeError under redirected stdout.
    """
    brain = _brain_under_real_javis(tmp_path)
    try:
        _scaffold(brain)
        existing = brain / "Custom Area" / "Keep Me.md"
        existing.parent.mkdir(parents=True)
        existing.write_text("# Keep me\n", encoding="utf-8")
        existing_before = existing.read_bytes()
        launcher = _build_extract(brain, tmp_path)

        env = os.environ.copy()
        env["BRAIN_OS_NO_PAUSE"] = "1"
        # Deliberately emulate the user's failing Windows environment. The launcher must
        # replace these values with UTF-8 before starting Python.
        env["PYTHONIOENCODING"] = "cp1252"
        env["PYTHONUTF8"] = "0"

        proc = subprocess.run(
            ["cmd.exe", "/d", "/c", str(launcher)],
            cwd=str(brain),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        assert proc.returncode == 0, output
        assert "UnicodeEncodeError" not in output
        assert "[1/3] PREVIEW" in output
        assert "[2/3] APPLY" in output
        assert "[3/3] VERIFY + DOCTOR" in output
        assert "INSTALL COMPLETE - BRAIN OS V1 VERIFIED" in output
        assert (brain / "System" / "BrainOS" / "config.yml").is_file()
        assert existing.read_bytes() == existing_before
    finally:
        shutil.rmtree(brain, ignore_errors=True)
