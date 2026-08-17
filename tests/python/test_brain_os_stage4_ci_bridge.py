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
    # The repository CI executes each tests/python/test_*.py file directly with
    # `python file.py`, not via pytest. Print nested output so the Actions log is
    # auditable, and call this function explicitly from __main__ below.
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.returncode != 0:
        raise AssertionError(f"Brain OS gate failed with exit code {result.returncode}")


if __name__ == "__main__":
    test_brain_os_stage4_gate()
