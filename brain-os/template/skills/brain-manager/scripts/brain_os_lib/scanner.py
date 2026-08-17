from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path

from .changes import FileObservation
from .config import BrainOSConfig
from .hashing import FileChangedDuringHash, fingerprint_file
from .identity import MARKDOWN_SUFFIXES, read_javis_id
from .models import BrainFile, FileFingerprint, ProcessingState
from .paths import BrainPaths, relative_to_brain


DEFAULT_EXTENSIONS = (".md", ".markdown")


@dataclass
class ScanCollection:
    observations: list[FileObservation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    traversal_errors: list[str] = field(default_factory=list)
    uncertain_paths: set[str] = field(default_factory=set)
    duplicate_javis_ids: set[str] = field(default_factory=set)
    skipped_ignored: int = 0
    skipped_hidden: int = 0
    skipped_symlink: int = 0
    skipped_extension: int = 0
    reused_hashes: int = 0
    hashed_files: int = 0

    @property
    def deletion_safe(self) -> bool:
        return not self.traversal_errors


def _scan_options(config: BrainOSConfig) -> dict:
    raw = config.core.get("scan") or {}
    extensions = raw.get("extensions") or list(DEFAULT_EXTENSIONS)
    if not isinstance(extensions, list):
        extensions = list(DEFAULT_EXTENSIONS)
    normalized = []
    for value in extensions:
        ext = str(value or "").strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = "." + ext
        normalized.append(ext)
    return {
        "extensions": tuple(dict.fromkeys(normalized)) or DEFAULT_EXTENSIONS,
        "ignore_hidden": bool(raw.get("ignore_hidden", True)),
        "follow_symlinks": bool(raw.get("follow_symlinks", False)),
        "hash_retries": max(0, int(raw.get("hash_retries", 1) or 0)),
    }


def _is_hidden_rel(rel: str) -> bool:
    return any(part.startswith(".") for part in rel.split("/") if part)


def _existing_fast_fingerprint(
    path: Path,
    *,
    brain_root: Path,
    old: BrainFile | None,
    full_hash: bool,
) -> FileFingerprint | None:
    if full_hash or old is None or old.state == ProcessingState.MISSING:
        return None
    try:
        st = path.stat()
    except OSError:
        return None
    if (
        old.content_hash
        and int(old.size) == int(st.st_size)
        and int(old.mtime_ns) == int(st.st_mtime_ns)
    ):
        return FileFingerprint(
            path=relative_to_brain(brain_root, path),
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
            sha256=old.content_hash,
            suffix=path.suffix.lower(),
        )
    return None


def _neutralize_duplicate_javis_ids(result: ScanCollection) -> None:
    counts = Counter(obs.javis_id for obs in result.observations if obs.javis_id)
    duplicates = {value for value, count in counts.items() if count > 1}
    if not duplicates:
        return

    result.duplicate_javis_ids.update(duplicates)
    for value in sorted(duplicates):
        paths = [obs.path for obs in result.observations if obs.javis_id == value]
        result.warnings.append(
            f"duplicate javis_id {value!r} trong cùng scan: {paths}; bỏ identity này và không đoán."
        )

    # Do not let iteration order decide which copied note owns a duplicated ID.
    result.observations = [
        replace(obs, javis_id="") if obs.javis_id in duplicates else obs
        for obs in result.observations
    ]


def collect_files(
    config: BrainOSConfig,
    *,
    existing_by_path: dict[str, BrainFile] | None = None,
    full_hash: bool = False,
) -> ScanCollection:
    """Enumerate eligible knowledge files without modifying the Brain.

    Directory traversal errors make deletion detection unsafe for that run.
    Individual hash/read errors suppress deletion only for the affected path.
    """

    existing_by_path = existing_by_path or {}
    paths = BrainPaths(config)
    opts = _scan_options(config)
    result = ScanCollection()
    root = config.brain_root

    def onerror(exc: OSError) -> None:
        result.traversal_errors.append(
            f"{type(exc).__name__}: {getattr(exc, 'filename', '')}: {exc}"
        )

    for dirpath, dirnames, filenames in os.walk(
        root,
        topdown=True,
        onerror=onerror,
        followlinks=opts["follow_symlinks"],
    ):
        current_dir = Path(dirpath)

        kept_dirs: list[str] = []
        for name in dirnames:
            child = current_dir / name
            # Check symlinks before resolving relative paths. A symlink may point outside the Brain;
            # skipping it directly avoids both accidental escape and a false traversal error.
            if child.is_symlink() and not opts["follow_symlinks"]:
                result.skipped_symlink += 1
                continue
            try:
                rel = relative_to_brain(root, child)
            except Exception as exc:
                result.traversal_errors.append(f"{child}: {type(exc).__name__}: {exc}")
                continue

            if paths.is_ignored(rel):
                result.skipped_ignored += 1
                continue
            if opts["ignore_hidden"] and _is_hidden_rel(rel):
                result.skipped_hidden += 1
                continue
            kept_dirs.append(name)
        dirnames[:] = kept_dirs

        for name in filenames:
            fp = current_dir / name
            if fp.is_symlink():
                result.skipped_symlink += 1
                continue
            try:
                rel = relative_to_brain(root, fp)
            except Exception as exc:
                result.warnings.append(f"{fp}: outside/invalid path: {exc}")
                continue

            if paths.is_ignored(rel):
                result.skipped_ignored += 1
                continue
            if opts["ignore_hidden"] and _is_hidden_rel(rel):
                result.skipped_hidden += 1
                continue
            if fp.suffix.lower() not in opts["extensions"]:
                result.skipped_extension += 1
                continue

            try:
                old = existing_by_path.get(rel)
                fingerprint = _existing_fast_fingerprint(
                    fp,
                    brain_root=root,
                    old=old,
                    full_hash=full_hash,
                )
                if fingerprint is None:
                    fingerprint = fingerprint_file(
                        fp,
                        brain_root=root,
                        retries=opts["hash_retries"],
                    )
                    result.hashed_files += 1
                else:
                    result.reused_hashes += 1

                st = fp.stat()
                zone = paths.zone_for(rel)
                javis_id = ""
                if fp.suffix.lower() in MARKDOWN_SUFFIXES:
                    try:
                        javis_id = read_javis_id(fp)
                    except Exception as exc:
                        result.warnings.append(
                            f"{rel}: không đọc được javis_id: {type(exc).__name__}: {exc}"
                        )

                result.observations.append(
                    FileObservation(
                        fingerprint=fingerprint,
                        zone=zone,
                        fs_device=int(getattr(st, "st_dev", 0) or 0),
                        fs_inode=int(getattr(st, "st_ino", 0) or 0),
                        javis_id=javis_id,
                    )
                )
            except FileChangedDuringHash as exc:
                result.uncertain_paths.add(rel)
                result.warnings.append(f"{rel}: {exc}")
            except (OSError, ValueError) as exc:
                result.uncertain_paths.add(rel)
                result.warnings.append(f"{rel}: {type(exc).__name__}: {exc}")

    result.observations.sort(key=lambda item: item.path.casefold())
    _neutralize_duplicate_javis_ids(result)
    return result
