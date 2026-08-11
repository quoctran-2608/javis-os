"""Adapter Antigravity CLI cho Javis OS.

Hợp đồng upstream được dùng:

* ``agy models`` -> ``<model id>\t<display name>``.
* ``agy -p ... --output-format stream-json`` -> JSONL với ``init``,
  ``step_update`` và ``result``.
* ``--conversation <id>`` nối tiếp mạch hội thoại.
* Lần đầu print mode tự in URL Google Sign-In và nhận authorization code qua
  controlling terminal, nên Javis bọc riêng OAuth trong pseudo-terminal ẩn.

Mỗi lượt chạy với brain thật làm ``cwd`` để tool file native đọc/ghi đúng chỗ.
System prompt nằm trong ``GEMINI.md`` ở thư mục tạm rồi được gắn bằng ``--add-dir``,
vừa tránh trần command line Windows vừa không làm bẩn brain.
MCP Hub đi qua một proxy stdio riêng, vì schema MCP của Antigravity chưa có chỗ
khai HTTP header. Config MCP toàn cục chỉ chứa lệnh proxy tĩnh, không chứa token;
URL/token/vault đi qua environment riêng của từng tiến trình nên các chat không
ghi đè quyền của nhau và kho conversation gốc của CLI vẫn resume được.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import struct
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import AsyncIterator, Optional


_URL_RE = re.compile(r"https?://\S+")
_ANSI_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))"
)
_RESUME_ERROR = re.compile(
    r"(conversation|session|trajectory).*(not found|does not exist|failed|invalid)",
    re.I,
)
_TRANSIENT_ERROR = re.compile(
    r"\b(408|429|502|503|504|529)\b|timed?\s*out|timeout|overload|temporar(?:y|ily)|"
    r"service unavailable|connection reset|connection refused|try again",
    re.I,
)
_LOGIN = {
    "proc": None,
    "url": "",
    "ready": False,
    "done": False,
    "error": "",
    "lines": [],
}
_MCP_CONFIG_LOCK = threading.Lock()
_AUTH_CACHE = {"ts": 0.0, "connected": False}
_KEYRING_SERVICE = "gemini"
_KEYRING_ACCOUNT = "antigravity"
_LOGIN_START_WAIT_S = 45.0


def _no_window() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def find_antigravity_cli() -> Optional[str]:
    """Tìm ``agy``/``antigravity`` ở PATH và các vị trí cài chính thức."""
    override = os.environ.get("JAVIS_ANTIGRAVITY_BIN", "").strip()
    if override:
        try:
            if Path(override).is_file():
                return override
        except OSError:
            pass

    for name in ("agy", "antigravity"):
        found = shutil.which(name)
        if found:
            return found

    candidates = []
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        # Installer Windows chính thức đặt CLI ở
        # %LOCALAPPDATA%\agy\bin\agy.exe, nhưng process Javis cũ có thể chưa nhận
        # PATH người dùng mới sau khi cài.
        candidates += [
            Path(local_app_data) / "agy" / "bin" / "agy.exe",
            Path(local_app_data) / "Programs" / "Antigravity" / "agy.exe",
        ]

    home = Path.home()
    candidates += [
        home / ".local" / "bin" / "agy",
        home / ".local" / "bin" / "agy.exe",
        home / ".local" / "bin" / "antigravity",
        home / ".local" / "bin" / "antigravity.exe",
    ]
    for path in candidates:
        try:
            if path.is_file():
                return str(path)
        except OSError:
            pass
    return None


def token_path() -> Path:
    override = os.environ.get("JAVIS_ANTIGRAVITY_TOKEN_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"


def _token_info(path: Optional[Path] = None) -> dict:
    path = path or token_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    token = data.get("token") if isinstance(data.get("token"), dict) else data
    return {
        "refresh_token": str(token.get("refresh_token") or ""),
        "access_token": str(token.get("access_token") or ""),
        "expiry": str(token.get("expiry") or ""),
        "auth_method": str(data.get("auth_method") or ""),
    }


def _windows_keyring_has_credential() -> bool:
    """Chỉ kiểm tra target có tồn tại; không đọc nội dung credential."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        cred_read = advapi32.CredReadW
        cred_read.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        cred_read.restype = wintypes.BOOL
        cred_free = advapi32.CredFree
        cred_free.argtypes = [ctypes.c_void_p]
        pointer = ctypes.c_void_p()
        exists = bool(cred_read(
            f"{_KEYRING_SERVICE}:{_KEYRING_ACCOUNT}",
            1,  # CRED_TYPE_GENERIC
            0,
            ctypes.byref(pointer),
        ))
        if pointer.value:
            cred_free(pointer)
        return exists
    except Exception:
        return False


