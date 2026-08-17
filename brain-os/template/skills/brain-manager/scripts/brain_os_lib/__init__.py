"""Deterministic core for Brain OS V1.

This package intentionally contains no LLM calls. It owns the boring parts:
configuration, paths, hashing, frontmatter, stable identity, the rebuildable
SQLite index, filesystem change detection and incremental text diffing.
"""

from .models import (
    BrainFile,
    ChangeKind,
    DocumentType,
    FileFingerprint,
    ProcessingState,
)

__all__ = [
    "BrainFile",
    "ChangeKind",
    "DocumentType",
    "FileFingerprint",
    "ProcessingState",
]

__version__ = "0.2.0"
