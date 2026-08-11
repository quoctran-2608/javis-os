"""Canary release fork: update source, image, volumes và Windows CI phải đi cùng nhau."""
from _paths import ROOT  # noqa: E402,F401

import re
import sys


fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


main = (ROOT / "server" / "main.py").read_text(encoding="utf-8")
compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
hostinger = (ROOT / "docker-compose.hostinger.yml").read_text(encoding="utf-8")
build = (ROOT / "docker-compose.build.yml").read_text(encoding="utf-8")
ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
publish = (ROOT / ".github" / "workflows" / "docker-publish.yml").read_text(
    encoding="utf-8")
requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

check("release là 0.27.0", version == "0.27.0")
check("backend mặc định kiểm version từ fork",
      '"quoctran-2608/javis-os"' in main and "JAVIS_UPDATE_REPO" in main)
check("backend công bố image repo cho UI rollback",
      "JAVIS_IMAGE_REPO" in main and '"image_repo": IMAGE_REPO' in main)

for name, text in (
    ("compose production", compose),
    ("compose Hostinger", hostinger),
    ("compose build", build),
):
    check(f"{name} dùng image fork", "ghcr.io/quoctran-2608/javis-os" in text)
    check(f"{name} giữ volume Claude", "/home/javis/.claude" in text)
    check(f"{name} giữ volume Codex", "/home/javis/.codex" in text)
    check(f"{name} giữ volume Antigravity", "/home/javis/.gemini" in text)

check("Docker workflow build theo chính fork đang chạy",
      "ghcr.io/${GITHUB_REPOSITORY,,}" in publish)
check("Windows CI tồn tại", "windows-latest" in ci and "Windows CLI auth" in ci)
check("Windows CI chạy canary OAuth Antigravity",
      "test_antigravity_cli.py" in ci and "wrapped_oauth_url" in ci)
check("pywinpty chỉ cài trên Windows",
      bool(re.search(r"pywinpty==3\.0\.5;\s*sys_platform\s*==\s*[\"']win32[\"']",
                     requirements)))

old_image = "ghcr.io/blogminhquy/javis-os"
runtime_files = {
    "server/main.py": main,
    "docker-compose.yml": compose,
    "docker-compose.hostinger.yml": hostinger,
    "docker-compose.build.yml": build,
}
check("runtime không còn pull image upstream cũ",
      not any(old_image in text for text in runtime_files.values()))

print()
if fails:
    print(f"{len(fails)} FAIL: {fails}")
    sys.exit(1)
print("OK - test_release_channel: tất cả pass")
