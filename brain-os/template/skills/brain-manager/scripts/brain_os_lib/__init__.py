"""Deterministic core for Brain OS V1.

This package intentionally contains no LLM calls. It owns configuration, paths,
hashing, frontmatter, stable identity, the rebuildable SQLite index, filesystem
change detection, incremental text diffing, deterministic document typing,
Stage 5 dry-run taxonomy planning, Stage 6 Markdown import/provenance, the
Stage 7 deterministic Amplenote migration adapter, and Stage 8's deterministic
AI job/output validation + governance routing bridge. Actual model execution
remains owned by Javis.
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

__version__ = "0.7.0"
