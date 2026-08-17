from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DiffHunk:
    tag: str
    old_start: int
    old_end: int
    new_start: int
    new_end: int
    old_text: str
    new_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TextDiff:
    changed: bool
    old_lines: int
    new_lines: int
    changed_old_lines: int
    changed_new_lines: int
    hunks: tuple[DiffHunk, ...]
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "old_lines": self.old_lines,
            "new_lines": self.new_lines,
            "changed_old_lines": self.changed_old_lines,
            "changed_new_lines": self.changed_new_lines,
            "hunks": [h.to_dict() for h in self.hunks],
            "truncated": self.truncated,
        }


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…[truncated]"


def diff_text(
    old_text: str,
    new_text: str,
    *,
    max_hunks: int = 12,
    max_chars_per_side: int = 2000,
) -> TextDiff:
    """Return deterministic line-oriented change hunks.

    The result is intentionally compact enough to persist in the event journal.
    Full previous text lives only in the derived snapshot cache.
    """

    if old_text == new_text:
        lines = len(old_text.splitlines())
        return TextDiff(False, lines, lines, 0, 0, ())

    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    matcher = SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)

    hunks: list[DiffHunk] = []
    changed_old = 0
    changed_new = 0
    truncated = False

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue

        changed_old += i2 - i1
        changed_new += j2 - j1

        if len(hunks) >= max_hunks:
            truncated = True
            continue

        old_side = "".join(old_lines[i1:i2])
        new_side = "".join(new_lines[j1:j2])
        hunks.append(
            DiffHunk(
                tag=tag,
                old_start=i1 + 1,
                old_end=i2,
                new_start=j1 + 1,
                new_end=j2,
                old_text=_clip(old_side, max_chars_per_side),
                new_text=_clip(new_side, max_chars_per_side),
            )
        )

    return TextDiff(
        changed=True,
        old_lines=len(old_lines),
        new_lines=len(new_lines),
        changed_old_lines=changed_old,
        changed_new_lines=changed_new,
        hunks=tuple(hunks),
        truncated=truncated,
    )


class SnapshotStore:
    """Derived text cache used only for incremental diffing.

    Files are addressed by sha256(source_id), so arbitrary existing IDs remain
    safe on Windows/macOS/Linux. Deleting this directory never loses user data;
    the next scan simply rebuilds snapshots from current Markdown.
    """

    def __init__(self, state_root: Path | str, *, max_bytes: int = 2 * 1024 * 1024):
        self.root = Path(state_root).expanduser().resolve() / "snapshots"
        self.max_bytes = max(0, int(max_bytes))

    @staticmethod
    def _key(source_id: str) -> str:
        return hashlib.sha256(str(source_id).encode("utf-8")).hexdigest()

    def path_for(self, source_id: str) -> Path:
        return self.root / f"{self._key(source_id)}.txt"

    def read(self, source_id: str) -> str | None:
        path = self.path_for(source_id)
        if not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None

    def read_current_file(self, path: Path | str) -> str | None:
        fp = Path(path)
        try:
            if self.max_bytes and fp.stat().st_size > self.max_bytes:
                return None
            # utf-8-sig removes only an optional BOM from comparison text.
            return fp.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            return None

    def write(self, source_id: str, text: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.path_for(source_id)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.stem}-",
            suffix=".tmp",
            dir=str(self.root),
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(target)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def capture(self, source_id: str, path: Path | str) -> str | None:
        text = self.read_current_file(path)
        if text is not None:
            self.write(source_id, text)
        return text
