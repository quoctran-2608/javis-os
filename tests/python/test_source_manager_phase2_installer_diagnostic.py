"""Failure-only diagnostic for Source Manager Phase 2 installer.

Keep this tiny: if apply fails in CI, surface the installer's exact JSON/stderr before
stopping. It uses a fresh Brain and the real upstream system_sync seed.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_STATE = Path(tempfile.mkdtemp(prefix="javis-sm-p2-diag-state-"))
os.environ["JAVIS_STATE_DIR"] = str(_STATE)

import system_sync  # noqa: E402

_BRAIN = Path(tempfile.mkdtemp(prefix="javis-sm-p2-diag-brain-"))
_INSTALLER = ROOT / "source-manager" / "install_source_manager.py"

seed = system_sync.sync_brain(_BRAIN)
assert seed.get("ok"), seed

cmd = [
    sys.executable,
    str(_INSTALLER),
    "--brain", str(_BRAIN),
    "--state-dir", str(_STATE),
    "--apply",
    "--json",
]
r = subprocess.run(
    cmd,
    cwd=str(ROOT),
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
)

if r.returncode != 0:
    print("PHASE2_INSTALLER_DIAGNOSTIC_RETURN_CODE:", r.returncode)
    print("PHASE2_INSTALLER_DIAGNOSTIC_STDOUT_BEGIN")
    print(r.stdout)
    print("PHASE2_INSTALLER_DIAGNOSTIC_STDOUT_END")
    print("PHASE2_INSTALLER_DIAGNOSTIC_STDERR_BEGIN")
    print(r.stderr)
    print("PHASE2_INSTALLER_DIAGNOSTIC_STDERR_END")
    raise SystemExit(1)

try:
    payload = json.loads(r.stdout)
except Exception as exc:
    print("PHASE2_INSTALLER_DIAGNOSTIC_BAD_JSON:", type(exc).__name__, exc)
    print(r.stdout)
    raise SystemExit(1)

assert payload.get("applied") is True, payload
print("OK - Phase 2 installer diagnostic apply succeeded")
