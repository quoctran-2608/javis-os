"""Adapter Antigravity: model live, auth state và stream-json không được trôi.

Chạy không mạng bằng một binary ``agy`` giả. Test thật với tài khoản người dùng nằm ở bước
verify thủ công của agent, không được đưa credential cá nhân vào CI.
"""
from _paths import SERVER  # noqa: E402,F401

import asyncio
import json
import os
import stat
import tempfile
from pathlib import Path

import antigravity_cli


def _fake_binary(tmp: Path) -> Path:
    path = tmp / ("agy.exe" if os.name == "nt" else "agy")
    path.write_text(
        """#!/usr/bin/env python3
import json, sys
if len(sys.argv) > 1 and sys.argv[1] == "models":
    print("gemini-test-low\\tGemini Test Low")
    print("claude-test-thinking\\tClaude Test Thinking")
    raise SystemExit(0)
print(json.dumps({"event":"init","conversation_id":"agy-conv-1",
                  "init":{"model":"gemini-test-low"}}))
print(json.dumps({"event":"step_update","step_update":{"step_type":"tool",
                  "state":"ACTIVE","tool_name":"list_dir",
                  "tool_info":{"name":"list_dir","parameters":{"path":"."}}}}))
print(json.dumps({"event":"step_update","step_update":{"step_type":"agent_response",
                  "state":"DONE","text_delta":"Xin chào"}}))
print(json.dumps({"event":"result","result":{"conversation_id":"agy-conv-1",
                  "status":"SUCCESS","response":"Xin chào",
                  "usage":{"input_tokens":123,"output_tokens":9,"thinking_tokens":4}}}))
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_models_and_stream(monkeypatch, tmp_path):
    binary = _fake_binary(tmp_path)
    monkeypatch.setenv("JAVIS_ANTIGRAVITY_BIN", str(binary))
    monkeypatch.setenv("JAVIS_ANTIGRAVITY_TIMEOUT", "30")

    catalog = antigravity_cli.list_models()
    assert catalog["models"] == ["gemini-test-low", "claude-test-thinking"]
    assert catalog["items"][0]["display_name"] == "Gemini Test Low"

    async def run():
        cli = antigravity_cli.AntigravityCLI(
            cwd=str(tmp_path),
            model="gemini-test-low",
            instructions="SYSTEM",
            enable_mcp=False,
        )
        events = []
        async for event in cli.query("chào"):
            events.append(event)
        return events

    events = asyncio.run(run())
    assert [event["type"] for event in events] == [
        "session", "tool_call", "text", "final",
    ]
    final = events[-1]
    assert final["content"] == "Xin chào"
    assert final["session_id"] == "agy-conv-1"
    assert final["tokens_in"] == 123 and final["tokens_out"] == 9


def test_find_cli_uses_official_windows_install_dir(monkeypatch, tmp_path):
    binary = tmp_path / "agy" / "bin" / "agy.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"agy")
    monkeypatch.delenv("JAVIS_ANTIGRAVITY_BIN", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(antigravity_cli.shutil, "which", lambda _name: None)

    assert antigravity_cli.find_antigravity_cli() == str(binary)


def test_auth_status_reads_cli_token(monkeypatch, tmp_path):
    binary = _fake_binary(tmp_path)
    token = tmp_path / "oauth.json"
    monkeypatch.setenv("JAVIS_ANTIGRAVITY_BIN", str(binary))
    monkeypatch.setenv("JAVIS_ANTIGRAVITY_TOKEN_FILE", str(token))
    monkeypatch.setattr(antigravity_cli, "list_models", lambda **_kw: None)
    antigravity_cli._AUTH_CACHE.update({"ts": 0.0, "connected": False})

    assert antigravity_cli.auth_status()["connected"] is False
    token.write_text(json.dumps({
        "token": {"access_token": "secret", "refresh_token": "refresh",
                  "expiry": "2099-01-01T00:00:00Z"},
        "auth_method": "google",
    }), encoding="utf-8")
    status = antigravity_cli.auth_status()
    assert status["installed"] is True and status["connected"] is True
    assert "access_token" not in status and "refresh_token" not in status


def test_auth_status_accepts_system_keyring(monkeypatch, tmp_path):
    binary = _fake_binary(tmp_path)
    monkeypatch.setenv("JAVIS_ANTIGRAVITY_BIN", str(binary))
    monkeypatch.setenv("JAVIS_ANTIGRAVITY_TOKEN_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(
        antigravity_cli, "list_models",
        lambda **_kw: {"models": ["gemini-test-low"]})
    antigravity_cli._AUTH_CACHE.update({"ts": 0.0, "connected": False})
    assert antigravity_cli.auth_status()["connected"] is True


def test_auth_status_force_bypasses_cache(monkeypatch, tmp_path):
    binary = _fake_binary(tmp_path)
    calls = []
    monkeypatch.setenv("JAVIS_ANTIGRAVITY_BIN", str(binary))
    monkeypatch.setenv("JAVIS_ANTIGRAVITY_TOKEN_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(
        antigravity_cli,
        "list_models",
        lambda **_kw: calls.append("probe") or {"models": ["connected"]},
    )
    antigravity_cli._AUTH_CACHE.update({"ts": 10**20, "connected": False})

    status = antigravity_cli.auth_status(force=True)

    assert status["connected"] is True
    assert calls == ["probe"]


def test_login_pty_selects_google_and_sends_code(monkeypatch, tmp_path):
    binary = _fake_binary(tmp_path)
    oauth_url = (
        "https://accounts.google.com/o/oauth2/auth?"
        "code_challenge=challenge&redirect_uri=https%3A%2F%2Fantigravity.google%2Foauth-callback"
        "&state=test"
    )

    class FakePTY:
        def __init__(self):
            self.pid = 123
            self.alive = True
            self.writes = []
            self.chunks = ["Select login method:\n> 1. Google OAuth\n"]

        def read(self, _size=8192):
            for _ in range(100):
                if self.chunks:
                    return self.chunks.pop(0)
                if not self.alive:
                    raise EOFError
                import time
                time.sleep(0.01)
            return ""

        def write(self, text):
            self.writes.append(text)
            if text == "\r":
                self.chunks.append(
                    f"{oauth_url}\nPaste the authorization code below:\n")
            else:
                self.chunks.append(
                    'Got an error: token exchange failed: oauth2: '
                    '"invalid_grant" "Malformed auth code."\n')

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.alive = False

    fake = FakePTY()
    monkeypatch.setenv("JAVIS_ANTIGRAVITY_BIN", str(binary))
    monkeypatch.setenv("JAVIS_ANTIGRAVITY_TOKEN_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(antigravity_cli, "_spawn_login_pty", lambda _args: fake)
    monkeypatch.setattr(antigravity_cli, "list_models", lambda **_kw: None)
    monkeypatch.setattr(antigravity_cli, "_credential_present", lambda: False)
    antigravity_cli._AUTH_CACHE.update({"ts": 0.0, "connected": False})

    start = antigravity_cli.auth_login_ui_start()
    assert start == {"ok": True, "url": oauth_url, "done": False, "error": ""}
    assert fake.writes == ["\r"]

    result = antigravity_cli.auth_login_ui_code(" pasted-code ")
    assert result["ok"] is False and result["restart"] is True
    assert "không hợp lệ" in result["error"]
    assert fake.writes[-1] == "pasted-code\r"
    assert fake.alive is False


def test_wrapped_oauth_url_is_reassembled_before_return():
    wrapped = (
        "\x1b[36mhttps://accounts.google.com/o/oauth2/auth?"
        "code_challenge=challenge&redirect_uri=https%3A%2F%2Fantigravity.google%2Foauth-\n"
        " callback&scope=one%20two&sta\n"
        " te=session-value\x1b[0m\n\n"
        "If you aren't automatically redirected, paste the authorization code below:\n"
    )
    url = antigravity_cli._extract_oauth_url(wrapped)
    assert "\n" not in url and " " not in url
    assert "oauth-callback" in url
    assert "state=session-value" in url
    assert antigravity_cli._oauth_url_complete(url) is True


def test_oauth_url_without_state_is_not_ready():
    truncated = (
        "https://accounts.google.com/o/oauth2/auth?"
        "code_challenge=challenge&redirect_uri=https%3A%2F%2Fantigravity.google%2Foauth-callback"
    )
    assert antigravity_cli._oauth_url_complete(truncated) is False


def test_login_code_does_not_spawn_models_during_oauth(monkeypatch):
    class FakePTY:
        def __init__(self):
            self.alive = True
            self.writes = []

        def write(self, text):
            self.writes.append(text)

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.alive = False

    fake = FakePTY()
    antigravity_cli._LOGIN.update({
        "proc": fake,
        "url": "https://accounts.google.com/test",
        "ready": True,
        "done": False,
        "error": "",
        "lines": [],
    })
    monkeypatch.setattr(
        antigravity_cli,
        "auth_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("OAuth must not spawn agy models")),
    )
    probes = iter((False, True))
    monkeypatch.setattr(
        antigravity_cli, "_credential_present", lambda: next(probes, True))

    result = antigravity_cli.auth_login_ui_code("valid-code")

    assert result == {"ok": True}
    assert fake.writes == ["valid-code\r"]
    assert fake.alive is False


def test_login_code_requires_live_ready_session():
    antigravity_cli._LOGIN.update({
        "proc": None,
        "url": "",
        "ready": False,
        "done": False,
        "error": "",
        "lines": [],
    })
    result = antigravity_cli.auth_login_ui_code("code")
    assert result["ok"] is False and result["restart"] is True
    assert "link mới" in result["error"]


def test_auth_logout_clears_file_and_system_keyring(monkeypatch, tmp_path):
    binary = _fake_binary(tmp_path)
    token = tmp_path / "oauth.json"
    token.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("JAVIS_ANTIGRAVITY_BIN", str(binary))
    monkeypatch.setenv("JAVIS_ANTIGRAVITY_TOKEN_FILE", str(token))
    monkeypatch.setattr(
        antigravity_cli,
        "_clear_system_keyring",
        lambda: {"ok": True, "removed": True, "store": "test-keyring"},
    )
    antigravity_cli._AUTH_CACHE.update({"ts": 0.0, "connected": True})

    result = antigravity_cli.auth_logout()

    assert result == {"ok": True, "removed": ["file", "test-keyring"]}
    assert not token.exists()
    assert antigravity_cli._AUTH_CACHE["connected"] is False


def test_auth_logout_is_idempotent_when_no_credential(monkeypatch, tmp_path):
    binary = _fake_binary(tmp_path)
    monkeypatch.setenv("JAVIS_ANTIGRAVITY_BIN", str(binary))
    monkeypatch.setenv("JAVIS_ANTIGRAVITY_TOKEN_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(
        antigravity_cli,
        "_clear_system_keyring",
        lambda: {"ok": True, "removed": False, "store": "test-keyring"},
    )

    assert antigravity_cli.auth_logout() == {"ok": True, "removed": []}


def test_auth_logout_reports_keyring_failure(monkeypatch, tmp_path):
    binary = _fake_binary(tmp_path)
    monkeypatch.setenv("JAVIS_ANTIGRAVITY_BIN", str(binary))
    monkeypatch.setenv("JAVIS_ANTIGRAVITY_TOKEN_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(
        antigravity_cli,
        "_clear_system_keyring",
        lambda: {"ok": False, "removed": False, "error": "keyring unavailable"},
    )
    monkeypatch.setattr(
        antigravity_cli,
        "list_models",
        lambda **_kw: {"models": ["still-connected"]},
    )

    result = antigravity_cli.auth_logout()

    assert result["ok"] is False
    assert "keyring unavailable" in result["error"]


def test_auth_logout_accepts_missing_keyring_tool_after_file_disconnect(monkeypatch, tmp_path):
    binary = _fake_binary(tmp_path)
    token = tmp_path / "oauth.json"
    token.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("JAVIS_ANTIGRAVITY_BIN", str(binary))
    monkeypatch.setenv("JAVIS_ANTIGRAVITY_TOKEN_FILE", str(token))
    monkeypatch.setattr(
        antigravity_cli,
        "_clear_system_keyring",
        lambda: {"ok": False, "removed": False, "error": "secret-tool missing"},
    )
    monkeypatch.setattr(antigravity_cli, "list_models", lambda **_kw: None)

    result = antigravity_cli.auth_logout()

    assert result == {"ok": True, "removed": ["file"]}


def test_global_mcp_config_has_no_secret(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("JAVIS_HUB_TOKEN", "must-not-be-written")
    antigravity_cli._ensure_global_mcp_config()
    path = tmp_path / ".gemini" / "config" / "mcp_config.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    entry = data["mcpServers"]["javis-os"]
    assert entry["args"][-1].endswith("antigravity_mcp_proxy.py")
    assert "env" not in entry
    assert "must-not-be-written" not in path.read_text(encoding="utf-8")


def test_global_mcp_config_does_not_clobber_invalid_json(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / ".gemini" / "config" / "mcp_config.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    import pytest
    with pytest.raises(ValueError):
        antigravity_cli._ensure_global_mcp_config()
    assert path.read_text(encoding="utf-8") == "{broken"


def test_global_mcp_config_accepts_empty_file(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / ".gemini" / "config" / "mcp_config.json"
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    antigravity_cli._ensure_global_mcp_config()
    assert "javis-os" in json.loads(path.read_text(encoding="utf-8"))["mcpServers"]


def test_global_mcp_config_does_not_clobber_user_server(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / ".gemini" / "config" / "mcp_config.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "mcpServers": {"javis-os": {"command": "my-own-server", "args": []}},
    }), encoding="utf-8")
    import pytest
    with pytest.raises(ValueError):
        antigravity_cli._ensure_global_mcp_config()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mcpServers"]["javis-os"]["command"] == "my-own-server"


def test_process_handle_kills_process_group(monkeypatch):
    calls = []
    proc = type("P", (), {"pid": 4321, "terminate": lambda self: calls.append("fallback")})()
    handle = antigravity_cli._ProcessHandle(proc)
    monkeypatch.setattr(antigravity_cli.os, "name", "posix")
    monkeypatch.setattr(antigravity_cli.os, "getpgid", lambda pid: pid + 1)
    import signal
    monkeypatch.setattr(antigravity_cli.os, "killpg",
                        lambda pgid, sig: calls.append((pgid, sig)))
    handle.terminate()
    assert calls == [(4322, signal.SIGTERM)]


def test_transient_error_is_retryable(monkeypatch, tmp_path):
    binary = tmp_path / ("agy.exe" if os.name == "nt" else "agy")
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'event':'result','result':{'status':'ERROR',"
        "'error':'HTTP 429 overloaded, try again'}}))\n",
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("JAVIS_ANTIGRAVITY_BIN", str(binary))

    async def run():
        cli = antigravity_cli.AntigravityCLI(
            cwd=str(tmp_path), enable_mcp=False, expose_workspace=False)
        return [event async for event in cli.query("hi")]

    events = asyncio.run(run())
    error = next(event for event in events if event["type"] == "error")
    assert error["tam_thoi"] is True and error["ma"] == 429


def test_command_runs_in_brain_and_mounts_rules(tmp_path):
    cli = antigravity_cli.AntigravityCLI(
        cwd=str(tmp_path), model="gemini-test", instructions="SYSTEM")
    args = cli._build_args("hi", rules_dir="/tmp/javis-rules")
    assert cli.cwd == str(tmp_path.resolve())
    assert args[args.index("--add-dir") + 1] == "/tmp/javis-rules"
    assert str(tmp_path.resolve()) not in args


def test_instruction_mount_outputs_are_promoted(tmp_path):
    brain = tmp_path / "brain"
    mount = tmp_path / "rules"
    brain.mkdir()
    (mount / "nested").mkdir(parents=True)
    (mount / "GEMINI.md").write_text("system", encoding="utf-8")
    (mount / "created.txt").write_text("one", encoding="utf-8")
    (mount / "nested" / "two.txt").write_text("two", encoding="utf-8")
    cli = antigravity_cli.AntigravityCLI(cwd=str(brain), enable_mcp=False)
    promoted = cli._promote_workspace_outputs(str(mount))
    assert (brain / "created.txt").read_text(encoding="utf-8") == "one"
    assert (brain / "nested" / "two.txt").read_text(encoding="utf-8") == "two"
    assert not (brain / "GEMINI.md").exists()
    assert len(promoted) == 2


def test_instruction_mount_symlink_is_not_promoted(tmp_path):
    if os.name == "nt":
        return
    brain = tmp_path / "brain"
    mount = tmp_path / "rules"
    brain.mkdir()
    mount.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (mount / "link.txt").symlink_to(outside)
    cli = antigravity_cli.AntigravityCLI(cwd=str(brain), enable_mcp=False)
    assert cli._promote_workspace_outputs(str(mount)) == []
    assert not (brain / "link.txt").exists()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
