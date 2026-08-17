from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable

from .models import BrainFile, ChangeKind, FileFingerprint, ProcessingState


@dataclass(frozen=True)
class FileObservation:
    """One filesystem observation collected during a scan."""

    fingerprint: FileFingerprint
    zone: str
    fs_device: int = 0
    fs_inode: int = 0
    javis_id: str = ""

    @property
    def path(self) -> str:
        return self.fingerprint.path

    @property
    def sha256(self) -> str:
        return self.fingerprint.sha256


@dataclass(frozen=True)
class MatchDecision:
    file: BrainFile | None
    method: str = ""
    ambiguous: bool = False


def file_fs_identity(item: BrainFile) -> tuple[int, int]:
    scan_meta = (item.metadata or {}).get("scan") or {}
    try:
        return int(scan_meta.get("fs_device", 0)), int(scan_meta.get("fs_inode", 0))
    except (TypeError, ValueError):
        return 0, 0


def choose_existing_match(
    observation: FileObservation,
    candidates: Iterable[BrainFile],
) -> MatchDecision:
    """Find a unique prior DB row for a path that appeared somewhere else.

    Matching order is intentionally conservative:
    1. explicit javis_id in Markdown,
    2. filesystem device+inode,
    3. exact content hash + size + suffix, only when unique.

    Ambiguous matches are never guessed.
    """

    pool = list(candidates)

    if observation.javis_id:
        hits = [f for f in pool if f.source_id == observation.javis_id]
        if len(hits) == 1:
            return MatchDecision(hits[0], method="frontmatter")
        if len(hits) > 1:
            return MatchDecision(None, method="frontmatter", ambiguous=True)

    if observation.fs_inode:
        hits = []
        for item in pool:
            dev, ino = file_fs_identity(item)
            inode_matches = ino and ino == observation.fs_inode and dev == observation.fs_device
            # An inode from a row that was already MISSING may have been reused by the OS.
            # Require matching content in that case; for a file that disappeared only in
            # this scan, inode identity is strong enough to recognize rename+edit.
            safe_missing_match = (
                item.state != ProcessingState.MISSING
                or (item.content_hash and item.content_hash == observation.sha256)
            )
            if inode_matches and safe_missing_match:
                hits.append(item)
        if len(hits) == 1:
            return MatchDecision(hits[0], method="inode")
        if len(hits) > 1:
            return MatchDecision(None, method="inode", ambiguous=True)

    suffix = observation.fingerprint.suffix
    hits = [
        f
        for f in pool
        if f.content_hash
        and f.content_hash == observation.sha256
        and int(f.size) == int(observation.fingerprint.size)
        and (not f.file_type or f.file_type == suffix)
    ]
    if len(hits) == 1:
        return MatchDecision(hits[0], method="content_hash")
    if len(hits) > 1:
        return MatchDecision(None, method="content_hash", ambiguous=True)

    return MatchDecision(None)


def move_change_kind(old_path: str, new_path: str) -> ChangeKind:
    old_parent = PurePosixPath(old_path).parent
    new_parent = PurePosixPath(new_path).parent
    return ChangeKind.RENAMED if old_parent == new_parent else ChangeKind.MOVED


def state_after_content_change(old: BrainFile, new_hash: str) -> ProcessingState:
    """Preserve workflow state unless previously ingested knowledge became stale."""

    if old.state == ProcessingState.MISSING:
        return ProcessingState.DISCOVERED

    if old.last_ingested_hash and old.last_ingested_hash != new_hash:
        return ProcessingState.STALE

    if old.state in {ProcessingState.INGESTED, ProcessingState.COMPOUNDED}:
        return ProcessingState.STALE

    return old.state


def state_after_restore(old: BrainFile, new_hash: str) -> ProcessingState:
    if old.last_ingested_hash and old.last_ingested_hash != new_hash:
        return ProcessingState.STALE
    if old.last_ingested_hash and old.last_ingested_hash == new_hash:
        return ProcessingState.INGESTED
    return ProcessingState.DISCOVERED
