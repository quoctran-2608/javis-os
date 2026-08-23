"""Bundled tools for Second Brain managed Markdown.

The heavy rule is intentionally simple: scan is readonly/deterministic; mark is the only
write and is called only after an ingest has succeeded.
"""
from __future__ import annotations

import json
from pathlib import Path

import managed_markdown


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
