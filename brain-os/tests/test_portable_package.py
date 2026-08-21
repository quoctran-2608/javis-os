from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BRAIN_OS_ROOT.parent
BUILDER = BRAIN_OS_ROOT / "tools" / "build_portable_package.py"
PACKAGE_NAME = "BrainOS-V1-Portable"


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


def test_portable_package_inside_fresh_brain_preview_apply_verify(tmp_path: Path):
    brain = _brain_under_real_javis(tmp_path)
    try:
        legacy = brain / "Custom Area" / "Existing.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("# Existing user knowledge\n", encoding="utf-8")

        build_proc, built = _run(
            [
                sys.executable,
                str(BUILDER),
                "--output-dir",
                str(brain),
                "--source-sha",
                "portable-e2e-source-sha",
                "--compact",
            ],
            cwd=REPO_ROOT,
        )
        assert build_proc.returncode == 0, build_proc.stderr or build_proc.stdout
        assert built["ok"] is True
        assert built["source_sha"] == "portable-e2e-source-sha"

        package = brain / PACKAGE_NAME
        zip_path = brain / f"{PACKAGE_NAME}.zip"
        assert package.is_dir()
        assert zip_path.is_file()
        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["package_schema"] == 1
        assert manifest["source_sha"] == "portable-e2e-source-sha"
        assert manifest["ownership"]["system_skills"] == "javis-system-sync"
        assert not (package / "payload" / ".javis").exists()
        assert not (package / "payload" / ".claude").exists()
        assert not (package / "payload" / "Notes").exists()
        assert not (package / "payload" / "sources").exists()
        assert not (package / "payload" / "wiki").exists()

        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        assert f"{PACKAGE_NAME}/install.py" in names
        assert f"{PACKAGE_NAME}/manifest.json" in names
        assert f"{PACKAGE_NAME}/checksums.sha256" in names
        assert f"{PACKAGE_NAME}/payload/System/BrainOS/config.yml" in names
        assert not any(f"{PACKAGE_NAME}/payload/.javis/" in name for name in names)
        assert not any(f"{PACKAGE_NAME}/payload/.claude/" in name for name in names)

        # Run from inside the portable folder, not the Javis repo. No Brain path and no
        # --javis-root are provided: target defaults to package parent and runtime discovery
        # must recognize <repo>/brains/<brain>.
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

        apply_proc, applied = _run(
            [sys.executable, "install.py", "--apply", "--compact"], cwd=package
        )
        assert apply_proc.returncode == 0, apply_proc.stderr or apply_proc.stdout
        assert applied["ok"] is True
        assert applied["system_sync"]["ok"] is True
        assert applied["installed_contract"]["ok"] is True
        assert (brain / "System" / "BrainOS" / "config.yml").is_file()
        assert (brain / "skills" / "brain-manager" / "scripts" / "brain_os.py").is_file()
        for slug in ("ingest-source", "notes", "query-wiki", "lint-wiki"):
            skill = brain / "skills" / slug / "SKILL.md"
            assert skill.is_file()
            assert "javis_brain_os" in skill.read_text(encoding="utf-8")
        assert legacy.read_text(encoding="utf-8") == "# Existing user knowledge\n"

        verify_proc, verified = _run(
            [sys.executable, "install.py", "--verify", "--compact"], cwd=package
        )
        assert verify_proc.returncode == 0, verify_proc.stderr or verify_proc.stdout
        assert verified["ok"] is True
        assert verified["installed_contract"]["ok"] is True
        assert verified["installed_contract"]["owned_payload"]["copy"] == []
        assert verified["installed_contract"]["owned_payload"]["conflicts"] == []
        assert legacy.read_text(encoding="utf-8") == "# Existing user knowledge\n"
    finally:
        shutil.rmtree(brain, ignore_errors=True)


def test_portable_package_tamper_fails_before_target_write(tmp_path: Path):
    brain = _brain_under_real_javis(tmp_path)
    try:
        build_proc, built = _run(
            [
                sys.executable,
                str(BUILDER),
                "--output-dir",
                str(brain),
                "--source-sha",
                "tamper-test-sha",
                "--compact",
            ],
            cwd=REPO_ROOT,
        )
        assert build_proc.returncode == 0, build_proc.stderr or build_proc.stdout
        assert built["ok"] is True
        package = brain / PACKAGE_NAME
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
        assert not (brain / ".javis").exists()
    finally:
        shutil.rmtree(brain, ignore_errors=True)
