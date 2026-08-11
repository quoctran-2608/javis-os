#!/usr/bin/env python3
"""Cài binary AGY ARM64 chính thức làm fallback cho Docker x86 thiếu PCLMUL."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path


ARM64_MANIFEST = (
    "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/"
    "manifests/linux_arm64.json"
)
AMD64_MANIFEST = (
    "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/"
    "manifests/linux_amd64.json"
)


def _read_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def _download(url: str, destination: Path) -> None:
    with urllib.request.urlopen(url, timeout=180) as response:
        with destination.open("wb") as target:
            shutil.copyfileobj(response, target)


def _sha512(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_binary(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:*") as bundle:
        members = [
            member for member in bundle.getmembers()
            if member.isfile() and Path(member.name).name in {"agy", "antigravity"}
        ]
        if len(members) != 1:
            raise RuntimeError(
                f"Gói ARM64 phải có đúng một binary AGY, tìm thấy {len(members)}.")
        source = bundle.extractfile(members[0])
        if source is None:
            raise RuntimeError("Không đọc được binary AGY ARM64 trong archive.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tmp")
        with temporary.open("wb") as target:
            shutil.copyfileobj(source, target)
        os.chmod(temporary, 0o755)
        temporary.replace(destination)


def install(
    native: Path,
    destination: Path,
    manifest_url: str = ARM64_MANIFEST,
    native_manifest_url: str = AMD64_MANIFEST,
) -> str:
    if not native.is_file():
        raise RuntimeError(f"Không tìm thấy AGY native tại {native}.")
    # Không chạy binary x86 để đọc version: chính CPU VPS thiếu PCLMUL sẽ SIGILL
    # ở đây, trước khi fallback được cài. Hai manifest chính thức là nguồn version.
    native_manifest = _read_json(native_manifest_url)
    manifest = _read_json(manifest_url)
    native_version = str(native_manifest.get("version") or "").strip()
    version = str(manifest.get("version") or "").strip()
    url = str(manifest.get("url") or "").strip()
    expected = str(manifest.get("sha512") or "").strip().lower()
    if not version or not url or len(expected) != 128:
        raise RuntimeError("Manifest AGY ARM64 thiếu version, URL hoặc SHA-512.")
    if native_version != version:
        raise RuntimeError(
            f"AGY native {native_version} không khớp fallback ARM64 {version}.")

    with tempfile.TemporaryDirectory(prefix="javis-agy-arm64-") as tmp:
        archive = Path(tmp) / "agy-arm64.tar.gz"
        _download(url, archive)
        actual = _sha512(archive)
        if actual != expected:
            raise RuntimeError(
                f"SHA-512 AGY ARM64 không khớp: mong {expected}, nhận {actual}.")
        _extract_binary(archive, destination)
    return version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--manifest", default=ARM64_MANIFEST)
    parser.add_argument("--native-manifest", default=AMD64_MANIFEST)
    args = parser.parse_args()
    version = install(
        args.native,
        args.destination,
        args.manifest,
        args.native_manifest,
    )
    print(f"Installed Antigravity ARM64 fallback {version}: {args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
