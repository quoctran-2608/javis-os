from __future__ import annotations

import hashlib
import json
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
SCRIPTS = BRAIN_OS_ROOT / "template" / "skills" / "brain-manager" / "scripts"
PACKAGE_DIR_NAME = ".brain-os-installer"
ARCHIVE_NAME = "BrainOS-V1-Portable.zip"
LAUNCHER_NAME = "INSTALL-BRAIN-OS.bat"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from brain_os_lib.config import BrainOSConfig
from brain_os_lib.scanner import collect_files


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(cmd: list[str], *, cwd: Path) -> tuple[subprocess.CompletedProcess[str], dict]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc, payload


def _brain_under_real_javis(tmp_path: Path) -> Path:
    """Use the real local layout so runtime discovery is tested without cwd or flags."""
    brains = REPO_ROOT / "brains"
    brains.mkdir(exist_ok=True)
    brain = brains / f"_BrainOSPortableTest-{tmp_path.name}"
    if brain.exists():
        shutil.rmtree(brain)
    brain.mkdir()
    return brain


def _scaffold_exactly_like_javis_new_brain(brain: Path) -> None:
    """Call the same scaffold helper used by POST /brains/new."""
    if str(SERVER) not in sys.path:
        sys.path.insert(0, str(SERVER))
    import main as javis_main

    javis_main._ensure_brain_scaffold(brain)


def _build_copy_and_extract_zip(
    brain: Path, tmp_path: Path, *, source_sha: str
) -> tuple[dict, Path, Path, Path, set[str]]:
    """Model the user workflow exactly: build elsewhere, copy one ZIP, extract in Brain."""
    dist = tmp_path / f"dist-{source_sha}"
    dist.mkdir(parents=True, exist_ok=True)
    build_proc, built = _run(
        [
            sys.executable,
            str(BUILDER),
            "--output-dir",
            str(dist),
            "--source-sha",
            source_sha,
            "--compact",
        ],
        cwd=REPO_ROOT,
    )
    assert build_proc.returncode == 0, build_proc.stderr or build_proc.stdout
    assert built["ok"] is True
    assert built["source_sha"] == source_sha
    assert built["package_directory"] == PACKAGE_DIR_NAME
    assert built["launcher"] == LAUNCHER_NAME

    source_zip = dist / ARCHIVE_NAME
    assert source_zip.is_file()
    target_zip = brain / ARCHIVE_NAME
    assert not target_zip.exists()
    assert not (brain / PACKAGE_DIR_NAME).exists()
    assert not (brain / LAUNCHER_NAME).exists()
    shutil.copy2(source_zip, target_zip)

    with zipfile.ZipFile(target_zip) as zf:
        names = set(zf.namelist())
        assert names
        assert LAUNCHER_NAME in names
        assert all(
            name == LAUNCHER_NAME or name.startswith(PACKAGE_DIR_NAME + "/")
            for name in names
        )
        zf.extractall(brain)

    package = brain / PACKAGE_DIR_NAME
    launcher = brain / LAUNCHER_NAME
    assert package.is_dir()
    assert launcher.is_file()
    return built, package, launcher, target_zip, names


