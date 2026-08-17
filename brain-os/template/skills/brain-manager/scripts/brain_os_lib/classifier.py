from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from .config import BrainOSConfig
from .db import BrainIndex, utc_now
from .frontmatter import FrontmatterError, load_markdown
from .models import BrainFile, DocumentType, ProcessingState
from .paths import BrainPaths


CLASSIFIER_VERSION = 1


_TYPE_ALIASES: dict[str, DocumentType] = {
    "living-note": DocumentType.LIVING_NOTE,
    "living_note": DocumentType.LIVING_NOTE,
    "livingnote": DocumentType.LIVING_NOTE,
    "reference-source": DocumentType.REFERENCE_SOURCE,
    "reference_source": DocumentType.REFERENCE_SOURCE,
    "referencesource": DocumentType.REFERENCE_SOURCE,
    "source": DocumentType.REFERENCE_SOURCE,
    "scratch": DocumentType.SCRATCH,
    "scratchpad": DocumentType.SCRATCH,
    "daily": DocumentType.DAILY,
    "daily-note": DocumentType.DAILY,
    "daily_note": DocumentType.DAILY,
    "weekly": DocumentType.WEEKLY,
    "weekly-note": DocumentType.WEEKLY,
    "weekly_note": DocumentType.WEEKLY,
    "monthly": DocumentType.MONTHLY,
    "monthly-note": DocumentType.MONTHLY,
    "monthly_note": DocumentType.MONTHLY,
    "future": DocumentType.FUTURE,
    "future-note": DocumentType.FUTURE,
    "future_note": DocumentType.FUTURE,
    "memory": DocumentType.MEMORY,
    "derived-wiki": DocumentType.DERIVED_WIKI,
    "derived_wiki": DocumentType.DERIVED_WIKI,
    "wiki": DocumentType.DERIVED_WIKI,
    "system": DocumentType.SYSTEM,
    "binary-source": DocumentType.BINARY_SOURCE,
    "binary_source": DocumentType.BINARY_SOURCE,
}


@dataclass(frozen=True)
class ClassificationDecision:
    proposed_type: DocumentType
    document_type: DocumentType
    confidence: float
    accepted: bool
    needs_ai: bool
    status: str
    reason_codes: tuple[str, ...] = ()
    manual_mode: str = "auto"
    explicit_type_field: str = ""
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["proposed_type"] = self.proposed_type.value
        data["document_type"] = self.document_type.value
        data["reason_codes"] = list(self.reason_codes)
        data["warnings"] = list(self.warnings)
        return data


@dataclass
class ClassificationReport:
    ok: bool = True
    initialized: bool = True
    scanned_records: int = 0
    classified: int = 0
    needs_ai: int = 0
    unknown: int = 0
    cached: int = 0
    missing: int = 0
    path_filtered_out: int = 0
    type_counts: dict[str, int] = field(default_factory=dict)
    proposed_counts: dict[str, int] = field(default_factory=dict)
    needs_ai_paths: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().casefold().replace(" ", "-")


