from __future__ import annotations

import re
import uuid
from pathlib import Path

from .frontmatter import load_markdown, update_frontmatter
from .models import DocumentType, IdentityDecision


GENERATED_ID_RE = re.compile(r"^(?:note|src|mem|file)_[0-9a-f]{12}$")
SAFE_EXISTING_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")


def _prefix_for(document_type: DocumentType | str) -> str:
    value = (
        document_type.value
        if isinstance(document_type, DocumentType)
        else str(document_type or "")
    )
    if value == DocumentType.LIVING_NOTE.value:
        return "note"
    if value == DocumentType.MEMORY.value:
        return "mem"
    if value in {
        DocumentType.REFERENCE_SOURCE.value,
        DocumentType.BINARY_SOURCE.value,
    }:
        return "src"
    return "file"


def new_source_id(document_type: DocumentType | str = DocumentType.UNKNOWN) -> str:
    return f"{_prefix_for(document_type)}_{uuid.uuid4().hex[:12]}"


def valid_existing_id(value: str) -> bool:
    raw = str(value or "").strip()
    return bool(raw and SAFE_EXISTING_ID_RE.match(raw))


def read_javis_id(path: Path | str) -> str:
    fp = Path(path)
    if fp.suffix.lower() != ".md" or not fp.is_file():
        return ""
    value = load_markdown(fp).metadata.get("javis_id")
    raw = str(value or "").strip()
    return raw if valid_existing_id(raw) else ""


def decide_identity(
    path: Path | str,
    *,
    document_type: DocumentType | str = DocumentType.UNKNOWN,
    existing_source_id: str = "",
) -> IdentityDecision:
    existing = str(existing_source_id or "").strip()
    if existing and valid_existing_id(existing):
        return IdentityDecision(existing, source="database", generated=False)

    from_note = read_javis_id(path)
    if from_note:
        return IdentityDecision(from_note, source="frontmatter", generated=False)

    return IdentityDecision(
        new_source_id(document_type),
        source="generated",
        generated=True,
    )


def ensure_javis_id(
    path: Path | str,
    *,
    document_type: DocumentType | str = DocumentType.UNKNOWN,
    existing_source_id: str = "",
    dry_run: bool = True,
) -> IdentityDecision:
    decision = decide_identity(
        path,
        document_type=document_type,
        existing_source_id=existing_source_id,
    )
    fp = Path(path)

    if decision.generated and fp.suffix.lower() == ".md" and fp.is_file():
        update_frontmatter(
            fp,
            updates={"javis_id": decision.source_id},
            dry_run=dry_run,
        )
    return decision
