"""Bundled tools for Second Brain managed Markdown.

The heavy rule is intentionally simple: scan is readonly/deterministic; mark is the only
per-note write and is called only after an ingest has succeeded. Setup creates exactly one
fixed refresh loop and registers the current Brain with Javis' native scheduler.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

import managed_markdown

_REFRESH_SLUG = "second-brain-refresh"
_REFRESH_BODY = """Đây là vòng làm mới Second Brain sau khi người dùng sửa Markdown trong Obsidian.
Scheduler đã chạy precheck deterministic TRƯỚC khi gọi AI. Chỉ xử lý các đường dẫn được chèn trong khối JAVIS_CHANGED_MANAGED_NOTES; không quét lại cả vault bằng AI.
Với từng file trong danh sách: re-ingest bằng skill ingest-source, giữ folder ổn định trừ khi vị trí rõ ràng sai. Chỉ sau khi source + Wiki cập nhật thành công mới gọi javis_managed_notes_mark cho đường dẫn cuối cùng.
Nếu một file lỗi, báo rõ file đó và không mark nó để lần sau retry.
"""


def _vault(ctx) -> str:
    root = str(ctx.vault_root or "").strip()
    if not root:
        raise ValueError("Không xác định được vault hiện tại")
    return root


def _roots(args) -> list[str] | None:
    raw = (args or {}).get("roots")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("roots phải là mảng đường dẫn tương đối trong vault")
    return [str(x).strip() for x in raw if str(x).strip()]


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _scan(args, ctx):
    try:
        result = managed_markdown.scan_managed_notes(_vault(ctx), _roots(args))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return f"ERROR: scan managed Markdown lỗi: {type(e).__name__}: {e}"


def _mark(args, ctx):
    path = str((args or {}).get("path") or "").strip()
    if not path:
        return "ERROR: cần path Markdown tương đối trong vault"
    try:
        result = managed_markdown.mark_ingested(_vault(ctx), path)
        return json.dumps({"ok": True, **result}, ensure_ascii=False)
    except Exception as e:
        return f"ERROR: mark managed Markdown lỗi: {type(e).__name__}: {e}"


def _register_scheduler_brain(ctx, vault_root: Path) -> None:
    """Mirror the tiny registry contract used by self_improve.LoopFeature.register_brain.

    Scheduler accepts an absolute Brain path (legacy config already supports paths), so this
    does not need to know main.py's brain-name resolver. Existing config fields are preserved.
    """
    cfg_path = Path(ctx.state_dir) / "loop_config.json"
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    brains = list(raw.get("brains") or [])
    root_s = str(vault_root.resolve())
    if root_s not in [str(x) for x in brains]:
        brains.append(root_s)
        raw["brains"] = brains
        _atomic_write(cfg_path, json.dumps(raw, ensure_ascii=False, indent=2))


def _setup(args, ctx):
    """Create the fixed refresh loop once; never overwrite user edits to an existing loop."""
    try:
        root = Path(_vault(ctx)).resolve()
        loop_path = root / "Javis" / "loops" / f"{_REFRESH_SLUG}.md"
        created = False
        if not loop_path.exists():
            fm = {
                "type": "loop",
                "name": "Second Brain Refresh",
                "slug": _REFRESH_SLUG,
                "enabled": True,
                "goal": "custom",
                "mode": "auto",
                "interval_min": 15,
                "workspace": "vault",
                "tools_profile": "vault-safe",
                "quiet_hours": "",
                "max_runs_per_day": 0,
                "owner_chat": "",
                "notify": False,
                "precheck": "managed-markdown",
                "source_roots": [],
            }
            y = yaml.safe_dump(
                fm,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
                width=1000,
            ).strip()
            _atomic_write(loop_path, f"---\n{y}\n---\n\n{_REFRESH_BODY}")
            created = True
        elif not loop_path.is_file():
            raise ValueError(f"{loop_path} tồn tại nhưng không phải file")

        _register_scheduler_brain(ctx, root)
        return json.dumps(
            {
                "ok": True,
                "created": created,
                "loop": loop_path.relative_to(root).as_posix(),
                "registered_brain": str(root),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return f"ERROR: setup Second Brain Refresh lỗi: {type(e).__name__}: {e}"


def register(ctx):
    ctx.register_tool(
        name="javis_managed_notes_scan",
        description=(
            "Quét deterministic các Markdown đã được Javis quản lý trong các source roots. "
            "So canonical SHA-256 và trả danh sách CHỈ những note thực sự thay đổi; không gọi AI, "
            "không sửa file. roots là danh sách đường dẫn tương đối tùy chọn."
        ),
        handler=_scan,
        min_mode="readonly",
        schema={
            "type": "object",
            "properties": {
                "roots": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Các user/source roots tương đối trong vault; bỏ trống để dùng source roots hiện có chuẩn của Javis.",
                }
            },
        },
    )
    ctx.register_tool(
        name="javis_managed_notes_mark",
        description=(
            "Đánh dấu MỘT Markdown đã ingest thành công: ghi javis_managed và canonical "
            "javis_last_ingested_hash. Chỉ gọi SAU khi source + Wiki đã cập nhật thành công."
        ),
        handler=_mark,
        min_mode="safe",
        schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Đường dẫn Markdown tương đối trong vault, sau mọi move/rename.",
                }
            },
            "required": ["path"],
        },
    )
    ctx.register_tool(
        name="javis_managed_notes_setup",
        description=(
            "Bảo đảm Brain hiện tại có đúng một loop Second Brain Refresh 15 phút và được đăng ký "
            "với scheduler native. Không ghi đè loop nếu người dùng đã chỉnh nó."
        ),
        handler=_setup,
        min_mode="safe",
        schema={"type": "object", "properties": {}},
    )
