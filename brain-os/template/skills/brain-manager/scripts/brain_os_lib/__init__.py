"""Deterministic core for Brain OS V1.

This package intentionally contains no LLM calls. It owns configuration, paths,
hashing, frontmatter, stable identity, the rebuildable SQLite index, filesystem
change detection, incremental text diffing, deterministic document typing and
Stage 5 dry-run folder/tag taxonomy planning.
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

__version__ = "0.4.0"
