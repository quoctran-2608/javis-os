"""TEMPORARY CI BRIDGE for Brain OS V1 Gate 3.

This file is intentionally removed after the gate passes. The final Brain OS
branch must keep all persistent implementation/tests under `brain-os/` only.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


if __name__ == "__main__":
    run(
        [
            sys.executable,
            "-m",
            "compileall",
            "-q",
            "brain-os/template/skills/brain-manager/scripts",
        ]
    )
    run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "brain-os/tests/test_foundation.py",
            "brain-os/tests/test_core_stage2.py",
            "brain-os/tests/test_stage3_scanner.py",
            "brain-os/tests/test_stage3_identity_edges.py",
        ]
    )
