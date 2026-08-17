from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_brain_os_stage4_gate() -> None:
    root = Path(__file__).resolve().parents[2]
    targets = [
        "brain-os/tests/test_foundation.py",
        "brain-os/tests/test_core_stage2.py",
        "brain-os/tests/test_stage3_scanner.py",
        "brain-os/tests/test_stage3_identity_edges.py",
        "brain-os/tests/test_stage4_classifier.py",
    ]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *targets],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            "Brain OS gate failed\n\nSTDOUT:\n"
            + result.stdout
            + "\nSTDERR:\n"
            + result.stderr
        )
