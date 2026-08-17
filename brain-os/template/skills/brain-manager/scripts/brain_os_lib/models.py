from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class StrEnum(str, Enum):
    """Small Python 3.9-compatible string enum."""

    def __str__(self) -> str:
        return self.value


class DocumentType(StrEnum):
    UNKNOWN = "unknown"
    LIVING_NOTE = "living_note"
    REFERENCE_SOURCE = "reference_source"
    SCRATCH = "scratch"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    FUTURE = "future"
    MEMORY = "memory"
    DERIVED_WIKI = "derived_wiki"
    SYSTEM = "system"
    BINARY_SOURCE = "binary_source"


class ProcessingState(StrEnum):
    DISCOVERED = "discovered"
    INDEXED = "indexed"
    UNCLASSIFIED = "unclassified"
    CLASSIFIED = "classified"
    PENDING_INGEST = "pending_ingest"
    INGESTED = "ingested"
    COMPOUNDED = "compounded"
    STALE = "stale"
    PENDING_REINGEST = "pending_reingest"
    MISSING = "missing"
    IGNORED = "ignored"


class ChangeKind(StrEnum):
    CREATED = "created"
    MODIFIED = "modified"
    MOVED = "moved"
    RENAMED = "renamed"
    DELETED = "deleted"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class FileFingerprint:
    """Content + stat fingerprint for one file.

    `path` is always a POSIX path relative to the Brain root.
    """

    path: str
    size: int
    mtime_ns: int
    sha256: str
    suffix: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BrainFile:
    source_id: str
    path: str
    file_type: str = ""
    document_type: DocumentType = DocumentType.UNKNOWN
    category_id: str = ""
    state: ProcessingState = ProcessingState.DISCOVERED
    origin: str = "brain"
    size: int = 0
    mtime_ns: int = 0
    content_hash: str = ""
    last_seen_hash: str = ""
    last_ingested_hash: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_indexed_at: str = ""
    last_ingested_at: str = ""
    deleted_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["document_type"] = self.document_type.value
        data["state"] = self.state.value
        return data


@dataclass(frozen=True)
class IdentityDecision:
    source_id: str
    source: str
    generated: bool = False


@dataclass(frozen=True)
class FrontmatterUpdateResult:
    changed: bool
    dry_run: bool
    path: str
    before: dict[str, Any]
    after: dict[str, Any]
