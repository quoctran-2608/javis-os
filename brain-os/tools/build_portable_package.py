#!/usr/bin/env python3
"""Build a deterministic Brain OS V1 portable installer ZIP.

The resulting archive is named ``BrainOS-V1-Portable.zip`` but contains one hidden
``.brain-os-installer`` directory. A user extracts that directory directly inside a
fresh Brain and runs ``python .brain-os-installer/install.py``. The leading dot is a
safety boundary: Brain OS has ``scan.ignore_hidden: true``, so the temporary installer
and its Markdown documentation can never be mistaken for user knowledge while present.

Only Brain-OS-owned overlay files are bundled; app-owned system skills/mirrors and all
runtime/user-derived data are deliberately excluded.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

PACKAGE_DIR_NAME = ".brain-os-installer"
ARCHIVE_NAME = "BrainOS-V1-Portable.zip"
PACKAGE_SCHEMA = 1
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_installer(path: Path):
    spec = importlib.util.spec_from_file_location("brain_os_portable_installer", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Không nạp được installer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source_sha(repo_root: Path, explicit: str) -> str:
    if explicit.strip():
        return explicit.strip()
    env = os.getenv("BRAIN_OS_SOURCE_SHA", "").strip()
    if env:
        return env
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    value = (proc.stdout or "").strip()
    return value if proc.returncode == 0 else "unknown"


def _app_version(repo_root: Path) -> str:
    path = repo_root / "VERSION"
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _release_text(source_sha: str, app_version: str) -> str:
    return f"""# Brain OS V1 Portable Installer

Source commit: `{source_sha}`
Javis version at build: `{app_version or 'unknown'}`

## Cách dùng cho Brain mới

Copy `{ARCHIVE_NAME}` vào Brain mới rồi giải nén tại đó. ZIP tạo ra thư mục ẩn
`{PACKAGE_DIR_NAME}` trực tiếp bên trong Brain, ví dụ:

```text
brains/MinhSecondBrain/
├── ... dữ liệu/scaffold Javis mới ...
├── {ARCHIVE_NAME}
└── {PACKAGE_DIR_NAME}/
```

Thư mục installer bắt đầu bằng dấu chấm có chủ ý để Brain OS scanner bỏ qua toàn bộ
package trong lúc cài (`scan.ignore_hidden: true`).

Sau đó chạy **preview trước** từ Brain root:

```bash
python {PACKAGE_DIR_NAME}/install.py
```

Nếu `ok: true`, `runtime.compatible: true`, `package_integrity.ok: true` và
`plan.conflicts` rỗng thì mới apply:

```bash
python {PACKAGE_DIR_NAME}/install.py --apply
```

Cuối cùng verify read-only:

```bash
python {PACKAGE_DIR_NAME}/install.py --verify
```

Nếu Brain nằm ngoài `<Javis>/brains/`, thêm `--javis-root <đường-dẫn-Javis>` cho cả ba lệnh.
Sau khi verify PASS có thể xoá `{PACKAGE_DIR_NAME}` và `{ARCHIVE_NAME}`; Brain OS đã được
cài ở đúng các path do nó quản lý trong Brain.

## Những gì package KHÔNG chứa

- `.javis/brain-index.db` hoặc recovery/runtime state;
- Notes, sources, wiki hay dữ liệu người dùng/test;
- `.claude` mirrors;
- system skills do Javis `system_sync` sở hữu;
- Python/runtime cache như `__pycache__`, `.pyc`, `.pyo` và tool cache tạm.

Installer không overwrite file khác nội dung ở path Brain OS quản lý và không xoá file người dùng.
"""


def _write_deterministic_zip(package_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    root = package_dir.parent
    files = sorted(p for p in package_dir.rglob("*") if p.is_file())
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            arcname = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(arcname, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def build(repo_root: Path, output_dir: Path, *, source_sha: str = "") -> dict:
    repo_root = repo_root.resolve()
    output_dir = output_dir.resolve()
    brain_os_root = repo_root / "brain-os"
    installer_path = brain_os_root / "install_brain_os.py"
    template = brain_os_root / "template"
    if not installer_path.is_file() or not template.is_dir():
        raise RuntimeError(f"Repo không có Brain OS source hợp lệ: {repo_root}")

    installer = _load_installer(installer_path)
    source_sha = _source_sha(repo_root, source_sha)
    app_version = _app_version(repo_root)

    output_dir.mkdir(parents=True, exist_ok=True)
    package_dir = output_dir / PACKAGE_DIR_NAME
    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True)
    payload_dir = package_dir / "payload"
    payload_dir.mkdir()

    shutil.copy2(installer_path, package_dir / "install.py")
    (package_dir / "RELEASE.md").write_text(
        _release_text(source_sha, app_version), encoding="utf-8", newline="\n"
    )

    payload_files: dict[str, str] = {}
    for src, rel in installer.source_files(template):
        dst = payload_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        payload_files[rel.as_posix()] = sha256(dst)

    package_files: dict[str, str] = {
        "install.py": sha256(package_dir / "install.py"),
        "RELEASE.md": sha256(package_dir / "RELEASE.md"),
    }
    for rel in sorted(payload_files):
        package_files[f"payload/{rel}"] = payload_files[rel]

    manifest = {
        "package_schema": PACKAGE_SCHEMA,
        "name": "Brain OS V1 Portable Installer",
        "package_directory": PACKAGE_DIR_NAME,
        "archive_name": ARCHIVE_NAME,
        "scanner_hidden": True,
        "source_sha": source_sha,
        "javis_version": app_version,
        "payload_file_count": len(payload_files),
        "payload_files": payload_files,
        "package_files": package_files,
        "ownership": {
            "payload": "brain-os",
            "system_skills": "javis-system-sync",
            "derived_state": "not-packaged",
            "user_data": "not-packaged",
        },
        "excluded": [
            ".javis/**",
            ".claude/**",
            "Notes/**",
            "sources/**",
            "wiki/**",
            "skills/ingest-source/**",
            "skills/notes/**",
            "skills/query-wiki/**",
            "skills/lint-wiki/**",
            "**/__pycache__/**",
            "**/*.pyc",
            "**/*.pyo",
            "**/.pytest_cache/**",
            "**/.mypy_cache/**",
            "**/.ruff_cache/**",
        ],
    }
    manifest_path = package_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    checksum_lines = [f"{digest}  {rel}" for rel, digest in sorted(package_files.items())]
    checksum_lines.append(f"{sha256(manifest_path)}  manifest.json")
    (package_dir / "checksums.sha256").write_text(
        "\n".join(checksum_lines) + "\n", encoding="utf-8", newline="\n"
    )

    zip_path = output_dir / ARCHIVE_NAME
    _write_deterministic_zip(package_dir, zip_path)
    return {
        "ok": True,
        "action": "build-brain-os-portable-package",
        "source_sha": source_sha,
        "javis_version": app_version,
        "package_dir": str(package_dir),
        "package_directory": PACKAGE_DIR_NAME,
        "zip_path": str(zip_path),
        "zip_sha256": sha256(zip_path),
        "payload_file_count": len(payload_files),
        "package_file_count": len(package_files) + 2,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Brain OS V1 portable installer ZIP")
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[2]),
        help="Javis repository root",
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--source-sha", default="")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    payload: dict
    try:
        payload = build(
            Path(args.repo_root), Path(args.output_dir), source_sha=str(args.source_sha)
        )
    except Exception as exc:
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":") if args.compact else None,
            indent=None if args.compact else 2,
        )
    )
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
