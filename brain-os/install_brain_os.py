#!/usr/bin/env python3
"""Safely install Brain OS into an existing Javis Brain.

The same file serves two layouts:

1. Repository mode: ``<javis>/brain-os/install_brain_os.py`` with payload in
   ``<javis>/brain-os/template``.
2. Portable package mode: ``<brain>/BrainOS-V1-Portable/install.py`` with payload in
   ``./payload``. In this mode the target Brain defaults to the package parent, so a user
   can extract the package inside a fresh Brain and run ``python install.py``.

Preview is the default. The installer copies only Brain-OS-owned payload, refuses every
content conflict, deliberately leaves app-owned system-skill mirrors to Javis
``system_sync``, validates portable-package integrity when a manifest is present, and
never deletes user files.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SYSTEM_SKILLS = {"ingest-source", "notes", "query-wiki", "lint-wiki"}
PACKAGE_SCHEMA = 1


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files(template: Path):
    """Yield only files owned by the Brain OS overlay.

    Javis owns canonical system skills and their ``.claude`` mirrors; those are always
    installed/updated by ``system_sync`` instead of being blindly copied from the package.
    """
    for src in sorted(p for p in template.rglob("*") if p.is_file()):
        rel = src.relative_to(template)
        parts = rel.parts
        if parts and parts[0] == ".claude":
            continue
        if len(parts) >= 3 and parts[0] == "skills" and parts[1] in SYSTEM_SKILLS:
            continue
        yield src, rel


def _looks_like_javis_root(root: Path) -> bool:
    return (
        (root / "server" / "system_sync.py").is_file()
        and (root / "system" / "plugins" / "brain-os" / "plugin.py").is_file()
        and (root / "requirements.txt").is_file()
        and (root / ".claude" / "skills").is_dir()
    )


def _unique_paths(paths):
    seen = set()
    for raw in paths:
        if raw is None:
            continue
        try:
            p = Path(raw).expanduser().resolve()
        except Exception:
            continue
        key = os.path.normcase(str(p))
        if key in seen:
            continue
        seen.add(key)
        yield p


def discover_repo_root(
    target: Path,
    *,
    explicit: str | Path | None = None,
    script_path: Path | None = None,
) -> tuple[Path | None, list[str]]:
    """Find the Javis application root without assuming the installer lives in the repo.

    Priority is explicit flag -> environment -> normal local ``<repo>/brains/<brain>``
    layout -> cwd/script/Brain ancestors. External ``path:`` Brains can always use
    ``--javis-root`` or ``JAVIS_ROOT``.
    """
    script = (script_path or Path(__file__)).resolve()
    target = target.resolve()
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    for name in ("JAVIS_ROOT", "JAVIS_PROJECT_ROOT"):
        value = os.getenv(name, "").strip()
        if value:
            candidates.append(Path(value))
    if target.parent.name.casefold() == "brains":
        candidates.append(target.parent.parent)
    candidates.append(Path.cwd())
    candidates.extend(Path.cwd().resolve().parents)
    candidates.append(script.parent)
    candidates.extend(script.parent.parents)
    candidates.append(target)
    candidates.extend(target.parents)

    checked: list[str] = []
    for candidate in _unique_paths(candidates):
        checked.append(str(candidate))
        if _looks_like_javis_root(candidate):
            return candidate, checked
    return None, checked


def runtime_python(repo_root: Path) -> Path:
    """Prefer Javis' own virtualenv interpreter when present."""
    candidates = [
        repo_root / ".venv" / "Scripts" / "python.exe",
        repo_root / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return Path(sys.executable).resolve()


def _dependency_report(repo_root: Path) -> dict:
    python = runtime_python(repo_root)
    proc = subprocess.run(
        [str(python), "-c", "import pypdf, yaml"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return {
        "python": str(python),
        "pypdf_yaml_import": proc.returncode == 0,
        "error": "" if proc.returncode == 0 else ((proc.stderr or proc.stdout or "").strip()[:2000]),
    }


def runtime_report(repo_root: Path) -> dict:
    plugin = repo_root / "system" / "plugins" / "brain-os" / "plugin.py"
    req = repo_root / "requirements.txt"
    skills = {}
    for slug in sorted(SYSTEM_SKILLS):
        p = repo_root / ".claude" / "skills" / slug / "SKILL.md"
        skills[slug] = p.is_file() and "javis_brain_os" in p.read_text(encoding="utf-8")
    req_ok = req.is_file() and "pypdf" in req.read_text(encoding="utf-8").casefold()
    deps = _dependency_report(repo_root)
    compatible = (
        plugin.is_file()
        and req_ok
        and all(skills.values())
        and deps["pypdf_yaml_import"]
    )
    return {
        "repo_root": str(repo_root),
        "bridge_plugin": plugin.is_file(),
        "system_skills": skills,
        "document_dependency": req_ok,
        "runtime_dependencies": deps,
        "compatible": compatible,
    }


def plan(template: Path, target: Path) -> dict:
    copy, same, conflicts = [], [], []
    for src, rel in source_files(template):
        dst = target / rel
        if not dst.exists():
            copy.append(rel.as_posix())
        elif dst.is_file() and sha(src) == sha(dst):
            same.append(rel.as_posix())
        else:
            conflicts.append(rel.as_posix())
    return {"copy": copy, "same": same, "conflicts": conflicts}


def _portable_manifest(script_dir: Path) -> Path | None:
    path = script_dir / "manifest.json"
    return path if path.is_file() else None


def verify_package_integrity(script_dir: Path, template: Path) -> dict:
    """Verify a portable package manifest if present; repository mode needs no manifest."""
    manifest_path = _portable_manifest(script_dir)
    if manifest_path is None:
        return {"present": False, "ok": True, "source_sha": ""}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("package_schema") != PACKAGE_SCHEMA:
            raise RuntimeError(
                f"package_schema không hỗ trợ: {payload.get('package_schema')!r}"
            )
        expected = payload.get("package_files")
        if not isinstance(expected, dict) or not expected:
            raise RuntimeError("manifest thiếu package_files")
        actual_paths = {
            "install.py": script_dir / "install.py",
            "RELEASE.md": script_dir / "RELEASE.md",
        }
        for src, rel in source_files(template):
            actual_paths[f"payload/{rel.as_posix()}"] = src
        expected_keys = set(str(k) for k in expected)
        actual_keys = set(actual_paths)
        if expected_keys != actual_keys:
            missing = sorted(expected_keys - actual_keys)
            extra = sorted(actual_keys - expected_keys)
            raise RuntimeError(
                f"package file set sai; missing={missing[:10]}, extra={extra[:10]}"
            )
        bad = []
        for rel, path in actual_paths.items():
            if not path.is_file() or sha(path) != str(expected[rel]):
                bad.append(rel)
        if bad:
            raise RuntimeError("checksum không khớp: " + ", ".join(bad[:20]))
        return {
            "present": True,
            "ok": True,
            "source_sha": str(payload.get("source_sha") or ""),
            "file_count": len(actual_paths),
        }
    except Exception as exc:
        return {
            "present": True,
            "ok": False,
            "source_sha": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _load_system_sync(repo_root: Path):
    server = repo_root / "server"
    if str(server) not in sys.path:
        sys.path.insert(0, str(server))
    module = importlib.import_module("system_sync")
    module_root = Path(module.__file__).resolve().parent.parent
    if module_root != repo_root.resolve():
        raise RuntimeError(
            f"system_sync đang được import từ Javis root khác: {module_root}"
        )
    return module


def sync_system_skills(repo_root: Path, target: Path) -> dict:
    module = _load_system_sync(repo_root)
    cache = getattr(module, "_SYNCED_ROOTS", None)
    if isinstance(cache, set):
        cache.discard(str(target.resolve()))
    result = module.sync_brain(target)
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(f"Javis system_sync thất bại: {result!r}")
    return result


def installed_contract_report(template: Path, target: Path) -> dict:
    p = plan(template, target)
    system_skills = {}
    for slug in sorted(SYSTEM_SKILLS):
        path = target / "skills" / slug / "SKILL.md"
        system_skills[slug] = path.is_file() and "javis_brain_os" in path.read_text(
            encoding="utf-8"
        )
    owned_ok = not p["copy"] and not p["conflicts"]
    return {
        "owned_payload": p,
        "system_skills": system_skills,
        "ok": owned_ok and all(system_skills.values()),
    }


def _resolve_template(script_dir: Path, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    portable = script_dir / "payload"
    if portable.is_dir():
        return portable
    return script_dir / "template"


def _resolve_target(script_dir: Path, brain: str | None, template: Path) -> Path:
    if brain:
        return Path(brain).expanduser().resolve()
    if template.name == "payload" and template.parent == script_dir:
        return script_dir.parent.resolve()
    raise RuntimeError(
        "Thiếu Brain đích. Trong portable package hãy đặt thư mục package trực tiếp bên trong "
        "Brain; còn repository mode phải truyền path Brain."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely install/verify Brain OS in one existing Javis Brain"
    )
    parser.add_argument(
        "brain",
        nargs="?",
        help="Target Brain root. Portable package may omit this when extracted inside the Brain.",
    )
    parser.add_argument("--apply", action="store_true", help="Apply only after all preflight checks pass")
    parser.add_argument("--verify", action="store_true", help="Verify installed overlay + system-skill contract; never writes")
    parser.add_argument("--javis-root", help="Explicit Javis repository root (needed for some external path: Brains)")
    parser.add_argument("--payload", help="Explicit Brain OS payload/template directory")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    payload = {"ok": False, "action": "verify-brain-os" if args.verify else "install-brain-os"}
    try:
        if args.apply and args.verify:
            raise RuntimeError("--apply và --verify không dùng cùng lúc")
        template = _resolve_template(script_dir, args.payload)
        target = _resolve_target(script_dir, args.brain, template)
        payload.update(target=str(target), apply=bool(args.apply), template=str(template))
        if not template.is_dir():
            raise RuntimeError(f"Thiếu Brain OS payload: {template}")
        if not target.is_dir():
            raise RuntimeError(f"Brain đích không tồn tại: {target}")

        integrity = verify_package_integrity(script_dir, template)
        payload["package_integrity"] = integrity
        if not integrity["ok"]:
            raise RuntimeError("Portable package integrity FAIL: " + integrity.get("error", ""))

        repo_root, checked = discover_repo_root(
            target, explicit=args.javis_root, script_path=Path(__file__)
        )
        payload["runtime_discovery"] = {"checked": checked, "repo_root": str(repo_root or "")}
        if repo_root is None:
            raise RuntimeError(
                "Không tìm thấy Javis runtime root. Với Brain ngoài thư mục <Javis>/brains, "
                "hãy truyền --javis-root <path> hoặc đặt JAVIS_ROOT."
            )

        runtime = runtime_report(repo_root)
        p = plan(template, target)
        payload.update(runtime=runtime, plan=p)
        if not runtime["compatible"]:
            raise RuntimeError(
                "Javis runtime hiện tại chưa đủ Brain OS bridge/system-skill/dependency; "
                "không cài nửa vời."
            )

        if args.verify:
            installed = installed_contract_report(template, target)
            payload["installed_contract"] = installed
            if not installed["ok"]:
                raise RuntimeError("Brain OS installed contract chưa đạt")
            payload["ok"] = True
        else:
            if p["conflicts"]:
                raise RuntimeError(
                    "Brain đích có file trùng path nhưng khác nội dung; từ chối overwrite: "
                    + ", ".join(p["conflicts"][:20])
                )
            payload["installed"] = []
            payload["system_sync"] = None
            if args.apply:
                # Validate the app-owned system layer first. It is safe before Brain OS config
                # exists because these skills retain their legacy fallback until managed mode
                # becomes active.
                payload["system_sync"] = sync_system_skills(repo_root, target)
                for src, rel in source_files(template):
                    dst = target / rel
                    if dst.exists():
                        continue
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                payload["installed"] = list(p["copy"])
                installed = installed_contract_report(template, target)
                payload["installed_contract"] = installed
                if not installed["ok"]:
                    raise RuntimeError("Post-install contract verification FAIL")
            payload["ok"] = True
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
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