def _credential_present() -> bool:
    info = _token_info()
    return bool(
        info.get("refresh_token")
        or info.get("access_token")
        or _windows_keyring_has_credential()
    )


def auth_status(force: bool = False) -> dict:
    cli = find_antigravity_cli()
    if not cli:
        runtime = "Windows" if os.name == "nt" else "Linux/macOS"
        return {
            "installed": False,
            "connected": False,
            "error": f"Antigravity CLI chưa cài trong môi trường Javis đang chạy ({runtime}).",
        }
    info = _token_info()
    connected = bool(
        info.get("refresh_token")
        or info.get("access_token")
        or _windows_keyring_has_credential()
    )
    if not connected:
        # macOS/Linux desktop có thể cất OAuth trong system keyring thay vì file. `agy models`
        # chỉ đọc catalog/quyền tài khoản, không sinh nội dung và không tiêu một lượt chat.
        now = time.time()
        if force or now - float(_AUTH_CACHE.get("ts") or 0) > 30:
            _AUTH_CACHE.update({
                "ts": now,
                "connected": bool(list_models(timeout=10, cli_path=cli)),
            })
        connected = bool(_AUTH_CACHE.get("connected"))
    return {
        "installed": True,
        "connected": connected,
        "auth_method": info.get("auth_method", ""),
        "error": "" if connected else "Chưa đăng nhập Antigravity CLI trên máy này.",
    }


def _clear_system_keyring() -> dict:
    """Xóa OAuth Antigravity khỏi keyring OS mà không đọc secret.

    Antigravity dùng service ``gemini`` và account ``antigravity``. Windows
    Credential Manager ghép hai phần thành target ``gemini:antigravity``.
    """
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
            cred_delete = advapi32.CredDeleteW
            cred_delete.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            cred_delete.restype = wintypes.BOOL
            target = f"{_KEYRING_SERVICE}:{_KEYRING_ACCOUNT}"
            if cred_delete(target, 1, 0):  # CRED_TYPE_GENERIC
                return {"ok": True, "removed": True, "store": "windows"}
            error = ctypes.get_last_error()
            if error == 1168:  # ERROR_NOT_FOUND: trạng thái mong muốn đã đạt.
                return {"ok": True, "removed": False, "store": "windows"}
            return {
                "ok": False,
                "removed": False,
                "store": "windows",
                "error": f"Windows Credential Manager lỗi {error}.",
            }
        except Exception as exc:  # noqa: BLE001 - trả lỗi đọc được cho endpoint
            return {
                "ok": False,
                "removed": False,
                "store": "windows",
                "error": f"{type(exc).__name__}: {exc}",
            }

    if sys.platform == "darwin":
        command = shutil.which("security")
        args = [
            command,
            "delete-generic-password",
            "-s",
            _KEYRING_SERVICE,
            "-a",
            _KEYRING_ACCOUNT,
        ] if command else []
        store = "macos"
    else:
        command = shutil.which("secret-tool")
        args = [
            command,
            "clear",
            "service",
            _KEYRING_SERVICE,
            "username",
            _KEYRING_ACCOUNT,
        ] if command else []
        store = "linux"

    if not args:
        return {
            "ok": False,
            "removed": False,
            "store": store,
            "error": (
                "Không tìm thấy công cụ keyring của hệ điều hành "
                f"({'security' if store == 'macos' else 'secret-tool'})."
            ),
        }
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=_no_window(),
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {
            "ok": False,
            "removed": False,
            "store": store,
            "error": f"{type(exc).__name__}: {exc}",
        }
    if result.returncode == 0:
        return {"ok": True, "removed": True, "store": store}
    detail = (result.stderr or result.stdout or "").strip()
    # macOS `security` trả 44, secret-tool thường trả 1 khi item không tồn tại.
    not_found = result.returncode in ({44} if store == "macos" else {1}) and (
        not detail or re.search(r"not found|could not be found|no such", detail, re.I)
    )
    if not_found:
        return {"ok": True, "removed": False, "store": store}
    return {
        "ok": False,
        "removed": False,
        "store": store,
        "error": detail or f"Không xóa được credential ({result.returncode}).",
    }


