"""Canary cho công cụ đồng bộ fork: URL parsing và fail-safe không được trôi."""
from _paths import ROOT  # noqa: E402,F401

import importlib.util
import subprocess
import sys
from pathlib import Path


TOOL = ROOT / "tools" / "sync_upstream.py"
spec = importlib.util.spec_from_file_location("sync_upstream", TOOL)
sync = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(sync)

fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


check("đọc được SSH URL", sync.repo_slug("git@github.com:quoctran-2608/javis-os.git")
      == "quoctran-2608/javis-os")
check("đọc được HTTPS URL", sync.repo_slug(
    "https://github.com/blogminhquy/javis-os.git") == "blogminhquy/javis-os")
check("fork production được ghim rõ", sync.FORK_REPO == "quoctran-2608/javis-os")
check("upstream gốc được ghim rõ", sync.UPSTREAM_REPO == "blogminhquy/javis-os")

source = TOOL.read_text(encoding="utf-8")
check("kiểm cả file untracked trước merge", "--untracked-files=normal" in source)
check("chỉ fast-forward main theo origin", '["merge", "--ff-only", "origin/main"]' in source)
check("merge upstream trên nhánh sync", '["merge", "--no-edit", "upstream/main"]' in source)
check("không tự push production", 'run(["push"' not in source)
check("không tự giải hoặc bỏ conflict", "merge có conflict" in source.lower())

compiled = subprocess.run(
    [sys.executable, "-m", "py_compile", str(TOOL)],
    capture_output=True,
    text=True,
)
check("tool byte-compile được", compiled.returncode == 0)

print()
if fails:
    print(f"{len(fails)} FAIL: {fails}")
    raise SystemExit(1)
print("OK - test_sync_upstream: tất cả pass")
