"""Cầu stdio -> HTTP để Antigravity CLI gọi MCP Hub của Javis.

Antigravity nhận MCP stdio trong ``mcp_config.json`` nhưng schema hiện tại không
có trường HTTP header. Proxy nhỏ này giữ hub token ngoài argv, nhận JSON-RPC từng
dòng từ stdin, rồi chuyển nguyên gói sang ``/hub/mcp`` với các header của Javis.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def _error(mid, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": mid,
        "error": {"code": -32603, "message": message},
    }


def _forward(payload: dict) -> dict | None:
    url = os.environ.get("JAVIS_HUB_URL", "").strip()
    token = os.environ.get("JAVIS_HUB_TOKEN", "").strip()
    if not url or not token:
        return _error(payload.get("id"), "Thiếu cấu hình MCP Hub của Javis.")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "X-Javis-Mode": os.environ.get("JAVIS_HUB_MODE", "full"),
        "X-Javis-Engine": "antigravity",
    }
    vault = os.environ.get("JAVIS_HUB_VAULT", "").strip()
    if vault:
        headers["X-Javis-Vault"] = vault

    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            raw = response.read()
            if response.status == 202 or not raw:
                return None
            return json.loads(raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        return _error(payload.get("id"), f"MCP Hub HTTP {exc.code}: {detail}")
    except Exception as exc:  # noqa: BLE001 - lỗi phải trả về đúng JSON-RPC cho CLI
        return _error(payload.get("id"), f"MCP Hub: {type(exc).__name__}: {exc}")


def main() -> int:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            result = _error(None, f"JSON không hợp lệ: {exc}")
        else:
            result = _forward(payload)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
