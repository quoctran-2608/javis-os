#!/usr/bin/env python3
"""Install Source Manager without modifying the Javis code checkout.

Default is dry-run. `--apply` writes only:
  1) <JAVIS_STATE_DIR>/plugins/source-manager + plugin install manifest
  2) selected Source Manager assets inside the target Brain

Unknown/user-modified collisions fail closed. The only first-install replacement allowed is
Brain `skills/ingest-source/SKILL.md` when Javis' own system-manifest proves that exact file
is still system-managed and unmodified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

VERSION = "0.2.0"
HERE = Path(__file__).resolve().parent
PLUGIN_SRC = HERE / "package" / "global-plugin" / "source-manager"
BRAIN_SRC = HERE / "package" / "brain"
BRAIN_MANIFEST_REL = Path(".javis") / "source-manager-install.json"
STATE_MANIFEST_REL = Path("plugin-data") / "source-manager" / "install-manifest.json"
SYSTEM_MANIFEST_REL = Path(".javis") / "system-manifest.json"
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _json_bytes(data: dict) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".source-manager.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(str(tmp), str(path))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _write_if_changed(path: Path, data: bytes) -> bool:
    try:
        if path.is_file() and path.read_bytes() == data:
            return False
    except OSError:
        pass
    _atomic_write(path, data)
    return True


def _norm_skill_text(text: str) -> str:
    t = (text or "").replace("\r\n", "\n").replace("\ufeff", "")
    t = _DATE_RE.sub("<DATE>", t)
    t = "\n".join(line.rstrip() for line in t.split("\n"))
    return t.strip() + "\n"


def _javis_skill_hash(data: bytes) -> str:
    text = data.decode("utf-8")
    return hashlib.sha256(_norm_skill_text(text).encode("utf-8")).hexdigest()


def _pristine_system_ingest(brain: Path, current: bytes) -> bool:
    manifest = _read_json(brain / SYSTEM_MANIFEST_REL)
    entry = ((manifest.get("files") or {}).get("skills/ingest-source") or {})
    if entry.get("status") != "managed":
        return False
    expected = str(entry.get("hash") or "")
    if not expected:
        return False
    try:
        return _javis_skill_hash(current) == expected
    except (UnicodeDecodeError, ValueError):
        return False


def _source_files(base: Path):
    if not base.is_dir():
        raise FileNotFoundError(f"thiếu package directory: {base}")
    out = []
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        if any(part.startswith(".") and part not in (".javis",) for part in path.relative_to(base).parts):
            continue
        out.append((path.relative_to(base), path.read_bytes()))
    return out


def _manifest_files(manifest: dict) -> dict:
    files = manifest.get("files")
    return files if isinstance(files, dict) else {}


def _classify_target(*, target: Path, desired: bytes, rel: str, previous: dict,
                     brain: Path | None = None, allow_system_ingest: bool = False) -> dict:
    desired_sha = _sha(desired)
    if not target.exists():
        return {"rel": rel, "action": "create", "sha256": desired_sha}
    if not target.is_file():
        return {"rel": rel, "action": "conflict", "reason": "target_not_file"}
    try:
        current = target.read_bytes()
    except OSError as exc:
        return {"rel": rel, "action": "conflict", "reason": f"read_error:{type(exc).__name__}"}
    current_sha = _sha(current)
    if current == desired:
        return {"rel": rel, "action": "unchanged", "sha256": desired_sha}

    prev_hash = str(previous.get(rel) or "")
    if prev_hash and current_sha == prev_hash:
        return {"rel": rel, "action": "update_managed", "sha256": desired_sha}

    if allow_system_ingest and brain is not None and _pristine_system_ingest(brain, current):
        return {"rel": rel, "action": "replace_pristine_javis_system_skill", "sha256": desired_sha}

    return {
        "rel": rel,
        "action": "conflict",
        "reason": "existing_file_is_unknown_or_user_modified",
        "current_sha256": current_sha,
        "desired_sha256": desired_sha,
    }


def _build_plan(brain: Path, state_dir: Path) -> dict:
    brain_manifest_path = brain / BRAIN_MANIFEST_REL
    state_manifest_path = state_dir / STATE_MANIFEST_REL
    old_brain_files = _manifest_files(_read_json(brain_manifest_path))
    old_state_files = _manifest_files(_read_json(state_manifest_path))

    desired_brain = {}
    brain_ops = []
    for rel_path, data in _source_files(BRAIN_SRC):
        rel = rel_path.as_posix()
        desired_brain[rel] = _sha(data)
        op = _classify_target(
            target=brain / rel_path,
            desired=data,
            rel=rel,
            previous=old_brain_files,
            brain=brain,
            allow_system_ingest=(rel == "skills/ingest-source/SKILL.md"),
        )
        op["_source"] = str(BRAIN_SRC / rel_path)
        op["_target"] = str(brain / rel_path)
        brain_ops.append(op)

    desired_state = {}
    state_ops = []
    for rel_path, data in _source_files(PLUGIN_SRC):
        target_rel = Path("plugins") / "source-manager" / rel_path
        rel = target_rel.as_posix()
        desired_state[rel] = _sha(data)
        op = _classify_target(
            target=state_dir / target_rel,
            desired=data,
            rel=rel,
            previous=old_state_files,
        )
        op["_source"] = str(PLUGIN_SRC / rel_path)
        op["_target"] = str(state_dir / target_rel)
        state_ops.append(op)

    conflicts = [
        f"{op['rel']}: {op.get('reason', 'conflict')}"
        for op in (brain_ops + state_ops)
        if op["action"] == "conflict"
    ]
    writes = [
        op for op in (brain_ops + state_ops)
        if op["action"] in ("create", "update_managed", "replace_pristine_javis_system_skill")
    ]
    return {
        "brain_ops": brain_ops,
        "state_ops": state_ops,
        "conflicts": conflicts,
        "writes": writes,
        "desired_brain": desired_brain,
        "desired_state": desired_state,
        "brain_manifest_path": brain_manifest_path,
        "state_manifest_path": state_manifest_path,
    }


def _apply_plan(plan: dict, brain: Path, state_dir: Path) -> int:
    if plan["conflicts"]:
        return 0
    changed = 0
    for op in plan["writes"]:
        src = Path(op["_source"])
        target = Path(op["_target"])
        if _write_if_changed(target, src.read_bytes()):
            changed += 1

    desired_brain = {
        rel.as_posix(): _sha(data) for rel, data in _source_files(BRAIN_SRC)
    }
    desired_state = {
        (Path("plugins") / "source-manager" / rel).as_posix(): _sha(data)
        for rel, data in _source_files(PLUGIN_SRC)
    }
    brain_manifest = {
        "component": "source-manager",
        "installer_version": VERSION,
        "scope": "brain",
        "files": desired_brain,
    }
    state_manifest = {
        "component": "source-manager",
        "installer_version": VERSION,
        "scope": "javis-user-state",
        "files": desired_state,
    }
    if _write_if_changed(brain / BRAIN_MANIFEST_REL, _json_bytes(brain_manifest)):
        changed += 1
    if _write_if_changed(state_dir / STATE_MANIFEST_REL, _json_bytes(state_manifest)):
        changed += 1
    return changed


def _public_plan(plan: dict) -> dict:
    def clean(op):
        return {k: v for k, v in op.items() if not k.startswith("_")}
    return {
        "brain": [clean(x) for x in plan["brain_ops"]],
        "state": [clean(x) for x in plan["state_ops"]],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Install Source Manager into persistent Javis user state + one Brain. "
                    "Default is dry-run; never modifies the Javis code checkout."
    )
    parser.add_argument("--brain", required=True, help="Target Brain root.")
    parser.add_argument(
        "--state-dir",
        default=os.getenv("JAVIS_STATE_DIR", ""),
        help="Persistent JAVIS_STATE_DIR. Required unless JAVIS_STATE_DIR env is already set.",
    )
    parser.add_argument("--apply", action="store_true", help="Apply the planned writes.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    args = parser.parse_args(argv)

    brain = Path(args.brain).expanduser().resolve()
    if not brain.is_dir():
        result = {"ok": False, "applied": False, "error": f"Brain không tồn tại: {brain}"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    if not str(args.state_dir or "").strip():
        result = {
            "ok": False,
            "applied": False,
            "error": "Thiếu --state-dir/JAVIS_STATE_DIR. Phase 2 không đoán state dir để tránh ghi vào Javis checkout.",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    state_dir = Path(args.state_dir).expanduser().resolve()

    try:
        plan = _build_plan(brain, state_dir)
    except Exception as exc:
        result = {
            "ok": False,
            "applied": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    result = {
        "ok": not bool(plan["conflicts"]),
        "component": "source-manager",
        "version": VERSION,
        "applied": False,
        "brain": str(brain),
        "state_dir": str(state_dir),
        "changed": len(plan["writes"]),
        "conflicts": plan["conflicts"],
        "plan": _public_plan(plan),
        "required_environment": {
            "JAVIS_STATE_DIR": str(state_dir),
            "JAVIS_ENABLE_USER_PLUGINS": "true",
        },
        "app_checkout_modified": False,
    }

    if plan["conflicts"]:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    if args.apply:
        try:
            changed = _apply_plan(plan, brain, state_dir)
        except Exception as exc:
            result["ok"] = False
            result["error"] = f"{type(exc).__name__}: {exc}"
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2
        result["applied"] = True
        result["changed"] = changed

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
