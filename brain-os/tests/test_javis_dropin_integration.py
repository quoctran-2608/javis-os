from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest


BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BRAIN_OS_ROOT.parent
TEMPLATE = BRAIN_OS_ROOT / "template"
SERVER = REPO_ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import system_sync


def _plugin():
    path = REPO_ROOT / "system" / "plugins" / "brain-os" / "plugin.py"
    spec = importlib.util.spec_from_file_location("brain_os_bridge_plugin", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Ctx:
    def __init__(self, root: Path):
        self.vault_root = str(root)


@pytest.fixture()
def arbitrary_brain(tmp_path: Path) -> Path:
    root = tmp_path / "brains" / "Khach Hang Bat Ky"
    root.mkdir(parents=True)
    legacy = root / "Custom Area" / "Existing.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# Existing user knowledge\n", encoding="utf-8")
    shutil.copytree(TEMPLATE, root, dirs_exist_ok=True)
    return root


def test_bridge_uses_ctx_vault_root_even_when_process_cwd_is_repo(arbitrary_brain: Path, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)  # model real chat cwd=/app, not cwd=<brain>
    plugin = _plugin()
    payload = json.loads(plugin._handle({"op": "scan"}, Ctx(arbitrary_brain)))
    assert payload["ok"] is True
    assert payload["javis_bridge"]["active_brain"] == str(arbitrary_brain.resolve())
    assert payload["javis_bridge"]["cwd_independent"] is True
    assert (arbitrary_brain / ".javis" / "brain-index.db").is_file()
    assert (arbitrary_brain / "Custom Area" / "Existing.md").read_text(encoding="utf-8") == "# Existing user knowledge\n"


def test_capture_goes_to_active_arbitrary_brain_not_project_root(arbitrary_brain: Path, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    plugin = _plugin()
    body = "Một note được capture qua Javis bridge.\n"
    payload = json.loads(plugin._handle({"op": "capture_note", "body": body, "apply": True}, Ctx(arbitrary_brain)))
    assert payload["ok"] is True
    working = payload["result"]["working_path"]
    assert (arbitrary_brain / working).is_file()
    assert body in (arbitrary_brain / working).read_text(encoding="utf-8")
    assert not (REPO_ROOT / working).exists()


def test_javis_system_sync_keeps_brain_os_system_skill_contract(arbitrary_brain: Path):
    system_sync._SYNCED_ROOTS.discard(str(arbitrary_brain.resolve()))
    result = system_sync.sync_brain(arbitrary_brain)
    assert result["ok"] is True
    for slug in ("ingest-source", "notes", "query-wiki", "lint-wiki"):
        app_skill = REPO_ROOT / ".claude" / "skills" / slug / "SKILL.md"
        brain_skill = arbitrary_brain / "skills" / slug / "SKILL.md"
        assert brain_skill.is_file()
        text = brain_skill.read_text(encoding="utf-8")
        assert "javis_brain_os" in text
        assert "python skills/brain-manager/scripts" not in text
        assert system_sync.skill_hash(text) == system_sync.skill_hash(app_skill.read_text(encoding="utf-8"))


def test_bridge_fails_closed_on_plain_brain_without_brain_os(tmp_path: Path):
    plain = tmp_path / "brains" / "Plain"
    plain.mkdir(parents=True)
    payload = json.loads(_plugin()._handle({"op": "scan"}, Ctx(plain)))
    assert payload["ok"] is False
    assert "chưa cài Brain OS" in payload["error"]


def test_runtime_requirements_include_document_parser():
    text = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").casefold()
    assert "pypdf" in text
