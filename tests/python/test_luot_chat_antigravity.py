"""Một lượt dashboard qua Antigravity phải stream, lưu và resume bằng conversation_id."""
from _paths import SERVER  # noqa: E402,F401

import asyncio
import json
import os
import sys
import tempfile
from types import SimpleNamespace

os.environ.setdefault("JAVIS_STATE_DIR", tempfile.mkdtemp(prefix="javis-antigravity-chat-"))

from fastapi import WebSocketDisconnect  # noqa: E402

import main  # noqa: E402
from chat_runtime import ChatRuntime  # noqa: E402
from sessions import SessionStore  # noqa: E402


class _WS:
    cookies = {}

    def __init__(self, payload):
        self.payload = json.dumps(payload)
        self.sent = []

    async def accept(self):
        pass

    async def close(self, code=None):
        pass

    async def send_text(self, value):
        self.sent.append(json.loads(value))

    async def receive_text(self):
        if self.payload is not None:
            value, self.payload = self.payload, None
            return value
        for _ in range(400):
            if any(item.get("type") == "turn_done" for item in self.sent):
                break
            await asyncio.sleep(0.02)
        raise WebSocketDisconnect()


class _AntigravityFake:
    def __init__(self, **kwargs):
        self.session_id = None
        self.model = kwargs.get("model")

    def is_available(self):
        return True

    async def query(self, prompt):
        yield {"type": "session", "session_id": "agy-conversation-1"}
        yield {"type": "tool_call", "name": "javis_connections", "input": {}}
        yield {"type": "text", "content": "Vâng anh, Antigravity đã kết nối."}
        yield {
            "type": "final",
            "content": "Vâng anh, Antigravity đã kết nối.",
            "session_id": "agy-conversation-1",
            "tokens_in": 321,
            "tokens_out": 12,
        }


def test_luot_antigravity_chay_tron(monkeypatch, tmp_path):
    runtime = ChatRuntime()
    store = SessionStore(tmp_path / "conversations.db")
    ws = _WS({
        "message": "Kiểm tra Antigravity",
        "brain": "brain",
        "session_id": "phien-antigravity",
    })

    monkeypatch.setattr(main, "_CHAT_RUNTIME", runtime)
    monkeypatch.setattr(main, "get_store", lambda: store)
    monkeypatch.setattr(main.cfgmod, "gate_active", lambda: False)
    monkeypatch.setattr(main.cfgmod, "read_settings", lambda: {"model": {}})
    monkeypatch.setattr(
        main, "_chat_provider",
        lambda _cfg: ("antigravity-cli", "agy", "", "gemini-test-low"))
    monkeypatch.setattr(main, "_reasoning_level", lambda _cfg: "off")
    monkeypatch.setattr(main, "build_system_prompt", lambda *a, **k: "system")
    monkeypatch.setattr(main.channel_context, "build_channel_block", lambda *a, **k: "")
    monkeypatch.setattr(main, "claude_engine",
                        lambda **_kw: SimpleNamespace(session_id=None))
    monkeypatch.setattr(main, "AntigravityCLI", _AntigravityFake)
    monkeypatch.setattr(main, "_schedule_registry_discovery_shadow", lambda *a, **k: None)
    monkeypatch.setattr(main, "log_conversation", lambda *a, **k: None)
    monkeypatch.setattr(main.usage_store, "record", lambda *a, **k: None)

    async def nothing(*_a, **_k):
        return None

    monkeypatch.setattr(main, "_schedule_cancel_action", nothing)
    monkeypatch.setattr(main.learn_feature, "enqueue", nothing)

    async def run():
        await main.websocket_endpoint(ws)
        for _ in range(100):
            if runtime.get_job("phien-antigravity") is None:
                break
            await asyncio.sleep(0.02)

    asyncio.run(run())

    errors = [item for item in ws.sent if item.get("type") == "error"]
    responses = [item for item in ws.sent if item.get("type") == "response"]
    assert not errors
    assert len(responses) == 1
    assert responses[0]["engine"] == "antigravity"
    assert responses[0]["model"] == "gemini-test-low"
    assert responses[0]["ctx_in"] == 321
    assert any(item.get("type") == "stream" for item in ws.sent)

    messages = store.get_messages("phien-antigravity")
    assert [item["role"] for item in messages] == ["user", "assistant"]
    row = store.get_session("phien-antigravity")
    assert row["antigravity_conversation_id"] == "agy-conversation-1"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
