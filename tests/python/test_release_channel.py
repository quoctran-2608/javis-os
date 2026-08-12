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

check("release là 0.28.3", version == "0.28.3")

dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
compat = (ROOT / "system" / "agy-compatible.sh").read_text(encoding="utf-8")
check("Docker image cài QEMU ARM64 fallback cho Antigravity",
      "qemu-user libc6-arm64-cross" in dockerfile)
check("Docker build smoke-test đường Antigravity emulation",
      "JAVIS_ANTIGRAVITY_FORCE_EMULATION=1 agy --version" in dockerfile)
agy_block = dockerfile[
    dockerfile.index("COPY tools/install_antigravity_compat.py"):
    dockerfile.index("WORKDIR /app")
]
check("Docker build fail-closed nếu fallback Antigravity hỏng", "|| echo" not in agy_block)
check("wrapper tự bắt CPU thiếu PCLMUL", "pclmulqdq" in compat and "qemu-aarch64" in compat)
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

deploy = (ROOT / "DEPLOY.md").read_text(encoding="utf-8")
multi = (ROOT / "docker-compose.multi.yml").read_text(encoding="utf-8")
proxy = (ROOT / "docker-compose.proxy.yml").read_text(encoding="utf-8")
console = (ROOT / "dashboard" / "console.js").read_text(encoding="utf-8")
check("URL tải compose production không quay về upstream",
      "raw.githubusercontent.com/blogminhquy/javis-os" not in deploy + multi + proxy)
check("link tài liệu trong dashboard không quay về upstream",
      "github.com/blogminhquy/javis-os" not in console)

print()
if fails:
    print(f"{len(fails)} FAIL: {fails}")
    sys.exit(1)
print("OK - test_release_channel: tất cả pass")
