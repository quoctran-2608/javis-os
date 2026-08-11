"""Fallback ARM64-QEMU cho VPS x86 thiếu PCLMUL."""
from _paths import ROOT  # noqa: E402

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(ROOT / "tools"))
import install_antigravity_compat as installer  # noqa: E402


def _script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _runtime(tmp_path: Path, flags: str, force: str = "0"):
    native = _script(tmp_path / "native", 'echo "native:$*"\n')
    arm64 = _script(tmp_path / "arm64", 'echo "arm:$*"\n')
    qemu = _script(
        tmp_path / "qemu-aarch64",
        'printf "qemu:"; printf "%s|" "$@"; printf "\\n"\n',
    )
    ld_prefix = tmp_path / "arm-root"
    ld_prefix.mkdir()
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text(f"flags : {flags}\n", encoding="utf-8")
    env = {
        **os.environ,
        "JAVIS_ANTIGRAVITY_NATIVE_BIN": str(native),
        "JAVIS_ANTIGRAVITY_ARM64_BIN": str(arm64),
        "JAVIS_ANTIGRAVITY_QEMU_BIN": str(qemu),
        "JAVIS_ANTIGRAVITY_QEMU_LD_PREFIX": str(ld_prefix),
        "JAVIS_ANTIGRAVITY_CPUINFO": str(cpuinfo),
        "JAVIS_ANTIGRAVITY_FORCE_EMULATION": force,
    }
    return subprocess.run(
        [str(ROOT / "system" / "agy-compatible.sh"), "--version"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    ), native, arm64, qemu, ld_prefix


def test_wrapper_uses_native_when_cpu_has_required_flags(tmp_path):
    flags = "mmx pclmulqdq popcnt sse sse2 pni ssse3 sse4_1 sse4_2 cx16"
    result, *_ = _runtime(tmp_path, flags)
    assert result.returncode == 0
    assert result.stdout.strip() == "native:--version"


def test_wrapper_falls_back_when_pclmul_is_missing(tmp_path):
    flags = "mmx popcnt sse sse2 pni ssse3 sse4_1 sse4_2 cx16"
    result, _native, arm64, _qemu, ld_prefix = _runtime(tmp_path, flags)
    assert result.returncode == 0
    assert result.stdout.strip() == (
        f"qemu:-L|{ld_prefix}|{arm64}|--version|")


def test_wrapper_can_force_emulation_for_docker_smoke(tmp_path):
    flags = "mmx pclmulqdq popcnt sse sse2 pni ssse3 sse4_1 sse4_2 cx16"
    result, _native, arm64, _qemu, ld_prefix = _runtime(tmp_path, flags, force="1")
    assert result.returncode == 0
    assert result.stdout.strip() == (
        f"qemu:-L|{ld_prefix}|{arm64}|--version|")


def test_installer_verifies_checksum_and_extracts_one_binary(monkeypatch, tmp_path):
    archive = tmp_path / "bundle.tar.gz"
    payload = b"official-arm64-binary"
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo("antigravity")
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))
    checksum = hashlib.sha512(archive.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": "1.2.3",
        "url": archive.as_uri(),
        "sha512": checksum,
    }), encoding="utf-8")
    native_manifest = tmp_path / "native-manifest.json"
    native_manifest.write_text(json.dumps({"version": "1.2.3"}), encoding="utf-8")
    native = tmp_path / "native"
    native.write_bytes(b"x86-binary-that-must-not-run")
    destination = tmp_path / "out" / "agy"

    assert installer.install(
        native, destination, manifest.as_uri(), native_manifest.as_uri()) == "1.2.3"
    assert destination.read_bytes() == payload
    assert os.access(destination, os.X_OK)


def test_installer_rejects_bad_checksum(monkeypatch, tmp_path):
    archive = tmp_path / "bundle.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        data = b"binary"
        info = tarfile.TarInfo("antigravity")
        info.size = len(data)
        bundle.addfile(info, io.BytesIO(data))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": "1.2.3",
        "url": archive.as_uri(),
        "sha512": "0" * 128,
    }), encoding="utf-8")
    native_manifest = tmp_path / "native-manifest.json"
    native_manifest.write_text(json.dumps({"version": "1.2.3"}), encoding="utf-8")
    native = tmp_path / "native"
    native.write_bytes(b"x86-binary-that-must-not-run")

    import pytest
    with pytest.raises(RuntimeError, match="SHA-512"):
        installer.install(
            native, tmp_path / "agy", manifest.as_uri(), native_manifest.as_uri())


def test_installer_rejects_version_mismatch_without_running_native(tmp_path):
    archive = tmp_path / "bundle.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        data = b"arm64"
        info = tarfile.TarInfo("antigravity")
        info.size = len(data)
        bundle.addfile(info, io.BytesIO(data))
    arm_manifest = tmp_path / "arm.json"
    arm_manifest.write_text(json.dumps({
        "version": "1.2.4",
        "url": archive.as_uri(),
        "sha512": hashlib.sha512(archive.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    native_manifest = tmp_path / "native.json"
    native_manifest.write_text(json.dumps({"version": "1.2.3"}), encoding="utf-8")
    native = tmp_path / "native"
    native.write_bytes(b"not-an-executable")

    import pytest
    with pytest.raises(RuntimeError, match="không khớp"):
        installer.install(
            native, tmp_path / "agy", arm_manifest.as_uri(), native_manifest.as_uri())


def test_dockerfile_builds_and_smokes_arm64_fallback():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "qemu-user libc6-arm64-cross" in text
    assert "install_antigravity_compat.py" in text
    assert "JAVIS_ANTIGRAVITY_FORCE_EMULATION=1 agy --version" in text
    block = text[text.index("COPY tools/install_antigravity_compat.py"):
                 text.index("WORKDIR /app")]
    assert "|| echo" not in block


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