def _ascii_token(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")


def _type_from_value(value: Any) -> DocumentType | None:
    token = _normalize_token(value)
    if not token:
        return None
    if token in _TYPE_ALIASES:
        return _TYPE_ALIASES[token]
    try:
        return DocumentType(token)
    except ValueError:
        return None


def _classification_options(config: BrainOSConfig) -> dict[str, Any]:
    raw = config.core.get("classification") or {}
    manual = config.core.get("manual_override") or {}
    allowed_modes = manual.get("allowed_values") or ["auto", "ignore", "index", "ingest", "wiki"]
    return {
        "accept_confidence": float(raw.get("accept_confidence", 0.80)),
        "candidate_confidence": float(raw.get("candidate_confidence", 0.55)),
        "explicit_type_field": str(raw.get("explicit_type_field", "javis_type") or "javis_type"),
        "fallback_type_field": str(raw.get("fallback_type_field", "type") or "type"),
        "manual_mode_field": str(manual.get("field", "javis") or "javis"),
        "allowed_modes": tuple(str(v) for v in allowed_modes),
    }


def classification_policy_id(config: BrainOSConfig) -> str:
    payload = {
        "classifier_version": CLASSIFIER_VERSION,
        "classification": config.core.get("classification") or {},
        "manual_override": config.core.get("manual_override") or {},
        "paths": config.core.get("paths") or {},
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _zone_document_type(config: BrainOSConfig, path: str) -> tuple[DocumentType | None, str]:
    zone = BrainPaths(config).zone_for(path)
    if not zone:
        return None, ""

    paths = config.core.get("paths") or {}
    mapping = {
        str(paths.get("dashboard", "")): DocumentType.SYSTEM,
        str(paths.get("daily", "")): DocumentType.DAILY,
        str(paths.get("weekly", "")): DocumentType.WEEKLY,
        str(paths.get("monthly", "")): DocumentType.MONTHLY,
        str(paths.get("future", "")): DocumentType.FUTURE,
        str(paths.get("notes", "")): DocumentType.LIVING_NOTE,
        str(paths.get("sources", "")): DocumentType.REFERENCE_SOURCE,
        str(paths.get("library", "")): DocumentType.REFERENCE_SOURCE,
        str(paths.get("wiki", "")): DocumentType.DERIVED_WIKI,
        str(paths.get("memory", "")): DocumentType.MEMORY,
    }
    return mapping.get(zone), zone


def _path_hint(path: str) -> tuple[DocumentType | None, float, str]:
    parts = [_ascii_token(part) for part in PurePosixPath(path).parts[:-1]]
    groups: tuple[tuple[set[str], DocumentType, str], ...] = (
        ({"scratch", "scratchpad", "quick-notes", "quick-note", "tmp", "temp"}, DocumentType.SCRATCH, "scratch"),
        ({"daily", "daily-log", "daily-notes", "nhat-ky-ngay"}, DocumentType.DAILY, "daily"),
        ({"weekly", "weekly-log", "weekly-notes", "tuan"}, DocumentType.WEEKLY, "weekly"),
        ({"monthly", "monthly-log", "monthly-notes", "thang"}, DocumentType.MONTHLY, "monthly"),
        ({"future", "future-log", "someday", "later"}, DocumentType.FUTURE, "future"),
        ({"sources", "source", "references", "reference"}, DocumentType.REFERENCE_SOURCE, "reference"),
        ({"memory", "memories"}, DocumentType.MEMORY, "memory"),
    )
    for names, doc_type, label in groups:
        if any(part in names for part in parts):
            return doc_type, 0.90, f"path_hint:{label}"
    return None, 0.0, ""


def _filename_hint(path: str) -> tuple[DocumentType | None, float, str]:
    stem = PurePosixPath(path).stem
    normalized = _ascii_token(stem)

    if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])-([0-2]\d|3[01])", stem):
        return DocumentType.DAILY, 0.68, "filename_hint:iso_date"
    if re.fullmatch(r"\d{4}-[wW](0[1-9]|[1-4]\d|5[0-3])", stem):
        return DocumentType.WEEKLY, 0.68, "filename_hint:iso_week"
    if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", stem):
        return DocumentType.MONTHLY, 0.65, "filename_hint:year_month"
    if normalized in {"scratch", "scratchpad", "quick-note", "untitled", "temp", "tmp"}:
        return DocumentType.SCRATCH, 0.68, "filename_hint:scratch"
    return None, 0.0, ""


