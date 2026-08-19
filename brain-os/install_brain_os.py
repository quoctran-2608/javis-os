#!/usr/bin/env python3
"""Install Brain OS into an existing Javis Brain without blind directory overwrite.

Preview is default. The installer copies only Brain-OS-owned payload, refuses every
content conflict, and deliberately skips system skills/mirrors that Javis system_sync
owns. It never deletes target files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

SYSTEM_SKILLS = {"ingest-source", "notes", "query-wiki", "lint-wiki"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files(template: Path):
    for src in sorted(p for p in template.rglob("*") if p.is_file()):
        rel = src.relative_to(template)
        parts = rel.parts
        if parts and parts[0] == ".claude":
            continue  # mirror is derived by Javis system_sync
        if len(parts) >= 3 and parts[0] == "skills" and parts[1] in SYSTEM_SKILLS:
            continue  # app-owned system skills, also installed by system_sync
        yield src, rel


def runtime_report(repo_root: Path) -> dict:
    plugin = repo_root / "system" / "plugins" / "brain-os" / "plugin.py"
    req = repo_root / "requirements.txt"
    skills = {}
    for slug in sorted(SYSTEM_SKILLS):
        p = repo_root / ".claude" / "skills" / slug / "SKILL.md"
        skills[slug] = p.is_file() and "javis_brain_os" in p.read_text(encoding="utf-8")
    req_ok = req.is_file() and "pypdf" in req.read_text(encoding="utf-8").casefold()
    return {
        "bridge_plugin": plugin.is_file(),
        "system_skills": skills,
        "document_dependency": req_ok,
        "compatible": plugin.is_file() and req_ok and all(skills.values()),
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely install Brain OS into one existing Javis Brain")
    parser.add_argument("brain", help="Target Brain root, e.g. /brains/MyBrain")
    parser.add_argument("--apply", action="store_true", help="Apply only when preview has zero conflicts")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    template = Path(__file__).resolve().parent / "template"
    target = Path(args.brain).expanduser().resolve()
    payload = {"ok": False, "action": "install-brain-os", "target": str(target), "apply": bool(args.apply)}
    try:
        if not template.is_dir():
            raise RuntimeError(f"Thiếu template: {template}")
        if not target.is_dir():
            raise RuntimeError(f"Brain đích không tồn tại: {target}")
        runtime = runtime_report(repo_root)
        p = plan(template, target)
        payload.update(runtime=runtime, plan=p)
        if not runtime["compatible"]:
            raise RuntimeError("Javis runtime hiện tại chưa có đủ Brain OS bridge/system-skill/dependency; không cài nửa vời.")
        if p["conflicts"]:
            raise RuntimeError("Brain đích có file trùng path nhưng khác nội dung; từ chối overwrite: " + ", ".join(p["conflicts"][:20]))
        if args.apply:
            for src, rel in source_files(template):
                dst = target / rel
                if dst.exists():
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            payload["installed"] = list(p["copy"])
        else:
            payload["installed"] = []
        payload["ok"] = True
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":") if args.compact else None, indent=None if args.compact else 2))
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
