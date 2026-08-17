#!/usr/bin/env python3
"""
Brain OS runtime probe.

Mục tiêu:
- Xác minh môi trường runtime của Javis/Brain trước khi triển khai Brain OS.
- Không phụ thuộc vào Javis internals.
- Chỉ dùng standard library; YAML chỉ được kiểm tra như một capability.
- Không để lại file test sau khi chạy (trừ khi dùng --output).

Exit codes:
  0: mọi check bắt buộc đều đạt
  2: có check bắt buộc thất bại
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import sqlite3
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = 1
MIN_PYTHON = (3, 9)


@dataclass
class CheckResult:
    name: str
    ok: bool
    required: bool
    details: dict[str, Any]
    error: str = ""


def _result(
    name: str,
    ok: bool,
    *,
    required: bool = True,
    error: str = "",
    **details: Any,
) -> CheckResult:
    return CheckResult(
        name=name,
        ok=bool(ok),
        required=bool(required),
        details=details,
        error=str(error or ""),
    )


def infer_brain_root(script_path: Path) -> Path | None:
    """Infer <brain> from <brain>/skills/brain-manager/scripts/probe_runtime.py."""
    p = script_path.resolve()
    try:
        scripts_dir = p.parent
        skill_dir = scripts_dir.parent
        skills_dir = skill_dir.parent
        if (
            scripts_dir.name == "scripts"
            and skill_dir.name == "brain-manager"
            and skills_dir.name == "skills"
        ):
            return skills_dir.parent.resolve()
    except OSError:
        return None
    return None


def check_python() -> CheckResult:
    v = sys.version_info
    ok = (v.major, v.minor) >= MIN_PYTHON
    return _result(
        "python",
        ok,
        required=True,
        version=platform.python_version(),
        implementation=platform.python_implementation(),
        executable=sys.executable,
        minimum=f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}",
    )


def check_brain_root(brain_root: Path) -> CheckResult:
    try:
        resolved = brain_root.resolve()
        ok = resolved.exists() and resolved.is_dir()
        return _result(
            "brain_root",
            ok,
            required=True,
            path=str(resolved),
            exists=resolved.exists(),
            is_dir=resolved.is_dir(),
        )
    except Exception as exc:
        return _result(
            "brain_root",
            False,
            required=True,
            error=f"{type(exc).__name__}: {exc}",
            path=str(brain_root),
        )


def check_sqlite() -> CheckResult:
    try:
        conn = sqlite3.connect(":memory:")
        try:
            row = conn.execute("select sqlite_version()").fetchone()
            version = row[0] if row else ""
            conn.execute("create table probe(id integer primary key, value text not null)")
            conn.execute("insert into probe(value) values (?)", ("ok",))
            value = conn.execute("select value from probe").fetchone()[0]
        finally:
            conn.close()
        return _result(
            "sqlite",
            value == "ok",
            required=True,
            sqlite_version=version,
            module_version=sqlite3.version,
        )
    except Exception as exc:
        return _result(
            "sqlite",
            False,
            required=True,
            error=f"{type(exc).__name__}: {exc}",
        )


def check_fts5() -> CheckResult:
    """FTS5 is useful but optional for Brain OS V1."""
    try:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("create virtual table fts_probe using fts5(content)")
            conn.execute("insert into fts_probe(content) values (?)", ("brain os",))
            count = conn.execute(
                "select count(*) from fts_probe where fts_probe match 'brain'"
            ).fetchone()[0]
        finally:
            conn.close()
        return _result("sqlite_fts5", count == 1, required=False, available=True)
    except Exception as exc:
        return _result(
            "sqlite_fts5",
            False,
            required=False,
            error=f"{type(exc).__name__}: {exc}",
            available=False,
        )


def check_yaml() -> CheckResult:
    modules = {
        name: importlib.util.find_spec(name) is not None
        for name in ("yaml", "fastyaml")
    }
    return _result("yaml", any(modules.values()), required=True, modules=modules)


def _cleanup_dir(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def check_write_and_atomic_replace(brain_root: Path) -> CheckResult:
    probe_dir = brain_root / ".javis" / ".brain-os-probe"
    src: Path | None = None
    dst: Path | None = None
    try:
        probe_dir.mkdir(parents=True, exist_ok=True)
        token = f"{os.getpid()}-{time.time_ns()}"
        src = probe_dir / f"atomic-{token}.tmp"
        dst = probe_dir / f"atomic-{token}.txt"
        payload = "brain-os-atomic-write\n"
        src.write_text(payload, encoding="utf-8", newline="\n")
        src.replace(dst)
        roundtrip = dst.read_text(encoding="utf-8")
        return _result(
            "brain_write_atomic_replace",
            roundtrip == payload,
            required=True,
            directory=str(probe_dir),
            atomic_replace=True,
        )
    except Exception as exc:
        return _result(
            "brain_write_atomic_replace",
            False,
            required=True,
            error=f"{type(exc).__name__}: {exc}",
            directory=str(probe_dir),
        )
    finally:
        for p in (src, dst):
            if p is not None:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
        _cleanup_dir(probe_dir)


def check_unicode_filename(brain_root: Path) -> CheckResult:
    probe_dir = brain_root / ".javis" / ".brain-os-probe"
    fp: Path | None = None
    try:
        probe_dir.mkdir(parents=True, exist_ok=True)
        token = f"{os.getpid()}-{time.time_ns()}"
        fp = probe_dir / f"Kiểm-tra-tiếng-Việt-{token}.md"
        payload = "# Kiểm tra\n\nTiếng Việt: thuế, kế toán, điều tôi học được.\n"
        fp.write_text(payload, encoding="utf-8", newline="\n")
        roundtrip = fp.read_text(encoding="utf-8")
        ok = roundtrip == payload and fp.name.startswith("Kiểm-tra-tiếng-Việt-")
        return _result(
            "unicode_filename_utf8",
            ok,
            required=True,
            filename=fp.name,
        )
    except Exception as exc:
        return _result(
            "unicode_filename_utf8",
            False,
            required=True,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if fp is not None:
            try:
                fp.unlink(missing_ok=True)
            except OSError:
                pass
        _cleanup_dir(probe_dir)


def check_sha256(brain_root: Path) -> CheckResult:
    probe_dir = brain_root / ".javis" / ".brain-os-probe"
    fp: Path | None = None
    try:
        probe_dir.mkdir(parents=True, exist_ok=True)
        token = f"{os.getpid()}-{time.time_ns()}"
        fp = probe_dir / f"sha-{token}.bin"
        payload = b"brain-os-sha256-probe\x00\xff"
        fp.write_bytes(payload)

        h = hashlib.sha256()
        with fp.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)

        expected = hashlib.sha256(payload).hexdigest()
        actual = h.hexdigest()
        return _result(
            "sha256_streaming",
            actual == expected,
            required=True,
            digest=actual,
        )
    except Exception as exc:
        return _result(
            "sha256_streaming",
            False,
            required=True,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if fp is not None:
            try:
                fp.unlink(missing_ok=True)
            except OSError:
                pass
        _cleanup_dir(probe_dir)


def check_tempfile(brain_root: Path) -> CheckResult:
    probe_dir = brain_root / ".javis" / ".brain-os-probe"
    created: Path | None = None
    try:
        probe_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=probe_dir,
            prefix="tmp-",
            suffix=".txt",
            delete=False,
        ) as fh:
            fh.write("ok")
            created = Path(fh.name)

        ok = created.read_text(encoding="utf-8") == "ok"
        return _result(
            "tempfile_in_brain",
            ok,
            required=True,
            path=str(created),
        )
    except Exception as exc:
        return _result(
            "tempfile_in_brain",
            False,
            required=True,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if created is not None:
            try:
                created.unlink(missing_ok=True)
            except OSError:
                pass
        _cleanup_dir(probe_dir)


def run_checks(brain_root: Path) -> list[CheckResult]:
    checks: list[Callable[[], CheckResult]] = [
        check_python,
        lambda: check_brain_root(brain_root),
        check_sqlite,
        check_fts5,
        check_yaml,
        lambda: check_write_and_atomic_replace(brain_root),
        lambda: check_unicode_filename(brain_root),
        lambda: check_sha256(brain_root),
        lambda: check_tempfile(brain_root),
    ]
    return [fn() for fn in checks]


def build_report(
    brain_root: Path,
    checks: list[CheckResult],
) -> dict[str, Any]:
    required_failures = [c.name for c in checks if c.required and not c.ok]
    optional_failures = [c.name for c in checks if not c.required and not c.ok]
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": not required_failures,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "brain_root": str(brain_root),
        "script": str(Path(__file__).resolve()),
        "cwd": str(Path.cwd()),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "summary": {
            "required_failures": required_failures,
            "optional_failures": optional_failures,
        },
        "checks": [asdict(c) for c in checks],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe runtime capabilities required by Brain OS V1."
    )
    parser.add_argument(
        "--brain-root",
        help=(
            "Brain/vault root. Omit when script is installed under "
            "<brain>/skills/brain-manager/scripts/."
        ),
    )
    parser.add_argument(
        "--output",
        help=(
            "Optional JSON report path. Relative paths are resolved from "
            "current working directory."
        ),
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Print compact JSON instead of pretty JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.brain_root:
        brain_root = Path(args.brain_root).expanduser().resolve()
    else:
        inferred = infer_brain_root(Path(__file__))
        if inferred is None:
            report = {
                "schema_version": SCHEMA_VERSION,
                "ok": False,
                "error": (
                    "Không suy được brain root từ vị trí script. "
                    "Hãy cài script tại <brain>/skills/brain-manager/scripts/ "
                    "hoặc truyền --brain-root."
                ),
                "script": str(Path(__file__).resolve()),
                "cwd": str(Path.cwd()),
            }
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2
        brain_root = inferred

    checks = run_checks(brain_root)
    report = build_report(brain_root, checks)

    if args.output:
        output = Path(args.output).expanduser()
        if not output.is_absolute():
            output = (Path.cwd() / output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    if args.compact:
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