def _frontmatter_signals(
    config: BrainOSConfig,
    path: Path,
) -> tuple[DocumentType | None, str, str, list[str]]:
    opts = _classification_options(config)
    warnings: list[str] = []
    try:
        doc = load_markdown(path)
    except (FrontmatterError, OSError) as exc:
        return None, "auto", "", [f"frontmatter_unreadable:{type(exc).__name__}:{exc}"]

    metadata = doc.metadata or {}
    explicit_field = opts["explicit_type_field"]
    fallback_field = opts["fallback_type_field"]

    explicit_type: DocumentType | None = None
    used_field = ""
    if explicit_field in metadata:
        explicit_type = _type_from_value(metadata.get(explicit_field))
        used_field = explicit_field
        if explicit_type is None:
            warnings.append(
                f"invalid_explicit_type:{explicit_field}={metadata.get(explicit_field)!r}"
            )
    if explicit_type is None and fallback_field != explicit_field and fallback_field in metadata:
        fallback = _type_from_value(metadata.get(fallback_field))
        if fallback is not None:
            explicit_type = fallback
            used_field = fallback_field

    if (
        explicit_type is not None
        and explicit_field in metadata
        and fallback_field != explicit_field
        and fallback_field in metadata
    ):
        fallback = _type_from_value(metadata.get(fallback_field))
        if fallback is not None and fallback != explicit_type:
            warnings.append(
                f"conflicting_type_fields:{explicit_field}={explicit_type.value},"
                f"{fallback_field}={fallback.value}; dùng {explicit_field}"
            )

    manual_mode = "auto"
    manual_field = opts["manual_mode_field"]
    if manual_field in metadata:
        requested = str(metadata.get(manual_field) or "").strip().casefold()
        if requested in opts["allowed_modes"]:
            manual_mode = requested
        elif requested:
            warnings.append(f"invalid_manual_mode:{manual_field}={requested!r}")

    return explicit_type, manual_mode, used_field, warnings


def classify_document(config: BrainOSConfig, item: BrainFile) -> ClassificationDecision:
    """Classify one indexed Markdown record without modifying the source file.

    Strong deterministic evidence is accepted. Weak evidence remains only a proposal
    and is marked `needs_ai`; it is never committed as a document type by itself.
    """

    opts = _classification_options(config)
    absolute = config.brain_root / item.path
    explicit_type, manual_mode, used_field, warnings = _frontmatter_signals(
        config, absolute
    )

    proposed = DocumentType.UNKNOWN
    confidence = 0.0
    reasons: list[str] = []

    if explicit_type is not None:
        proposed = explicit_type
        confidence = 1.0
        reasons.append(f"frontmatter:{used_field}")
        zone_type, zone = _zone_document_type(config, item.path)
        if zone_type is not None and zone_type != explicit_type:
            warnings.append(
                f"explicit_type_overrides_zone:{zone}:{zone_type.value}->{explicit_type.value}"
            )
    else:
        zone_type, zone = _zone_document_type(config, item.path)
        if zone_type is not None:
            proposed = zone_type
            confidence = 0.98
            reasons.append(f"zone:{zone}")
        else:
            hinted, hinted_confidence, hinted_reason = _path_hint(item.path)
            if hinted is not None:
                proposed = hinted
                confidence = hinted_confidence
                reasons.append(hinted_reason)
            else:
                hinted, hinted_confidence, hinted_reason = _filename_hint(item.path)
                if hinted is not None:
                    proposed = hinted
                    confidence = hinted_confidence
                    reasons.append(hinted_reason)

    accepted = (
        proposed != DocumentType.UNKNOWN
        and confidence >= float(opts["accept_confidence"])
    )
    candidate = (
        proposed != DocumentType.UNKNOWN
        and confidence >= float(opts["candidate_confidence"])
    )

    # `ignore`/`index` are explicit processing choices. The semantic type may stay
    # unknown, but Brain OS does not need to spend AI on classification to honor them.
    no_ai_manual = manual_mode in {"ignore", "index"}
    needs_ai = not accepted and not no_ai_manual

    if accepted:
        status = "accepted"
    elif candidate and needs_ai:
        status = "needs_ai_candidate"
    elif needs_ai:
        status = "needs_ai_unknown"
    else:
        status = "manual_no_ai"

    committed = proposed if accepted else DocumentType.UNKNOWN
    if not reasons:
        reasons.append("no_deterministic_signal")

    return ClassificationDecision(
        proposed_type=proposed,
        document_type=committed,
        confidence=round(float(confidence), 4),
        accepted=accepted,
        needs_ai=needs_ai,
        status=status,
        reason_codes=tuple(reasons),
        manual_mode=manual_mode,
        explicit_type_field=used_field,
        warnings=tuple(warnings),
    )


def _state_after_classification(
    current: ProcessingState,
    *,
    accepted: bool,
) -> ProcessingState:
    if current == ProcessingState.MISSING:
        return current
    if current in {ProcessingState.DISCOVERED, ProcessingState.UNCLASSIFIED, ProcessingState.CLASSIFIED}:
        return ProcessingState.CLASSIFIED if accepted else ProcessingState.UNCLASSIFIED
    return current


