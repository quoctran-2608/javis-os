from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = BRAIN_OS_ROOT / "template"
SCRIPTS = TEMPLATE / "skills" / "brain-manager" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from brain_os_lib.config import BrainOSConfig
from brain_os_lib.reconcile import reconcile_brain


@pytest.fixture()
def brain(tmp_path: Path) -> Path:
    root = tmp_path / "Brain Pilot"
    shutil.copytree(TEMPLATE, root)
    return root


def _run_recovery(brain: Path, command: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(brain / "skills/brain-manager/scripts/brain_recovery.py"),
            "--brain-root",
            str(brain),
            "--compact",
            command,
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _pilot(brain: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(brain / "skills/brain-manager/scripts/brain_pilot.py"),
            "--brain-root",
            str(brain),
            "--compact",
            "check",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_fresh_prepared_template_is_ready_for_initial_dry_run_pilot(brain: Path):
    note = brain / "Notes" / "Pilot.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Pilot\nReal-vault fixture.\n", encoding="utf-8")
    cfg = BrainOSConfig.load(brain)
    assert reconcile_brain(cfg, full_hash=True).ok is True

    prepared = _run_recovery(brain, "prepare", "--apply")
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr

    check = _pilot(brain)
    assert check.returncode == 0, check.stdout + check.stderr
    payload = json.loads(check.stdout)
    assert payload["pilot_ready"] is True
    assert payload["blockers"] == []
    assert payload["config"]["dry_run"] is True
    assert payload["brain_watch"]["enabled"] is False
    assert payload["compatibility"]["ok"] is True
    assert payload["recovery"]["rebuild_ready"] is True
    assert payload["runtime_dependencies"]["yaml"] is True
    assert payload["runtime_dependencies"]["pypdf"] is True


def test_pilot_check_blocks_when_brain_watch_is_enabled(brain: Path):
    cfg = BrainOSConfig.load(brain)
    assert reconcile_brain(cfg, full_hash=True).ok is True
    assert _run_recovery(brain, "prepare", "--apply").returncode == 0

    loop = brain / "Javis" / "loops" / "brain-watch.md"
    text = loop.read_text(encoding="utf-8")
    loop.write_text(text.replace("enabled: false", "enabled: true", 1), encoding="utf-8")

    check = _pilot(brain)
    assert check.returncode == 2
    payload = json.loads(check.stdout)
    assert payload["pilot_ready"] is False
    assert "brain_watch_must_remain_disabled_for_initial_pilot" in payload["blockers"]


def test_pilot_check_blocks_when_dry_run_is_disabled(brain: Path):
    cfg = BrainOSConfig.load(brain)
    assert reconcile_brain(cfg, full_hash=True).ok is True
    assert _run_recovery(brain, "prepare", "--apply").returncode == 0

    config_path = brain / "System" / "BrainOS" / "config.yml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["dry_run"] = False
    config_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    check = _pilot(brain)
    assert check.returncode == 2
    payload = json.loads(check.stdout)
    assert payload["pilot_ready"] is False
    assert "dry_run_must_be_true_for_initial_pilot" in payload["blockers"]
