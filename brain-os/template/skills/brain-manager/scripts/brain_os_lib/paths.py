from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .config import BrainOSConfig


class BrainPathError(ValueError):
    pass


def normalize_rel_path(value: Path | str) -> str:
    raw = str(value).replace("\\", "/").strip()
    if raw in ("", "."):
        return ""
    if raw.startswith("/"):
        raise BrainPathError(f"Path phải relative: {value!r}")

    parts = PurePosixPath(raw).parts
    if any(part in ("", ".", "..") for part in parts):
        raise BrainPathError(f"Path không an toàn: {value!r}")
    return PurePosixPath(*parts).as_posix()


def relative_to_brain(brain_root: Path | str, path: Path | str) -> str:
    root = Path(brain_root).resolve()
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise BrainPathError(f"Path nằm ngoài Brain: {resolved}") from exc


def safe_join(brain_root: Path | str, rel_path: Path | str) -> Path:
    root = Path(brain_root).resolve()
    rel = normalize_rel_path(rel_path)
    target = root if not rel else root.joinpath(*rel.split("/"))
    resolved = target.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise BrainPathError(f"Path traversal bị chặn: {rel_path!r}") from exc
    return resolved


def is_under(rel_path: str, prefix: str) -> bool:
    rel = normalize_rel_path(rel_path)
    pref = normalize_rel_path(prefix)
    return rel == pref or rel.startswith(pref + "/")


@dataclass(frozen=True)
class PathDecision:
    path: str
    zone: str
    ignored: bool
    policy: dict[str, Any]


class BrainPaths:
    def __init__(self, config: BrainOSConfig):
        self.config = config
        self.root = config.brain_root
        self._zones = tuple((config.core.get("zones") or {}).keys())
        self._ignore = config.ignore_paths

    def rel(self, path: Path | str) -> str:
        p = Path(path)
        if p.is_absolute():
            return relative_to_brain(self.root, p)
        return normalize_rel_path(p)

    def abs(self, rel_path: Path | str) -> Path:
        return safe_join(self.root, rel_path)

    def zone_for(self, rel_path: Path | str) -> str:
        rel = self.rel(rel_path)
        if not rel:
            return ""
        first = rel.split("/", 1)[0]
        if first in self._zones:
            return first
        return ""

    def is_ignored(self, rel_path: Path | str) -> bool:
        rel = self.rel(rel_path)
        if not rel:
            return False
        return any(is_under(rel, prefix) for prefix in self._ignore)

    def decision(self, rel_path: Path | str) -> PathDecision:
        rel = self.rel(rel_path)
        zone = self.zone_for(rel)
        ignored = self.is_ignored(rel)
        policy = self.config.zone_policy(zone) if zone else {}
        return PathDecision(path=rel, zone=zone, ignored=ignored, policy=policy)

    def ensure_state_dir(self) -> Path:
        state = self.config.path("state")
        state.mkdir(parents=True, exist_ok=True)
        return state