def _list_files(index: BrainIndex) -> list[BrainFile]:
    if index.conn is None:
        return []
    rows = index.conn.execute("SELECT source_id FROM files ORDER BY path").fetchall()
    result: list[BrainFile] = []
    for row in rows:
        item = index.get_file(str(row["source_id"]))
        if item is not None:
            result.append(item)
    return result


def _classification_cache_valid(
    item: BrainFile,
    *,
    policy_id: str,
) -> bool:
    raw = (item.metadata or {}).get("classification") or {}
    return bool(
        isinstance(raw, dict)
        and int(raw.get("classifier_version", 0) or 0) == CLASSIFIER_VERSION
        and str(raw.get("policy_id", "")) == policy_id
        and str(raw.get("content_hash", "")) == item.content_hash
        and str(raw.get("path", "")) == item.path
    )


def classify_brain(
    config: BrainOSConfig,
    *,
    force: bool = False,
    paths: set[str] | None = None,
) -> ClassificationReport:
    """Classify indexed files and persist only derived classification state in SQLite."""

    if not config.db_path.is_file():
        return ClassificationReport(initialized=False)

    normalized_paths = {str(PurePosixPath(p.replace("\\", "/"))) for p in (paths or set())}
    policy_id = classification_policy_id(config)
    report = ClassificationReport()

    with BrainIndex(config.db_path) as index:
        for item in _list_files(index):
            if normalized_paths and item.path not in normalized_paths:
                report.path_filtered_out += 1
                continue

            report.scanned_records += 1
            if item.state == ProcessingState.MISSING:
                report.missing += 1
                continue
            if not (config.brain_root / item.path).is_file():
                report.missing += 1
                report.warnings.append(f"{item.path}: file không tồn tại; bỏ classification")
                continue
            if not force and _classification_cache_valid(item, policy_id=policy_id):
                report.cached += 1
                continue

            decision = classify_document(config, item)
            metadata = dict(item.metadata or {})
            payload = decision.to_dict()
            payload.update(
                {
                    "classifier_version": CLASSIFIER_VERSION,
                    "policy_id": policy_id,
                    "content_hash": item.content_hash,
                    "path": item.path,
                    "classified_at": utc_now(),
                }
            )
            metadata["classification"] = payload

            next_state = _state_after_classification(
                item.state,
                accepted=decision.accepted,
            )
            index.upsert_file(
                replace(
                    item,
                    document_type=decision.document_type,
                    state=next_state,
                    metadata=metadata,
                    updated_at="",
                )
            )

            if decision.accepted:
                report.classified += 1
            if decision.needs_ai:
                report.needs_ai += 1
                if len(report.needs_ai_paths) < 50:
                    report.needs_ai_paths.append(item.path)
            if decision.document_type == DocumentType.UNKNOWN:
                report.unknown += 1

            committed_key = decision.document_type.value
            proposed_key = decision.proposed_type.value
            report.type_counts[committed_key] = report.type_counts.get(committed_key, 0) + 1
            report.proposed_counts[proposed_key] = report.proposed_counts.get(proposed_key, 0) + 1
            report.warnings.extend(f"{item.path}: {warning}" for warning in decision.warnings)

    report.type_counts = dict(sorted(report.type_counts.items()))
    report.proposed_counts = dict(sorted(report.proposed_counts.items()))
    return report


def list_classifications(
    config: BrainOSConfig,
    *,
    needs_ai_only: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not config.db_path.is_file():
        return []

    rows: list[dict[str, Any]] = []
    with BrainIndex(config.db_path) as index:
        for item in _list_files(index):
            raw = (item.metadata or {}).get("classification") or {}
            if not isinstance(raw, dict) or not raw:
                continue
            if needs_ai_only and not bool(raw.get("needs_ai", False)):
                continue
            rows.append(
                {
                    "source_id": item.source_id,
                    "path": item.path,
                    "document_type": item.document_type.value,
                    "state": item.state.value,
                    "classification": raw,
                }
            )
            if len(rows) >= max(1, min(int(limit), 500)):
                break
    return rows