def test_portable_package_inside_javis_created_brain_preview_apply_verify(tmp_path: Path):
    brain = _brain_under_real_javis(tmp_path)
    try:
        _scaffold_exactly_like_javis_new_brain(brain)
        javis_readme = brain / "Javis" / "README.md"
        assert javis_readme.is_file()
        javis_readme_before = javis_readme.read_bytes()

        legacy = brain / "Custom Area" / "Existing.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("# Existing user knowledge\n", encoding="utf-8")

        built, package, launcher, zip_path, names = _build_copy_and_extract_zip(
            brain, tmp_path, source_sha="portable-e2e-source-sha"
        )
        assert built["zip_path"] != str(zip_path)
        assert zip_path.is_file()
        assert package.name.startswith(".")
        assert launcher.parent == brain

        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["package_schema"] == 1
        assert manifest["source_sha"] == "portable-e2e-source-sha"
        assert manifest["package_directory"] == PACKAGE_DIR_NAME
        assert manifest["archive_name"] == ARCHIVE_NAME
        assert manifest["scanner_hidden"] is True
        assert manifest["ownership"]["system_skills"] == "javis-system-sync"
        assert manifest["launchers"]["windows"]["path"] == LAUNCHER_NAME
        assert manifest["launchers"]["windows"]["sha256"] == _sha256(launcher)
        assert manifest["launchers"]["windows"]["flow"] == [
            "preview",
            "apply",
            "verify-doctor",
        ]
        assert not (package / "payload" / ".javis").exists()
        assert not (package / "payload" / ".claude").exists()
        assert not (package / "payload" / "Notes").exists()
        assert not (package / "payload" / "sources").exists()
        assert not (package / "payload" / "wiki").exists()

        assert LAUNCHER_NAME in names
        assert f"{PACKAGE_DIR_NAME}/install.py" in names
        assert f"{PACKAGE_DIR_NAME}/manifest.json" in names
        assert f"{PACKAGE_DIR_NAME}/checksums.sha256" in names
        assert f"{PACKAGE_DIR_NAME}/payload/System/BrainOS/config.yml" in names
        assert not any(f"{PACKAGE_DIR_NAME}/payload/.javis/" in name for name in names)
        assert not any(f"{PACKAGE_DIR_NAME}/payload/.claude/" in name for name in names)

        preview_proc, preview = _run(
            [sys.executable, "install.py", "--compact"], cwd=package
        )
        assert preview_proc.returncode == 0, preview_proc.stderr or preview_proc.stdout
        assert preview["ok"] is True
        assert preview["target"] == str(brain.resolve())
        assert preview["runtime_discovery"]["repo_root"] == str(REPO_ROOT.resolve())
        assert preview["runtime"]["compatible"] is True
        assert preview["package_integrity"]["present"] is True
        assert preview["package_integrity"]["ok"] is True
        assert preview["package_integrity"]["source_sha"] == "portable-e2e-source-sha"
        assert preview["plan"]["conflicts"] == []
        assert "System/BrainOS/config.yml" in preview["plan"]["copy"]
        assert not (brain / "System" / "BrainOS" / "config.yml").exists()
        assert legacy.read_text(encoding="utf-8") == "# Existing user knowledge\n"
        assert javis_readme.read_bytes() == javis_readme_before

        apply_proc, applied = _run(
            [sys.executable, "install.py", "--apply", "--compact"], cwd=package
        )
        assert apply_proc.returncode == 0, apply_proc.stderr or apply_proc.stdout
        assert applied["ok"] is True
        assert applied["system_sync"]["ok"] is True
        assert applied["system_sync"]["runtime_python"] == applied["runtime"]["runtime_dependencies"]["python"]
        assert applied["installed_contract"]["ok"] is True
        assert (brain / "System" / "BrainOS" / "config.yml").is_file()
        assert (brain / "skills" / "brain-manager" / "scripts" / "brain_os.py").is_file()
        for slug in ("ingest-source", "notes", "query-wiki", "lint-wiki"):
            skill = brain / "skills" / slug / "SKILL.md"
            assert skill.is_file()
            assert "javis_brain_os" in skill.read_text(encoding="utf-8")
        assert legacy.read_text(encoding="utf-8") == "# Existing user knowledge\n"
        assert javis_readme.read_bytes() == javis_readme_before

        config = BrainOSConfig.load(brain)
        scan = collect_files(config, full_hash=True)
        observed = [item.path for item in scan.observations]
        assert not any(path.startswith(PACKAGE_DIR_NAME + "/") for path in observed)
        assert scan.skipped_hidden >= 1
        assert "Custom Area/Existing.md" in observed
        assert LAUNCHER_NAME not in observed

        verify_proc, verified = _run(
            [sys.executable, "install.py", "--verify", "--compact"], cwd=package
        )
        assert verify_proc.returncode == 0, verify_proc.stderr or verify_proc.stdout
        assert verified["ok"] is True
        assert verified["installed_contract"]["ok"] is True
        assert verified["installed_contract"]["owned_payload"]["copy"] == []
        assert verified["installed_contract"]["owned_payload"]["conflicts"] == []
        assert legacy.read_text(encoding="utf-8") == "# Existing user knowledge\n"
        assert javis_readme.read_bytes() == javis_readme_before
    finally:
        shutil.rmtree(brain, ignore_errors=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher is executed only on Windows")
def test_windows_one_click_launcher_runs_full_safe_install(tmp_path: Path):
    brain = _brain_under_real_javis(tmp_path)
    try:
        _scaffold_exactly_like_javis_new_brain(brain)
        existing = brain / "Custom Area" / "Keep Me.md"
        existing.parent.mkdir(parents=True)
        existing.write_text("# Keep me\n", encoding="utf-8")
        existing_before = existing.read_bytes()

        _built, package, launcher, _zip_path, _names = _build_copy_and_extract_zip(
            brain, tmp_path, source_sha="windows-one-click-sha"
        )
        env = os.environ.copy()
        env["BRAIN_OS_NO_PAUSE"] = "1"
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
        assert "[1/3] PREVIEW" in output
        assert "[2/3] APPLY" in output
        assert "[3/3] VERIFY + DOCTOR" in output
        assert "INSTALL COMPLETE - BRAIN OS V1 VERIFIED" in output
        assert (brain / "System" / "BrainOS" / "config.yml").is_file()
        assert (brain / "skills" / "brain-manager" / "scripts" / "brain_os.py").is_file()
        assert existing.read_bytes() == existing_before

        verify_proc, verified = _run(
            [sys.executable, "install.py", "--verify", "--compact"], cwd=package
        )
        assert verify_proc.returncode == 0, verify_proc.stderr or verify_proc.stdout
        assert verified["ok"] is True
        assert verified["installed_contract"]["ok"] is True
    finally:
        shutil.rmtree(brain, ignore_errors=True)


def test_portable_package_tamper_fails_before_target_write(tmp_path: Path):
    brain = _brain_under_real_javis(tmp_path)
    try:
        _scaffold_exactly_like_javis_new_brain(brain)
        _built, package, _launcher, _zip_path, _names = _build_copy_and_extract_zip(
            brain, tmp_path, source_sha="tamper-test-sha"
        )
        config = package / "payload" / "System" / "BrainOS" / "config.yml"
        config.write_text(config.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")

        proc, payload = _run(
            [sys.executable, "install.py", "--apply", "--compact"], cwd=package
        )
        assert proc.returncode == 2
        assert payload["ok"] is False
        assert payload["package_integrity"]["ok"] is False
        assert "checksum" in payload["error"].casefold()
        assert not (brain / "System" / "BrainOS" / "config.yml").exists()
    finally:
        shutil.rmtree(brain, ignore_errors=True)