def auth_logout() -> dict:
    """Xóa credential file/keyring để lần chạy kế tiếp mở lại Google Sign-In."""
    cli = find_antigravity_cli()
    if not cli:
        return {"ok": False, "error": "Antigravity CLI chưa cài"}
    _login_stop()

    removed = []
    errors = []
    try:
        path = token_path()
        if path.exists():
            path.unlink()
            removed.append("file")
    except Exception as exc:  # noqa: BLE001 - endpoint phải trả lỗi đọc được
        errors.append(f"Token file: {type(exc).__name__}: {exc}")

    keyring = _clear_system_keyring()
    if keyring.get("ok"):
        if keyring.get("removed"):
            removed.append(str(keyring.get("store") or "keyring"))
    else:
        errors.append(str(keyring.get("error") or "Không xóa được system keyring."))

    _AUTH_CACHE.update({"ts": time.time(), "connected": False})
    if not errors:
        return {"ok": True, "removed": removed}
    # Một số máy Linux lưu token ở file nhưng không cài CLI `secret-tool`. Nếu
    # catalog không còn đọc được sau khi xóa file thì trạng thái ngắt đã đạt.
    if not list_models(timeout=10, cli_path=cli):
        return {"ok": True, "removed": removed}
    return {"ok": False, "error": " ".join(errors)}


def parse_models(output: str) -> list[dict]:
    """Parse output ``agy models``; dung sai cả tab lẫn nhiều khoảng trắng."""
    out: list[dict] = []
    seen: set[str] = set()
    for raw in (output or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("\t", 1)
        if len(parts) == 1:
            parts = re.split(r"\s{2,}", line, maxsplit=1)
        model_id = parts[0].strip()
        if not model_id or model_id in seen or " " in model_id:
            continue
        seen.add(model_id)
        out.append({
            "id": model_id,
            "display_name": parts[1].strip() if len(parts) > 1 else model_id,
        })
    return out


def list_models(timeout: float = 30.0, cli_path: Optional[str] = None) -> Optional[dict]:
    cli = cli_path or find_antigravity_cli()
    if not cli:
        return None
    try:
        result = subprocess.run(
            [cli, "models"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1.0, float(timeout)),
            creationflags=_no_window(),
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    items = parse_models(result.stdout)
    if result.returncode != 0 or not items:
        return None
    return {
        "models": [item["id"] for item in items],
        "items": items,
        "default_model": items[0]["id"],
        "source": "antigravity-cli",
    }


def _login_args(cli: str) -> list[str]:
    # Không gọi `agy models` trước OAuth. Khi chưa đăng nhập lệnh này chỉ tốn thêm
    # vài giây; trên VPS chậm nó còn có thể giữ credential store đúng lúc phiên
    # OAuth chuẩn bị mở. Model không cần thiết để CLI phát link đăng nhập.
    return [
        cli,
        "-p",
        "Reply with exactly OK.",
        "--output-format",
        "json",
        "--print-timeout",
        "90s",
    ]


def _login_process_env() -> dict:
    """Môi trường riêng cho OAuth, ép POSIX dùng flow URL của máy remote.

    Tài liệu AGY phân biệt máy local (tự mở browser) với SSH/headless (in URL +
    nhận code). Javis trên Docker được gọi qua HTTP nên không có biến SSH dù bản
    chất là máy remote. Gắn dấu SSH giả chỉ cho process OAuth để AGY không cố mở
    browser bên trong container.
    """
    env = dict(os.environ)
    env.setdefault("NO_COLOR", "1")
    env.setdefault("TERM", "xterm-256color")
    if os.name != "nt":
        if not str(env.get("SSH_CONNECTION") or "").strip():
            env["SSH_CONNECTION"] = "127.0.0.1 0 127.0.0.1 0"
        if not str(env.get("SSH_CLIENT") or "").strip():
            env["SSH_CLIENT"] = "127.0.0.1 0 0"
    return env


class _WindowsLoginPTY:
    """Terminal ẩn cho OAuth Windows; ``agy`` đọc code từ console, không từ pipe."""

    def __init__(self, args: list[str]):
        try:
            from winpty import Backend, PtyProcess
        except ImportError as exc:
            raise RuntimeError(
                "Thiếu pywinpty. Chạy lại setup.bat để cài thành phần đăng nhập Antigravity."
            ) from exc
        self._proc = PtyProcess.spawn(
            args,
            cwd=os.getcwd(),
            env=_login_process_env(),
            dimensions=(30, 2000),
            backend=Backend.WinPTY,
        )
        self.pid = self._proc.pid

    def read(self, size: int = 8192) -> str:
        return self._proc.read(size)

    def write(self, text: str) -> None:
        self._proc.write(text)

    def is_alive(self) -> bool:
        return self._proc.isalive()

    def terminate(self) -> None:
        try:
            self._proc.terminate(force=True)
        except Exception:
            pass


class _PosixLoginPTY:
    """PTY stdlib cho Linux/macOS/VPS, không cần thêm dependency runtime."""

    def __init__(self, args: list[str]):
        import fcntl
        import pty
        import termios

        master_fd, slave_fd = pty.openpty()
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 2000, 0, 0))
        # agy >=1.1.2 mở /dev/tty thay vì đọc stdin. Wrapper chạy trong process
        # riêng, tạo session và nhận slave PTY làm controlling terminal trước exec.
        wrapper = (
            "import fcntl, os, sys, termios;"
            "os.setsid();"
            "fcntl.ioctl(0, termios.TIOCSCTTY, 0);"
            "os.execv(sys.argv[1], sys.argv[1:])"
        )
        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-c", wrapper, *args],
                cwd=os.getcwd(),
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                env=_login_process_env(),
            )
        finally:
            os.close(slave_fd)
        self._master_fd = master_fd
        self.pid = self._proc.pid

    def read(self, size: int = 8192) -> str:
        return os.read(self._master_fd, size).decode("utf-8", "replace")

    def write(self, text: str) -> None:
        os.write(self._master_fd, text.encode("utf-8"))

    def is_alive(self) -> bool:
        return self._proc.poll() is None

    def terminate(self) -> None:
        try:
            if self.is_alive():
                import signal
                os.killpg(os.getpgid(self.pid), signal.SIGTERM)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        try:
            os.close(self._master_fd)
        except OSError:
            pass


