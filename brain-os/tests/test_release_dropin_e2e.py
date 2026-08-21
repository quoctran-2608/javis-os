from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BRAIN_OS_ROOT.parent
INSTALLER = BRAIN_OS_ROOT / "install_brain_os.py"
SERVER = REPO_ROOT / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import system_sync


class Ctx:
    def __init__(self, root: Path):
        self.vault_root = str(root)


def _plugin():
    path = REPO_ROOT / "system" / "plugins" / "brain-os" / "plugin.py"
    spec = importlib.util.spec_from_file_location("brain_os_release_bridge", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install(brain: Path, *, apply: bool) -> tuple[subprocess.CompletedProcess[str], dict]:
    cmd = [sys.executable, str(INSTALLER), str(brain), "--compact"]
    if apply:
        cmd.append("--apply")
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc, payload


def _bridge(plugin, brain: Path, args: dict) -> dict:
    return json.loads(plugin._handle(args, Ctx(brain)))


def test_clean_dropin_release_path_end_to_end(tmp_path: Path, monkeypatch):
    """Exercise the path a real release uses, from installer to governed ingest.

    This intentionally does not copy brain-os/template into the fixture. The target starts
    as an arbitrary existing Brain, is previewed/applied by the public installer CLI,
    receives app-owned system skills through Javis system_sync, then is operated only
    through the bundled active-Brain bridge.
    """
    brain = tmp_path / "brains" / "Release Brain"
    brain.mkdir(parents=True)
    legacy = brain / "Custom Area" / "Existing.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# Existing user knowledge\n", encoding="utf-8")

    preview_proc, preview = _install(brain, apply=False)
    assert preview_proc.returncode == 0, preview_proc.stderr or preview_proc.stdout
    assert preview["ok"] is True
    assert preview["apply"] is False
    assert preview["runtime"]["compatible"] is True
    assert preview["plan"]["conflicts"] == []
    assert "System/BrainOS/config.yml" in preview["plan"]["copy"]
    assert not (brain / "System" / "BrainOS" / "config.yml").exists()
    assert legacy.read_text(encoding="utf-8") == "# Existing user knowledge\n"

    apply_proc, applied = _install(brain, apply=True)
    assert apply_proc.returncode == 0, apply_proc.stderr or apply_proc.stdout
    assert applied["ok"] is True
    assert applied["apply"] is True
    assert applied["plan"]["conflicts"] == []
    assert (brain / "System" / "BrainOS" / "config.yml").is_file()
    assert (brain / "skills" / "brain-manager" / "scripts" / "brain_os.py").is_file()
    assert legacy.read_text(encoding="utf-8") == "# Existing user knowledge\n"

    system_sync._SYNCED_ROOTS.discard(str(brain.resolve()))
    sync = system_sync.sync_brain(brain)
    assert sync["ok"] is True
    for slug in ("ingest-source", "notes", "query-wiki", "lint-wiki"):
        skill = brain / "skills" / slug / "SKILL.md"
        assert skill.is_file()
        text = skill.read_text(encoding="utf-8")
        assert "javis_brain_os" in text
        assert "python skills/brain-manager/scripts" not in text

    monkeypatch.chdir(REPO_ROOT)
    plugin = _plugin()
    scan = _bridge(plugin, brain, {"op": "scan"})
    assert scan["ok"] is True
    assert scan["javis_bridge"]["active_brain"] == str(brain.resolve())
    assert scan["javis_bridge"]["cwd_independent"] is True
    assert (brain / ".javis" / "brain-index.db").is_file()

    source = tmp_path / "External Release Source.md"
    source.write_text(
        "# Release source\n\nOne stable Living Note used for final-head E2E proof.\n",
        encoding="utf-8",
    )
    first = _bridge(
        plugin,
        brain,
        {
            "op": "import_markdown",
            "source": str(source),
            "document_type": "living_note",
            "category": "notes_personal_learning",
            "apply": True,
        },
    )
    assert first["ok"] is True
    first_result = first["result"]
    working = first_result["working_path"]
    snapshot = first_result["snapshot_path"]
    assert not Path(working).is_absolute()
    assert not Path(snapshot).is_absolute()
    assert (brain / working).is_file()
    assert (brain / snapshot).read_bytes() == source.read_bytes()

    renamed = tmp_path / "Renamed External Source.md"
    renamed.write_bytes(source.read_bytes())
    second = _bridge(
        plugin,
        brain,
        {"op": "import_markdown", "source": str(renamed), "apply": True},
    )
    assert second["ok"] is True
    second_result = second["result"]
    assert second_result["source_id"] == first_result["source_id"]
    assert second_result["working_path"] == working
    assert second_result["snapshot_path"] == snapshot
    assert second_result["reused_snapshot"] is True
    assert second_result["reused_working_copy"] is True
    manifests = list((brain / ".javis" / "originals" / "imports").glob("*/manifest.json"))
    assert len(manifests) == 1

    recorded = _bridge(
        plugin,
        brain,
        {"op": "record_ingest", "path": working, "compounded": True},
    )
    assert recorded["ok"] is True
    assert recorded["source_id"] == first_result["source_id"]
    assert recorded["state"] == "compounded"
    assert recorded["last_ingested_hash"] == recorded["content_hash"]
    assert recorded["recovery_checkpointed"] is True
    assert recorded["writes_user_files"] is False
    assert recorded["derived_state_only"] is True

    final_scan = _bridge(plugin, brain, {"op": "scan"})
    assert final_scan["ok"] is True
    assert legacy.read_text(encoding="utf-8") == "# Existing user knowledge\n"
    assert len(list((brain / ".javis" / "originals" / "imports").glob("*/manifest.json"))) == 1
