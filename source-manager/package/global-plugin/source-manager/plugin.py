"""Source Manager Phase 2 global USER plugin.

Ownership: install into <JAVIS_STATE_DIR>/plugins/source-manager, never Javis app code.
Phase 2 is deliberately read-only: status, doctor, deterministic file probe only.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

VERSION = "0.2.0"
PHASE = 2
SCHEMA_VERSION = 1
MANAGED_ROOTS = ("Notes", "sources", "Library")


def _brain(ctx) -> Path:
    raw = str(ctx.vault_root or "").strip()
    if not raw:
        raise ValueError("Source Manager cần active Brain (vault_root)")
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Brain không tồn tại: {root}")
    return root


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _env_user_plugins_enabled() -> bool:
    for key in ("JAVIS_ENABLE_USER_PLUGINS", "JAVIS_ENABLE_VAULT_PLUGINS"):
        if str(os.getenv(key, "")).strip().lower() in ("1", "true", "yes", "on"):
            return True
    return False


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _has_symlink_component(path: Path, root: Path) -> bool:
    cur = path
    while True:
        try:
            if cur.is_symlink():
                return True
        except OSError:
            return True
        if cur == root:
            return False
        if cur.parent == cur:
            return True
        cur = cur.parent


def _resolve_managed_file(root: Path, raw_path) -> tuple[Path, str]:
    raw = str(raw_path or "").strip()
    if not raw:
        raise ValueError("thiếu path")
    normalized = raw.replace("\\", "/")
    p = Path(normalized)
    if p.is_absolute():
        raise ValueError("path phải là Brain-relative, không nhận absolute path")
    if any(part in ("", ".", "..") for part in p.parts):
        raise ValueError("path chứa segment không hợp lệ/traversal")
    if not p.parts or p.parts[0].lower() not in {x.lower() for x in MANAGED_ROOTS}:
        raise ValueError("path ngoài managed roots: Notes/, sources/, Library/")
    candidate = (root / Path(*p.parts)).resolve()
    if not _is_under(candidate, root):
        raise ValueError("path thoát khỏi Brain")
    lexical = root / Path(*p.parts)
    if _has_symlink_component(lexical, root):
        raise ValueError("không theo symlink trong Source Manager Phase 2")
    if not candidate.is_file():
        raise ValueError(f"file không tồn tại: {p.as_posix()}")
    return candidate, p.as_posix()


def _status(args, ctx):
    root = _brain(ctx)
    plugin_dir = Path(ctx.dir).resolve()
    state_dir = Path(ctx.state_dir).resolve()
    config = root / "System" / "SourceManager" / "config.yml"
    return {
        "ok": True,
        "component": "source-manager",
        "version": VERSION,
        "phase": PHASE,
        "schema_version": SCHEMA_VERSION,
        "brain_root": str(root),
        "plugin_source": ctx.source,
        "plugin_dir": str(plugin_dir),
        "state_dir": str(state_dir),
        "state_dir_explicit": bool(str(os.getenv("JAVIS_STATE_DIR", "")).strip()),
        "user_plugins_enabled": _env_user_plugins_enabled(),
        "config_exists": config.is_file(),
        "db_path": ".javis/source-manager.db",
        "db_exists": (root / ".javis" / "source-manager.db").is_file(),
        "semantic_write_enabled": False,
        "managed_roots": list(MANAGED_ROOTS),
    }


def _doctor(args, ctx):
    root = _brain(ctx)
    state_dir = Path(ctx.state_dir).resolve()
    expected_plugin = (state_dir / "plugins" / "source-manager").resolve()
    actual_plugin = Path(ctx.dir).resolve()

    ingest = root / "skills" / "ingest-source" / "SKILL.md"
    manager_skill = root / "skills" / "source-manager" / "SKILL.md"
    loop = root / "Javis" / "loops" / "source-watch.md"
    config = root / "System" / "SourceManager" / "config.yml"
    brain_local_plugin = root / "plugins" / "source-manager"

    ingest_text = _read_text(ingest)
    loop_text = _read_text(loop).lower()
    config_text = _read_text(config)
    manager_text = _read_text(manager_skill)

    checks = {
        "active_brain": root.is_dir(),
        "plugin_source_user": ctx.source == "user",
        "plugin_path_global": actual_plugin == expected_plugin,
        "state_dir_explicit": bool(str(os.getenv("JAVIS_STATE_DIR", "")).strip()),
        "user_plugins_enabled": _env_user_plugins_enabled(),
        "no_brain_local_plugin": not brain_local_plugin.exists(),
        "ingest_route_only": (
            "SOURCE_MANAGER_PHASE2_ROUTE_ONLY" in ingest_text
            and "PHASE2_NO_LEGACY_INGEST" in ingest_text
        ),
        "source_manager_skill": "SOURCE_MANAGER_PHASE2" in manager_text,
        "source_watch_present": loop.is_file(),
        "source_watch_disabled": "enabled: false" in loop_text,
        "config_schema_v1": (
            "schema_version: 1" in config_text and "semantic_write_enabled: false" in config_text
        ),
        "semantic_write_disabled": True,
    }
    problems = [name for name, ok in checks.items() if not ok]
    return {
        "ok": not problems,
        "component": "source-manager",
        "version": VERSION,
        "phase": PHASE,
        "brain_root": str(root),
        "checks": checks,
        "problems": problems,
        "semantic_write_enabled": False,
    }


def _probe_file(args, ctx):
    root = _brain(ctx)
    path, rel = _resolve_managed_file(root, (args or {}).get("path"))
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            h.update(chunk)
    st = path.stat()
    return {
        "ok": True,
        "component": "source-manager",
        "version": VERSION,
        "phase": PHASE,
        "action": "probe_only",
        "mutated": False,
        "path": rel,
        "sha256": h.hexdigest(),
        "bytes": size,
        "extension": path.suffix.lower(),
        "is_markdown": path.suffix.lower() in (".md", ".markdown"),
        "mtime_ns": int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))),
    }


def register(ctx):
    ctx.register_tool(
        name="source_manager_status",
        description="Read-only Source Manager Phase 2 status for the active Brain.",
        handler=_status,
        schema={"type": "object", "properties": {}, "additionalProperties": False},
        min_mode="readonly",
    )
    ctx.register_tool(
        name="source_manager_doctor",
        description="Read-only architecture doctor: verify global plugin + Brain route-only assets.",
        handler=_doctor,
        schema={"type": "object", "properties": {}, "additionalProperties": False},
        min_mode="readonly",
    )
    ctx.register_tool(
        name="source_manager_probe_file",
        description="Read-only deterministic SHA-256 probe for one file under Notes/, sources/, or Library/.",
        handler=_probe_file,
        schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Brain-relative path under Notes/, sources/, or Library/."
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        min_mode="readonly",
    )