def _spawn_login_pty(args: list[str]):
    return _WindowsLoginPTY(args) if os.name == "nt" else _PosixLoginPTY(args)


def _login_stop() -> None:
    proc = _LOGIN.get("proc")
    if proc:
        try:
            proc.terminate()
        except Exception:
            pass
    _LOGIN.update({
        "proc": None,
        "url": "",
        "ready": False,
        "done": False,
        "error": "",
        "lines": [],
    })


def _clean_terminal_text(text: str) -> str:
    return _ANSI_RE.sub("", text or "").replace("\r", "")


def _extract_oauth_url(text: str) -> str:
    """Ghép URL OAuth bị terminal wrap thành nhiều dòng.

    WinPTY có thể chèn newline giữa ``userinfo`` hoặc ngay trước ``&state``.
    Regex ``https?://\\S+`` khi đó chỉ lấy nửa đầu URL và làm PKCE code không
    khớp phiên đăng nhập.
    """
    clean = _clean_terminal_text(text)
    start = clean.rfind("https://accounts.google.com/")
    if start < 0:
        return ""
    tail = clean[start:]
    low = tail.lower()
    boundaries = [
        "if you aren't automatically redirected",
        "after authenticating, copy the code",
        "paste the authorization code",
        "waiting for authentication",
        "or, paste the authorization code",
    ]
    ends = [low.find(marker) for marker in boundaries if low.find(marker) >= 0]
    if ends:
        tail = tail[:min(ends)]
    # URL do CLI in riêng một block; khoảng trắng trong block chỉ là wrap của TTY.
    return re.sub(r"\s+", "", tail)


def _oauth_url_complete(url: str) -> bool:
    try:
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        return (
            parsed.scheme == "https"
            and parsed.netloc == "accounts.google.com"
            and bool(query.get("state", [""])[0])
            and bool(query.get("code_challenge", [""])[0])
            and bool(query.get("redirect_uri", [""])[0])
        )
    except Exception:
        return False


def _friendly_login_error(text: str) -> str:
    low = (text or "").lower()
    if "invalid_grant" in low or "malformed auth code" in low:
        return (
            "Authorization code không hợp lệ hoặc đã hết hạn. "
            "Bấm “Lấy link mới” rồi đăng nhập lại."
        )
    if "authentication timed out" in low or "authentication failed or timed out" in low:
        return (
            "Link đăng nhập đã hết hạn. Bấm “Lấy link mới” rồi hoàn tất trong 60 giây."
        )
    if "authentication interrupted" in low or "authentication failed" in low:
        return "Google không xác nhận được đăng nhập. Hãy lấy link mới và thử lại."
    match = re.search(r"got an error:\s*(.+)", text or "", re.I)
    if match:
        return match.group(1).strip()[:500]
    return ""


def _sanitized_login_tail(lines: list[str], limit: int = 6) -> str:
    """Trace ngắn để chẩn đoán VPS, nhưng không trả URL PKCE/token ra UI/log."""
    safe = []
    for raw in lines or []:
        line = _clean_terminal_text(str(raw)).strip()
        if not line:
            continue
        line = _URL_RE.sub("[link OAuth đã ẩn]", line)
        line = re.sub(
            r"\b(state|code_challenge|code|access_token|refresh_token)=\S+",
            r"\1=[đã ẩn]",
            line,
            flags=re.I,
        )
        safe.append(line[:500])
    return " | ".join(safe[-max(1, int(limit)):])[:1200]


