#!/usr/bin/env python3
"""Chuẩn bị một nhánh đồng bộ upstream an toàn cho fork production.

Luồng:
  1. Yêu cầu cây Git sạch và đang ở nhánh main.
  2. Kiểm tra origin là fork, upstream là repo gốc.
  3. Fetch cả hai remote.
  4. Fast-forward main theo origin/main.
  5. Tạo nhánh sync/upstream-<VERSION> và merge upstream/main.

Script không push, không sửa main sau merge và không tự giải conflict. Sau khi
test đạt, người vận hành tự merge nhánh sync vào main rồi push origin.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FORK_REPO = "quoctran-2608/javis-os"
UPSTREAM_REPO = "blogminhquy/javis-os"


def run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print("$ git " + " ".join(args), flush=True)
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def capture(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "").strip())
    return (result.stdout or "").strip()


def repo_slug(url: str) -> str:
    match = re.search(r"(?:github\.com[:/])([^/]+/[^/]+?)(?:\.git)?$", url or "")
    return match.group(1) if match else ""


def remote_slug(name: str) -> str:
    return repo_slug(capture(["remote", "get-url", name]))


def version_from_ref(ref: str = "upstream/main") -> str:
    text = capture(["show", f"{ref}:VERSION"])
    version = text.strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError(f"VERSION trên {ref} không hợp lệ: {version!r}")
    return version


def require_safe_state() -> None:
    dirty = capture(["status", "--porcelain", "--untracked-files=normal"])
    if dirty:
        raise RuntimeError(
            "Cây Git đang có thay đổi chưa commit. Commit/push hoặc tự cất chúng trước."
        )
    branch = capture(["branch", "--show-current"])
    if branch != "main":
        raise RuntimeError(f"Phải chạy từ nhánh main; hiện đang ở {branch or 'detached HEAD'}.")
    origin = remote_slug("origin")
    upstream = remote_slug("upstream")
    if origin.lower() != FORK_REPO.lower():
        raise RuntimeError(f"origin phải là {FORK_REPO}, hiện là {origin or 'không rõ'}.")
    if upstream.lower() != UPSTREAM_REPO.lower():
        raise RuntimeError(
            f"upstream phải là {UPSTREAM_REPO}, hiện là {upstream or 'không rõ'}."
        )


def prepare(branch_name: str = "") -> int:
    require_safe_state()
    run(["fetch", "origin", "--prune"])
    run(["fetch", "upstream", "--prune"])
    run(["merge", "--ff-only", "origin/main"])
    version = version_from_ref()
    branch = branch_name or f"sync/upstream-{version}"
    if capture(["branch", "--list", branch]):
        raise RuntimeError(
            f"Nhánh {branch} đã tồn tại. Xóa/đổi tên nhánh cũ hoặc dùng --branch tên-khác."
        )
    run(["switch", "-c", branch])
    merged = run(["merge", "--no-edit", "upstream/main"], check=False)
    if merged.returncode:
        print(
            "\nMerge có conflict. Giữ nguyên trạng thái hiện tại để xử lý; "
            "sau đó chạy test trước khi merge vào main.",
            file=sys.stderr,
        )
        return merged.returncode
    print(
        "\nĐã tạo nhánh sync sạch. Chạy:\n"
        "  python tests/python/test_antigravity_cli.py\n"
        "  python tests/python/test_luot_chat_antigravity.py\n"
        "  python tests/python/test_update.py\n"
        "Nếu đạt:\n"
        "  git switch main\n"
        f"  git merge --no-ff {branch}\n"
        "  git push origin main",
        flush=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tạo nhánh merge upstream an toàn cho fork Javis OS."
    )
    parser.add_argument("--branch", default="", help="Tên nhánh sync tùy chọn.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Chỉ kiểm tra cây Git và remote, không fetch/merge.",
    )
    args = parser.parse_args()
    try:
        if args.check:
            require_safe_state()
            print("OK: cây Git sạch, main/origin/upstream đúng hợp đồng.")
            return 0
        return prepare(args.branch)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
