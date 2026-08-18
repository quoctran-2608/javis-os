"""Deterministic core for Brain OS V1.

This package intentionally contains no LLM calls. It owns configuration, paths,
hashing, frontmatter, stable identity, the rebuildable SQLite index, filesystem
change detection, incremental text diffing, deterministic document typing,
taxonomy planning, import/provenance, Amplenote migration, Stage 8 AI
governance/validation, Stage 9 deterministic Brain Watch orchestration, and
Stage 10 document normalization. Actual model execution, INGEST, Wiki, Memory,
Knowledge Graph, and scheduling remain owned by Javis.
"""

from .models import (
    BrainFile,
    ChangeKind,
    DocumentType,
    FileFingerprint,
    ProcessingState,
)

__all__ = ["BrainFile", "ChangeKind", "DocumentType", "FileFingerprint", "ProcessingState"]
__version__ = "0.8.0"