def _login_storage_error() -> str:
    """Bắt volume auth sai owner/read-only trước khi AGY chết trong PTY."""
    root = Path.home() / ".gemini"
    probe = root / f".javis-write-probe-{os.getpid()}-{threading.get_ident()}"
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return ""
    except Exception as exc:  # noqa: BLE001 - cần trả lỗi filesystem đọc được
        try:
            probe.unlink(missing_ok=True)
        except Exception:
            pass
        message = (
            f"Antigravity không ghi được thư mục credential {root}: "
            f"{type(exc).__name__}: {exc}."
        )
        if os.name != "nt" and Path("/.dockerenv").exists():
            message += (
                " Trên VPS Docker, chạy: "
                "`docker compose exec -u root javis "
                "chown -R 10001:10001 /home/javis/.gemini`, rồi thử lại."
            )
        return message


def auth_login_ui_start() -> dict:
    """Bắt đầu Google Sign-In và trả URL để frontend mở."""
    cli = find_antigravity_cli()
    if not cli:
        return {"ok": False, "error": "Antigravity CLI chưa cài"}
    _login_stop()
    storage_error = _login_storage_error()
    if storage_error:
        return {"ok": False, "error": storage_error}
    try:
        proc = _spawn_login_pty(_login_args(cli))
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    _LOGIN["proc"] = proc
    reader_done = threading.Event()

    def read_terminal():
        raw_buffer = ""
        selected_google = False
        try:
            # Đọc tới EOF thay vì dừng ngay khi process vừa thoát. Trên VPS,
            # lỗi permission/keyring có thể làm AGY chết trước khi thread này
            # được lên lịch; PTY vẫn còn stderr chờ đọc.
            while True:
                try:
                    chunk = proc.read(8192)
                except (EOFError, OSError):
                    break
                if not chunk:
                    if not proc.is_alive():
                        break
                    continue
                raw_buffer = (raw_buffer + chunk)[-65536:]
                clean = _clean_terminal_text(raw_buffer)
                _LOGIN["lines"] = clean.splitlines()[-100:]
                low = clean.lower()
                if not selected_google and "select login method" in low:
                    proc.write("\r")
                    selected_google = True
                oauth_url = _extract_oauth_url(clean)
                if _oauth_url_complete(oauth_url):
                    _LOGIN["url"] = oauth_url
                    # Một số bản AGY/VPS chỉ in URL rồi chờ ở /dev/tty, không
                    # dùng đúng câu "authorization code" mà parser cũ đòi.
                    # URL đủ state + PKCE + redirect_uri đã là cổng an toàn.
                    _LOGIN["ready"] = True
                if (
                    "authorization code" in low
                    or "paste the code displayed in the browser" in low
                ):
                    _LOGIN["ready"] = _oauth_url_complete(_LOGIN["url"])
                if (
                    '"status":"success"' in low.replace(" ", "")
                    or "signed in successfully" in low
                    or "authentication successful" in low
                ):
                    _LOGIN["done"] = True
                error = _friendly_login_error(clean)
                if error:
                    _LOGIN["error"] = error
                    break
        except Exception as exc:  # noqa: BLE001 - trạng thái login phải giữ lỗi
            if proc.is_alive():
                _LOGIN["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            reader_done.set()

    threading.Thread(target=read_terminal, daemon=True).start()

    deadline = time.monotonic() + _LOGIN_START_WAIT_S
    while time.monotonic() < deadline:
        if _LOGIN["ready"] or _LOGIN["done"] or _LOGIN["error"]:
            break
        if not proc.is_alive() and reader_done.wait(timeout=0.2):
            break
        time.sleep(0.2)
    if not _LOGIN["ready"]:
        # Process đã chết nhanh thì cho reader một nhịp cuối để lấy stderr còn
        # nằm trong PTY trước khi dựng thông báo cho UI.
        if not proc.is_alive():
            reader_done.wait(timeout=1.0)
        if auth_status().get("connected"):
            _LOGIN["done"] = True
            return {"ok": True, "done": True, "url": ""}
        error = _LOGIN["error"]
        if not error:
            detail = _sanitized_login_tail(_LOGIN.get("lines") or [])
            stopped = not proc.is_alive()
            error = (
                "Antigravity CLI đã dừng trước khi phát link đăng nhập."
                if stopped else
                "Antigravity CLI chưa phát link đăng nhập trong thời gian chờ."
            )
            if detail:
                error += f" Chi tiết: {detail}"
        _login_stop()
        return {"ok": False, "error": error}
    return {"ok": True, "url": _LOGIN["url"], "done": False, "error": ""}


def auth_login_ui_code(code: str) -> dict:
    proc = _LOGIN.get("proc")
    clean_code = (code or "").strip()
    if not proc or not proc.is_alive() or not _LOGIN.get("ready"):
        return {
            "ok": False,
            "restart": True,
            "error": "Phiên đăng nhập đã hết hạn. Hãy lấy link mới.",
        }
    if not clean_code:
        return {"ok": False, "error": "Authorization code đang trống."}
    try:
        proc.write(clean_code + "\r")
    except Exception as exc:
        _login_stop()
        return {
            "ok": False,
            "restart": True,
            "error": f"Không gửi được code: {exc}",
        }
    # Không gọi `auth_status(force=True)` ở đây: nó spawn `agy models`, tranh
    # credential store với chính phiên OAuth đang mở và từng làm request treo.
    _AUTH_CACHE.update({"ts": 0.0, "connected": False})
    for _ in range(300):
        if _LOGIN["error"]:
            error = _LOGIN["error"]
            _login_stop()
            return {"ok": False, "restart": True, "error": error}
        if _LOGIN["done"] or _credential_present():
            _LOGIN["done"] = True
            _AUTH_CACHE.update({"ts": time.time(), "connected": True})
            _login_stop()
            return {"ok": True}
        if not proc.is_alive():
            break
        time.sleep(0.2)
    ok = _credential_present()
    if ok:
        _AUTH_CACHE.update({"ts": time.time(), "connected": True})
    _login_stop()
    return {
        "ok": ok,
        "restart": not ok,
        "error": "" if ok else "Đăng nhập thất bại hoặc đã hết thời gian chờ.",
    }


class _ProcessHandle:
    """Handle tương thích ``claude_cli.cancel_all`` nhưng giết cả process group POSIX."""

    def __init__(self, proc):
        self.proc = proc
        self.pid = proc.pid

    def terminate(self):
        if os.name == "nt":
            self.proc.terminate()
            return
        try:
            import signal
            os.killpg(os.getpgid(self.pid), signal.SIGTERM)
        except Exception:
            self.proc.terminate()


def _register_process(proc, tag: str):
    handle = _ProcessHandle(proc)
    try:
        import claude_cli
        with claude_cli._PROC_LOCK:
            claude_cli._ACTIVE_PROCS[handle] = tag
    except Exception:
        pass
    return handle


def _unregister_process(handle) -> None:
    try:
        import claude_cli
        with claude_cli._PROC_LOCK:
            claude_cli._ACTIVE_PROCS.pop(handle, None)
    except Exception:
        pass


def _ensure_global_mcp_config() -> None:
    """Đăng ký proxy Javis tĩnh trong config Antigravity, không ghi secret vào đĩa."""
    config_dir = Path.home() / ".gemini" / "config"
    path = config_dir / "mcp_config.json"
    proxy = Path(__file__).with_name("antigravity_mcp_proxy.py").resolve()
    expected = {"command": sys.executable, "args": [str(proxy)]}
    with _MCP_CONFIG_LOCK:
        try:
            raw = path.read_text(encoding="utf-8") if path.exists() else ""
            data = json.loads(raw) if raw.strip() else {}
        except Exception as exc:
            raise ValueError(f"Không đọc được {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{path} phải chứa một JSON object.")
        servers = data.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError(f"mcpServers trong {path} phải là một JSON object.")
        # Bản thử nghiệm cũ dùng tên `javis`. Chỉ dọn nếu entry trùng chính xác proxy này;
        # không chạm server riêng của người dùng vô tình cũng đặt tên javis.
        if servers.get("javis") == expected:
            servers.pop("javis", None)
        current = servers.get("javis-os")
        if current and current != expected:
            args = current.get("args") if isinstance(current, dict) else []
            ours = any(str(arg).endswith("antigravity_mcp_proxy.py") for arg in (args or []))
            if not ours:
                raise ValueError(
                    f"Server MCP tên javis-os trong {path} đã thuộc cấu hình khác.")
        if servers.get("javis-os") == expected:
            return
        servers["javis-os"] = expected
        config_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)


