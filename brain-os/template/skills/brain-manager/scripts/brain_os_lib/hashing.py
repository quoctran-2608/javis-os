from __future__ import annotations

import hashlib
from pathlib import Path

from .models import FileFingerprint


DEFAULT_CHUNK_SIZE = 1024 * 1024


class FileChangedDuringHash(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str, *, encoding: str = "utf-8") -> str:
    return sha256_bytes(text.encode(encoding))


def sha256_file(path: Path | str, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    fp = Path(path)
    h = hashlib.sha256()
    with fp.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _relative_posix(path: Path, brain_root: Path) -> str:
    resolved = path.resolve()
    root = brain_root.resolve()
    try:
        rel = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"File nằm ngoài Brain root: {resolved}") from exc
    return rel.as_posix()


def fingerprint_file(
    path: Path | str,
    *,
    brain_root: Path | str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    retries: int = 1,
) -> FileFingerprint:
    """Hash a file and guard against concurrent modification.

    Obsidian may autosave while a scan is running. We compare stat before/after
    hashing and retry once by default instead of recording a mixed fingerprint.
    """

    fp = Path(path)
    root = Path(brain_root)

    last_before = None
    last_after = None
    for _attempt in range(retries + 1):
        before = fp.stat()
        digest = sha256_file(fp, chunk_size=chunk_size)
        after = fp.stat()
        last_before, last_after = before, after

        stable = (
            before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns
        )
        if stable:
            return FileFingerprint(
                path=_relative_posix(fp, root),
                size=after.st_size,
                mtime_ns=after.st_mtime_ns,
                sha256=digest,
                suffix=fp.suffix.lower(),
            )

    raise FileChangedDuringHash(
        "File thay đổi trong lúc hash: "
        f"{fp} (before size/mtime={last_before.st_size}/{last_before.st_mtime_ns}, "
        f"after={last_after.st_size}/{last_after.st_mtime_ns})"
    )


def stat_signature(path: Path | str) -> tuple[int, int]:
    st = Path(path).stat()
    return st.st_size, st.st_mtime_ns
