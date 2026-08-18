from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = BRAIN_OS_ROOT / "tools" / "validate_foundation.py"
TEMPLATE = BRAIN_OS_ROOT / "template"


def _load_validator():
    spec = importlib.util.spec_from_file_location("brain_os_validate_foundation", VALIDATOR)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Không load được validator: {VALIDATOR}")
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotation/module metadata through sys.modules.
    # Register dynamic modules before exec_module so this loader is reliable on
    # Python 3.12+ as well as local runtimes.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_foundation_gate_is_clean():
    validator = _load_validator()
    report = validator.run(TEMPLATE)
    assert report.errors == [], "\n".join(report.errors)
    assert report.ok is True


def test_foundation_starts_fail_safe():
    validator = _load_validator()
    config = validator.load_yaml(TEMPLATE / "System/BrainOS/config.yml")
    assert config["dry_run"] is True
    assert config["folders"]["allow_auto_move"] is False
    assert config["folders"]["allow_auto_create"] is False
    assert config["tags"]["allow_auto_create"] is False


def test_protected_zones_never_ingest():
    validator = _load_validator()
    config = validator.load_yaml(TEMPLATE / "System/BrainOS/config.yml")
    zones = config["zones"]
    for zone in ("00 - Dashboard", "wiki", ".javis"):
        assert zones[zone]["ingest"] == "never"


def test_capability_and_system_trees_are_excluded_from_brain_scan():
    validator = _load_validator()
    config = validator.load_yaml(TEMPLATE / "System/BrainOS/config.yml")
    ignored = set(config["ignore_paths"])
    required = {"skills", "agents", "workflows", "plugins", "System", "Javis", ".javis"}
    assert required <= ignored


def test_stage3_scan_policy_is_fail_safe():
    validator = _load_validator()
    config = validator.load_yaml(TEMPLATE / "System/BrainOS/config.yml")
    scan = config["scan"]
    assert scan["extensions"] == [".md", ".markdown"]
    assert scan["ignore_hidden"] is True
    assert scan["follow_symlinks"] is False
    assert scan["hash_retries"] >= 1
    assert scan["max_snapshot_bytes"] > 0
    assert scan["emit_unchanged_events"] is False
    assert scan["deletion_policy"] == "mark_missing"