class AntigravityCLI:
    """Engine sự kiện tương thích ``CodexCLI``/Claude SDK của Javis."""

    def __init__(
        self,
        cwd: Optional[str] = None,
        tag: str = "chat",
        model: Optional[str] = None,
        instructions: Optional[str] = None,
        mode: str = "full",
        expose_workspace: bool = True,
        enable_mcp: bool = True,
        sandbox_native: bool = False,
        reasoning: str = "off",
    ):
        self.cli_path = find_antigravity_cli()
        self.cwd = str(Path(cwd or os.getcwd()).expanduser().resolve())
        self.tag = tag
        self.model = model
        self.instructions = instructions
        self.mode = mode
        self.expose_workspace = expose_workspace
        self.enable_mcp = enable_mcp
        self.sandbox_native = sandbox_native
        self.reasoning = reasoning
        self.session_id = None

    def is_available(self) -> bool:
        return self.cli_path is not None

    def reset_session(self) -> None:
        self.session_id = None

    @staticmethod
    def _timeout_seconds() -> int:
        try:
            return max(30, int(os.getenv("JAVIS_ANTIGRAVITY_TIMEOUT", "600")))
        except ValueError:
            return 600

    def _prepare_workspace(self) -> str:
        root = tempfile.mkdtemp(prefix="javis-antigravity-")
        path = Path(root)
        primary = (
            "JAVIS WORKSPACE RULES:\n"
            f"- The primary project workspace is: {self.cwd}\n"
            "- The directory containing this GEMINI.md is only an instruction mount.\n"
            "- Never create, edit, move, or delete project files in the instruction mount.\n"
            "- Resolve relative project paths under the primary project workspace.\n\n"
        )
        (path / "GEMINI.md").write_text(
            primary + (self.instructions or ""), encoding="utf-8")
        if self.enable_mcp:
            try:
                _ensure_global_mcp_config()
            except Exception as exc:
                print(f"[antigravity mcp] {type(exc).__name__}: {exc}", file=sys.stderr)
        return root

    def _promote_workspace_outputs(self, workspace: str) -> list[str]:
        """Cứu file mới nếu Antigravity chọn nhầm instruction mount làm workspace ghi.

        CLI 1.1.x ưu tiên root chứa ``GEMINI.md`` cho một số lệnh ``write_to_file`` dù
        process cwd là brain. Mọi file ngoài ``GEMINI.md`` trong mount là output của lượt,
        nên chuyển về cùng đường tương đối trong brain trước khi xoá thư mục tạm.
        """
        source_root = Path(workspace)
        target_root = Path(self.cwd)
        promoted = []
        try:
            target_root_resolved = target_root.resolve()
            for source in source_root.rglob("*"):
                if (source.is_symlink() or not source.is_file()
                        or source.relative_to(source_root).as_posix() == "GEMINI.md"):
                    continue
                relative = source.relative_to(source_root)
                target = (target_root / relative).resolve()
                if target != target_root_resolved and target_root_resolved not in target.parents:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                promoted.append(str(target))
        except Exception as exc:
            print(f"[antigravity files] {type(exc).__name__}: {exc}", file=sys.stderr)
        return promoted

    def _process_env(self) -> dict:
        env = dict(os.environ)
        env.setdefault("NO_COLOR", "1")
        if self.enable_mcp:
            try:
                import mcp_hub
                env.update({
                    "JAVIS_HUB_URL": mcp_hub.hub_url(),
                    "JAVIS_HUB_TOKEN": mcp_hub.hub_token(),
                    "JAVIS_HUB_MODE": self.mode or "full",
                    "JAVIS_HUB_VAULT": self.cwd,
                })
            except Exception:
                pass
        return env

    def _build_args(self, prompt: str, rules_dir: Optional[str] = None) -> list[str]:
        args = [
            self.cli_path,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--print-timeout",
            f"{self._timeout_seconds()}s",
        ]
        if self.model:
            args += ["--model", self.model]
        effort = {"low": "low", "medium": "medium", "high": "high",
                  "xhigh": "high", "ultra": "high"}.get(self.reasoning)
        if effort:
            args += ["--effort", effort]
        if self.session_id:
            args += ["--conversation", self.session_id]
        if rules_dir:
            args += ["--add-dir", rules_dir]
        if self.sandbox_native or self.mode == "suggest":
            args.append("--sandbox")
        if self.mode == "suggest":
            args += ["--mode", "plan"]
        elif self.mode == "auto":
            args += ["--mode", "accept-edits"]
        else:
            args.append("--dangerously-skip-permissions")
        return args

    async def query(self, prompt: str) -> AsyncIterator[dict]:
        if not self.cli_path:
            yield {
                "type": "error",
                "content": "Không tìm thấy Antigravity CLI. Cài lệnh `agy` rồi kết nối ở trang Models.",
            }
            return

        workspace = self._prepare_workspace()
        proc = None
        proc_handle = None
        stderr_lines: list[str] = []
        emitted_text = False
        got_result = False
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._build_args(prompt, rules_dir=workspace),
                cwd=self.cwd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._process_env(),
                creationflags=_no_window(),
                start_new_session=(os.name != "nt"),
            )
            proc_handle = _register_process(proc, self.tag)

            async def read_stderr():
                assert proc is not None and proc.stderr is not None
                while True:
                    raw = await proc.stderr.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", "replace").rstrip()
                    if line:
                        stderr_lines.append(line)

            stderr_task = asyncio.create_task(read_stderr())
            assert proc.stdout is not None
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = event.get("event")
                if kind == "init":
                    sid = event.get("conversation_id") or (event.get("init") or {}).get("conversation_id")
                    if sid:
                        self.session_id = sid
                        yield {"type": "session", "session_id": sid}
                    continue
                if kind == "step_update":
                    step = event.get("step_update") or {}
                    step_type = step.get("step_type")
                    if step_type == "agent_response" and step.get("text_delta"):
                        emitted_text = True
                        yield {"type": "text", "content": step.get("text_delta") or ""}
                    elif step_type == "tool" and step.get("state") == "ACTIVE":
                        info = step.get("tool_info") or {}
                        yield {
                            "type": "tool_call",
                            "name": step.get("tool_name") or info.get("name") or "tool",
                            "input": info.get("parameters") or {},
                            "item": info,
                        }
                    elif step_type == "tool" and step.get("state") == "DONE":
                        info = step.get("tool_info") or {}
                        yield {
                            "type": "tool_result",
                            "content": info.get("output") or f"{step.get('tool_name') or 'tool'} xong",
                        }
                    continue
                if kind != "result":
                    continue
                got_result = True
                result = event.get("result") or {}
                sid = result.get("conversation_id") or self.session_id
                if sid:
                    self.session_id = sid
                usage = result.get("usage") or {}
                if result.get("status") == "SUCCESS":
                    yield {
                        "type": "final",
                        "content": result.get("response") or "",
                        "session_id": sid,
                        "tokens_in": int(usage.get("input_tokens") or 0),
                        "tokens_out": int(usage.get("output_tokens") or 0),
                        "thinking_tokens": int(usage.get("thinking_tokens") or 0),
                    }
                else:
                    message = str(result.get("error") or "Antigravity không trả về nội dung.")
                    error_event = {
                        "type": "error",
                        "content": message,
                        "resume_failed": bool(self.session_id and _RESUME_ERROR.search(message)),
                    }
                    if _TRANSIENT_ERROR.search(message):
                        error_event["tam_thoi"] = True
                        status = re.search(r"\b(408|429|502|503|504|529)\b", message)
                        if status:
                            error_event["ma"] = int(status.group(1))
                    yield error_event

            await proc.wait()
            await stderr_task
            if proc.returncode and not emitted_text and not got_result:
                detail = "\n".join(stderr_lines[-8:]).strip()
                if detail:
                    yield {
                        "type": "error",
                        "content": detail[:1200],
                        "resume_failed": bool(self.session_id and _RESUME_ERROR.search(detail)),
                    }
        except asyncio.CancelledError:
            if proc and proc.returncode is None:
                if proc_handle:
                    proc_handle.terminate()
                else:
                    proc.kill()
            raise
        except Exception as exc:  # noqa: BLE001 - engine phải trả sự kiện lỗi, không nổ caller
            yield {"type": "error", "content": f"Antigravity CLI: {type(exc).__name__}: {exc}"}
        finally:
            if proc is not None:
                _unregister_process(proc_handle)
                if proc.returncode is None:
                    try:
                        if proc_handle:
                            proc_handle.terminate()
                        else:
                            proc.kill()
                    except ProcessLookupError:
                        pass
            self._promote_workspace_outputs(workspace)
            shutil.rmtree(workspace, ignore_errors=True)


