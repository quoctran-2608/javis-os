"""Bundled bridge: run governed Brain OS helpers against ctx.vault_root.

Javis chat engines intentionally run with cwd at the application/project root, not at
an individual Brain. Brain OS scripts, however, live inside each Brain. This plugin is
the boundary between those two ownership models: the active Brain comes only from
PluginContext.vault_root and every helper is executed by absolute path under that Brain.

No arbitrary command/script execution is exposed. The action map below is closed and
subprocess is always shell=False.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_MAX_STDOUT = 1024 * 1024
_MAX_NOTE_BYTES = 2 * 1024 * 1024
_TIMEOUT_S = 600


def _error(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _root(ctx) -> Path:
    raw = str(getattr(ctx, "vault_root", "") or "").strip()
    if not raw:
        raise RuntimeError("Javis chưa xác định active Brain (vault_root rỗng).")
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"Active Brain không tồn tại: {root}")
    if not (root / "System" / "BrainOS" / "config.yml").is_file():
        raise RuntimeError(
            "Brain hiện tại chưa cài Brain OS (thiếu System/BrainOS/config.yml)."
        )
    return root


def _script(root: Path, name: str) -> Path:
    scripts = (root / "skills" / "brain-manager" / "scripts").resolve()
    try:
        scripts.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("Brain manager scripts path thoát khỏi active Brain.") from exc
    target = (scripts / name).resolve()
    try:
        target.relative_to(scripts)
    except ValueError as exc:
        raise RuntimeError(f"Script path không an toàn: {name}") from exc
    if not target.is_file():
        raise RuntimeError(f"Brain OS helper chưa được cài: {target}")
    return target


def _run(cmd: list[str], *, root: Path, stdin: str | None = None) -> str:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT_S,
            shell=False,
            # Javis may run without a parent console on Windows.  A console-style
            # helper (python.exe) would otherwise flash a new black window for every
            # Brain OS operation.  CREATE_NO_WINDOW is absent off Windows, where 0 is
            # the normal subprocess value and is safe to pass unconditionally.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return _error(f"Brain OS helper vượt timeout {_TIMEOUT_S}s.")
    except Exception as exc:
        return _error(f"Không chạy được Brain OS helper: {type(exc).__name__}: {exc}")

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if len(out) > _MAX_STDOUT:
        return _error("Brain OS helper trả output quá lớn; từ chối cắt JSON kết quả.")
    if proc.returncode != 0:
        detail = out or err or f"exit={proc.returncode}"
        return _error(f"Brain OS helper thất bại: {detail[:8000]}")
    if not out:
        return _error("Brain OS helper không trả JSON.")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return _error(f"Brain OS helper trả output không phải JSON: {out[:2000]}")
    if not isinstance(payload, dict):
        return _error("Brain OS helper JSON root không phải object.")
    payload.setdefault("javis_bridge", {})
    if isinstance(payload["javis_bridge"], dict):
        payload["javis_bridge"].update(
            {"active_brain": str(root), "cwd_independent": True}
        )
    return json.dumps(payload, ensure_ascii=False)


def _handle(args, ctx) -> str:
    args = args or {}
    op = str(args.get("op") or "").strip()
    try:
        root = _root(ctx)

        if op in {"scan", "classify", "taxonomy"}:
            script = _script(root, "brain_os.py")
            cmd = [sys.executable, str(script), "--brain-root", str(root), "--compact", op]
            path = str(args.get("path") or "").strip()
            if path and op in {"classify", "taxonomy"}:
                cmd += ["--path", path]
            return _run(cmd, root=root)

        if op == "capture_note":
            body = str(args.get("body") or "")
            if not body.strip():
                return _error("capture_note cần body không rỗng.")
            if len(body.encode("utf-8")) > _MAX_NOTE_BYTES:
                return _error(f"Note vượt trần {_MAX_NOTE_BYTES} bytes.")
            script = _script(root, "capture_note.py")
            cmd = [sys.executable, str(script), "--brain-root", str(root), "--compact"]
            if bool(args.get("apply", False)):
                cmd.append("--apply")
            title = str(args.get("title") or "").strip()
            category = str(args.get("category") or "").strip()
            if title:
                cmd += ["--title", title]
            if category:
                cmd += ["--category", category]
            return _run(cmd, root=root, stdin=body)

        if op == "import_markdown":
            source = str(args.get("source") or "").strip()
            if not source:
                return _error("import_markdown cần source path.")
            script = _script(root, "brain_os.py")
            cmd = [
                sys.executable, str(script), "--brain-root", str(root), "--compact",
                "import", source,
            ]
            document_type = str(args.get("document_type") or "").strip()
            category = str(args.get("category") or "").strip()
            if document_type:
                cmd += ["--type", document_type]
            if category:
                cmd += ["--category", category]
            if bool(args.get("apply", False)):
                cmd.append("--apply")
            return _run(cmd, root=root)

        if op == "import_amplenote":
            source = str(args.get("source") or "").strip()
            if not source:
                return _error("import_amplenote cần source path.")
            script = _script(root, "import_amplenote.py")
            cmd = [
                sys.executable, str(script), source,
                "--brain-root", str(root), "--compact",
            ]
            cmd.append("--apply" if bool(args.get("apply", False)) else "--dry-run")
            return _run(cmd, root=root)

        if op == "import_document":
            source = str(args.get("source") or "").strip()
            if not source:
                return _error("import_document cần source path.")
            script = _script(root, "import_document.py")
            cmd = [
                sys.executable, str(script), source,
                "--brain-root", str(root), "--compact",
            ]
            category = str(args.get("category") or "").strip()
            if category:
                cmd += ["--category", category]
            if bool(args.get("apply", False)):
                cmd.append("--apply")
            return _run(cmd, root=root)

        if op == "record_ingest":
            path = str(args.get("path") or "").strip()
            if not path:
                return _error("record_ingest cần managed Brain-relative path.")
            script = _script(root, "record_ingest.py")
            cmd = [
                sys.executable, str(script), "--path", path,
                "--brain-root", str(root), "--compact",
            ]
            if bool(args.get("compounded", False)):
                cmd.append("--compounded")
            return _run(cmd, root=root)

        return _error(
            "op không hợp lệ. Dùng scan/classify/taxonomy/capture_note/import_markdown/"
            "import_amplenote/import_document/record_ingest."
        )
    except Exception as exc:
        return _error(f"{type(exc).__name__}: {exc}")


def register(ctx):
    ctx.register_tool(
        name="javis_brain_os",
        description=(
            "Chạy Brain OS helper trên ĐÚNG active Brain của lượt Javis, không phụ thuộc cwd. "
            "op: scan, classify, taxonomy, capture_note, import_markdown, import_amplenote, "
            "import_document, record_ingest. Import/capture mặc định preview; chỉ ghi khi apply=true. "
            "Dùng tool này thay cho Bash `python skills/brain-manager/...` trong Brain OS-managed mode."
        ),
        handler=_handle,
        min_mode="auto",
        schema={
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": [
                    "scan", "classify", "taxonomy", "capture_note", "import_markdown",
                    "import_amplenote", "import_document", "record_ingest"
                ]},
                "path": {"type": "string"},
                "source": {"type": "string"},
                "body": {"type": "string"},
                "title": {"type": "string"},
                "category": {"type": "string"},
                "document_type": {
                    "type": "string", "enum": ["", "living_note", "reference_source"]
                },
                "apply": {"type": "boolean"},
                "compounded": {"type": "boolean"}
            },
            "required": ["op"],
            "additionalProperties": False
        },
    )