def messages_stream(
    model: str,
    messages: list[dict],
    reasoning: str = "off",
    *,
    cwd: Optional[str] = None,
    mode: str = "suggest",
    expose_workspace: bool = False,
    enable_mcp: bool = False,
) -> AsyncIterator[dict]:
    """Đổi hợp đồng message-stream của Javis sang một lượt Antigravity CLI.

    Dùng cho Fast Path và chatbot. Đường chat chính dùng ``AntigravityCLI`` trực
    tiếp để giữ ``conversation_id`` native.
    """
    system = "\n\n".join(
        str(message.get("content") or "")
        for message in messages
        if message.get("role") == "system" and message.get("content")
    )
    turns = []
    for message in messages:
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        turns.append(f"{'USER' if role == 'user' else 'ASSISTANT'}:\n{message.get('content') or ''}")
    prompt = "\n\n".join(turns) or "Hãy trả lời theo system prompt."

    async def _stream():
        cli = AntigravityCLI(
            cwd=cwd,
            tag="antigravity-stream",
            model=model or None,
            instructions=system or None,
            mode=mode,
            expose_workspace=expose_workspace,
            enable_mcp=enable_mcp,
            sandbox_native=not expose_workspace,
            reasoning=reasoning,
        )
        streamed = False
        actual_model = model or "mặc định"
        yield {"type": "meta", "model": actual_model}
        async for event in cli.query(prompt):
            kind = event.get("type")
            if kind == "text":
                streamed = True
                yield event
            elif kind == "final":
                if not streamed and event.get("content"):
                    yield {"type": "text", "content": event.get("content") or ""}
                yield {
                    "type": "usage",
                    "input": event.get("tokens_in", 0),
                    "output": event.get("tokens_out", 0),
                }
            elif kind in ("tool_call", "tool_result", "error"):
                yield event

    return _stream()
