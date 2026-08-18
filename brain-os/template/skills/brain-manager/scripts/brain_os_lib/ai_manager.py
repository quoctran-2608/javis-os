from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from .candidates import make_candidate_id, title_for_path, upsert_candidate
from .config import BrainOSConfig
from .db import BrainIndex, utc_now
from .jobs import enqueue_job, get_job, make_job_id, set_job_status
from .metadata import read_markdown_probe
from .models import BrainFile, DocumentType, ProcessingState
from .taxonomy import TaxonomyRegistry

AI_POLICY_VERSION = 1
AI_OUTPUT_SCHEMA_VERSION = 1

ALLOWED_AI_DOCUMENT_TYPES = {
    DocumentType.UNKNOWN,
    DocumentType.LIVING_NOTE,
    DocumentType.REFERENCE_SOURCE,
    DocumentType.SCRATCH,
    DocumentType.DAILY,
    DocumentType.WEEKLY,
    DocumentType.MONTHLY,
    DocumentType.FUTURE,
}
ALLOWED_ROUTES = {
    "none",
    "index",
    "ingest",
    "incremental_ingest",
    "wiki_candidate",
    "memory_candidate",
}

_ROUTE_BY_TYPE: dict[DocumentType, set[str]] = {
    DocumentType.UNKNOWN: {"none", "index"},
    DocumentType.SCRATCH: {"none", "index"},
    DocumentType.FUTURE: {"none", "index"},
    DocumentType.DAILY: {"none", "index", "ingest", "memory_candidate"},
    DocumentType.WEEKLY: {
        "none", "index", "ingest", "incremental_ingest",
        "wiki_candidate", "memory_candidate",
    },
    DocumentType.MONTHLY: {
        "none", "index", "ingest", "incremental_ingest",
        "wiki_candidate", "memory_candidate",
    },
    DocumentType.LIVING_NOTE: {
        "none", "index", "ingest", "incremental_ingest",
        "wiki_candidate", "memory_candidate",
    },
    DocumentType.REFERENCE_SOURCE: {
        "none", "index", "ingest", "incremental_ingest", "wiki_candidate",
    },
}


class BrainManagerError(RuntimeError):
    pass


@dataclass
class AIQueueReport:
    ok: bool = True
    initialized: bool = True
    scanned_records: int = 0
    queued: int = 0
    reused: int = 0
    skipped_missing: int = 0
    skipped_resolved: int = 0
    skipped_manual: int = 0
    warnings: list[str] = field(default_factory=list)
    jobs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AppliedAIResult:
    job_id: str
    source_id: str
    accepted: bool
    confidence: float
    document_type: str
    category_id: str
    route: str
    next_state: str
    candidate_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ai_policy_id(config: BrainOSConfig) -> str:
    payload = {
        "ai_policy_version": AI_POLICY_VERSION,
        "classification": config.core.get("classification") or {},
        "taxonomy": config.core.get("taxonomy") or {},
        "processing": config.core.get("processing") or {},
        "zones": config.core.get("zones") or {},
        "manual_override": config.core.get("manual_override") or {},
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _list_files(index: BrainIndex) -> list[BrainFile]:
    rows = index._require().execute(
        "SELECT source_id FROM files ORDER BY path"
    ).fetchall()
    result: list[BrainFile] = []
    for row in rows:
        item = index.get_file(str(row["source_id"]))
        if item is not None:
            result.append(item)
    return result


def _manual_mode(item: BrainFile) -> str:
    classification = (item.metadata or {}).get("classification") or {}
    if isinstance(classification, dict):
        return str(classification.get("manual_mode") or "auto").strip().casefold()
    return "auto"


def _needs_ai_review(item: BrainFile) -> bool:
    metadata = item.metadata or {}
    classification = metadata.get("classification") or {}
    taxonomy = metadata.get("taxonomy") or {}
    classification_need = bool(
        isinstance(classification, dict)
        and classification
        and classification.get("needs_ai", False)
    )
    taxonomy_need = bool(
        isinstance(taxonomy, dict)
        and taxonomy
        and taxonomy.get("applicable", False)
        and not taxonomy.get("accepted", False)
    )
    return classification_need or taxonomy_need


def _bounded_evidence(config: BrainOSConfig, item: BrainFile) -> dict[str, Any]:
    taxonomy_cfg = config.core.get("taxonomy") or {}
    classification_cfg = config.core.get("classification") or {}
    max_bytes = min(int(taxonomy_cfg.get("max_text_probe_bytes", 131072)), 65536)
    max_frontmatter = int(classification_cfg.get("max_frontmatter_bytes", 65536))
    probe = read_markdown_probe(
        config.brain_root / item.path,
        max_frontmatter_bytes=max_frontmatter,
        max_text_probe_bytes=max_bytes,
    )
    excerpt = probe.body_excerpt
    if len(excerpt) > 16000:
        excerpt = excerpt[:16000]
    return {
        "title": probe.title,
        "headings": list(probe.headings[:24]),
        "raw_tags": list(probe.raw_tags),
        "body_excerpt": excerpt,
        "truncated": bool(probe.truncated or len(probe.body_excerpt) > len(excerpt)),
        "warnings": list(probe.warnings),
    }


def _registry_constraints(registry: TaxonomyRegistry) -> dict[str, Any]:
    categories = [
        {"id": c.id, "scope": c.scope, "label": c.label, "path": c.path}
        for c in sorted(
            registry.categories.values(), key=lambda value: (value.scope, value.path)
        )
    ]
    return {
        "allowed_document_types": sorted(v.value for v in ALLOWED_AI_DOCUMENT_TYPES),
        "allowed_routes": sorted(ALLOWED_ROUTES),
        "categories": categories,
        "canonical_tags": sorted(registry.tags),
    }


def _job_payload(
    config: BrainOSConfig,
    item: BrainFile,
    *,
    registry: TaxonomyRegistry,
    policy_id: str,
) -> dict[str, Any]:
    metadata = item.metadata or {}
    return {
        "schema_version": AI_OUTPUT_SCHEMA_VERSION,
        "ai_policy_version": AI_POLICY_VERSION,
        "policy_id": policy_id,
        "source": {
            "source_id": item.source_id,
            "path": item.path,
            "content_hash": item.content_hash,
            "document_type": item.document_type.value,
            "category_id": item.category_id,
            "state": item.state.value,
            "last_ingested_hash": item.last_ingested_hash,
        },
        "signals": {
            "classification": metadata.get("classification") or {},
            "taxonomy": metadata.get("taxonomy") or {},
        },
        "evidence": _bounded_evidence(config, item),
        "constraints": _registry_constraints(registry),
        "contract": {
            "writes_user_files": False,
            "executes_javis_ingest": False,
            "writes_wiki": False,
            "writes_memory": False,
            "candidate_first": True,
        },
    }


def queue_ai_jobs(
    config: BrainOSConfig,
    *,
    limit: int = 100,
    force: bool = False,
) -> AIQueueReport:
    if not config.db_path.is_file():
        return AIQueueReport(initialized=False)
    report = AIQueueReport()
    registry = TaxonomyRegistry.from_config(config)
    policy_id = ai_policy_id(config)
    bounded_limit = max(1, min(int(limit), 500))
    with BrainIndex(config.db_path) as index:
        for item in _list_files(index):
            report.scanned_records += 1
            if item.state == ProcessingState.MISSING:
                report.skipped_missing += 1
                continue
            if not (config.brain_root / item.path).is_file():
                report.skipped_missing += 1
                report.warnings.append(f"{item.path}: file không tồn tại; bỏ AI queue")
                continue
            manual = _manual_mode(item)
            if manual in {"ignore", "index"}:
                report.skipped_manual += 1
                continue
            if not _needs_ai_review(item):
                report.skipped_resolved += 1
                continue
            job_id = make_job_id(item.source_id, item.content_hash, policy_id)
            payload = _job_payload(config, item, registry=registry, policy_id=policy_id)
            job, created = enqueue_job(
                index,
                job_id=job_id,
                source_id=item.source_id,
                payload=payload,
                priority=100,
                force=force,
            )
            if created:
                report.queued += 1
            else:
                report.reused += 1
            if len(report.jobs) < bounded_limit:
                report.jobs.append(job)
            if report.queued + report.reused >= bounded_limit:
                break
    return report


def _scope_for_type(document_type: DocumentType) -> str:
    if document_type == DocumentType.LIVING_NOTE:
        return "living_notes"
    if document_type == DocumentType.REFERENCE_SOURCE:
        return "knowledge"
    return ""


def _parse_document_type(value: Any) -> DocumentType:
    try:
        document_type = DocumentType(str(value or "").strip())
    except ValueError as exc:
        raise BrainManagerError(f"AI document_type không hợp lệ: {value!r}") from exc
    if document_type not in ALLOWED_AI_DOCUMENT_TYPES:
        raise BrainManagerError(
            f"AI không được gán document_type đặc quyền: {document_type.value}"
        )
    return document_type


def _validate_shape(result: dict[str, Any]) -> dict[str, Any]:
    required = {"schema_version", "job_id", "source_id", "content_hash", "decision"}
    extra = set(result) - required
    missing = required - set(result)
    if missing or extra:
        raise BrainManagerError(
            f"AI output top-level sai schema; missing={sorted(missing)} extra={sorted(extra)}"
        )
    if int(result.get("schema_version") or 0) != AI_OUTPUT_SCHEMA_VERSION:
        raise BrainManagerError(
            f"AI output schema_version phải là {AI_OUTPUT_SCHEMA_VERSION}"
        )
    decision = result.get("decision")
    if not isinstance(decision, dict):
        raise BrainManagerError("AI output decision phải là object")
    required_decision = {
        "document_type", "category_id", "canonical_tags",
        "route", "confidence", "rationale",
    }
    extra_decision = set(decision) - required_decision
    missing_decision = required_decision - set(decision)
    if missing_decision or extra_decision:
        raise BrainManagerError(
            "AI decision sai schema; "
            f"missing={sorted(missing_decision)} extra={sorted(extra_decision)}"
        )
    return decision


def validate_ai_result(
    config: BrainOSConfig,
    result: dict[str, Any],
    *,
    index: BrainIndex,
) -> tuple[dict[str, Any], dict[str, Any], BrainFile, DocumentType, Any]:
    decision = _validate_shape(result)
    job_id = str(result.get("job_id") or "").strip()
    source_id = str(result.get("source_id") or "").strip()
    content_hash = str(result.get("content_hash") or "").strip()
    if not job_id or not source_id or len(content_hash) != 64:
        raise BrainManagerError("AI output thiếu job/source/content hash hợp lệ")
    job = get_job(index, job_id)
    if job is None:
        raise BrainManagerError(f"Không tìm thấy AI job: {job_id}")
    if job["status"] not in {"pending", "processing"}:
        raise BrainManagerError(
            f"AI job {job_id} không còn apply được; status={job['status']}"
        )
    payload = job.get("payload") or {}
    source = payload.get("source") or {}
    if not isinstance(source, dict):
        raise BrainManagerError(f"AI job payload hỏng: {job_id}")
    item = index.get_file(source_id)
    if item is None:
        raise BrainManagerError(f"Không tìm thấy source_id trong index: {source_id}")
    if item.state == ProcessingState.MISSING:
        raise BrainManagerError(f"Source đã missing; từ chối AI result: {item.path}")
    if str(job["source_id"]) != source_id:
        raise BrainManagerError("AI output source_id không khớp job")
    expected_hash = str(source.get("content_hash") or "")
    if (
        expected_hash != content_hash
        or item.content_hash != content_hash
        or str(source.get("source_id") or "") != source_id
    ):
        raise BrainManagerError(
            "AI result stale: source/content hash đã thay đổi; phải queue job mới"
        )
    document_type = _parse_document_type(decision.get("document_type"))
    if item.document_type != DocumentType.UNKNOWN and document_type != item.document_type:
        raise BrainManagerError(
            "AI không được override deterministic document_type "
            f"{item.document_type.value}->{document_type.value}"
        )
    route = str(decision.get("route") or "").strip()
    if route not in ALLOWED_ROUTES:
        raise BrainManagerError(f"AI route không hợp lệ: {route!r}")
    if route not in _ROUTE_BY_TYPE.get(document_type, {"none", "index"}):
        raise BrainManagerError(
            f"Route {route!r} không được phép cho {document_type.value}"
        )
    manual = _manual_mode(item)
    if manual in {"ignore", "index"} and route not in {"none", "index"}:
        raise BrainManagerError(f"Manual javis:{manual} chặn AI route {route!r}")
    if manual == "wiki" and route not in {"wiki_candidate", "index", "none"}:
        raise BrainManagerError(
            f"Manual javis:wiki chỉ cho wiki_candidate/index/none, không {route!r}"
        )
    raw_confidence = decision.get("confidence")
    if isinstance(raw_confidence, bool) or not isinstance(raw_confidence, (int, float)):
        raise BrainManagerError("AI confidence phải là số 0..1")
    confidence = float(raw_confidence)
    if not 0.0 <= confidence <= 1.0:
        raise BrainManagerError("AI confidence phải trong 0..1")
    rationale = str(decision.get("rationale") or "").strip()
    if not rationale or len(rationale) > 1000:
        raise BrainManagerError("AI rationale phải có nội dung và <= 1000 ký tự")
    registry = TaxonomyRegistry.from_config(config)
    raw_tags = decision.get("canonical_tags")
    if not isinstance(raw_tags, list):
        raise BrainManagerError("AI canonical_tags phải là list")
    max_tags = int((config.core.get("tags") or {}).get("max_per_note", 6))
    if len(raw_tags) > max_tags:
        raise BrainManagerError(f"AI canonical_tags vượt max_per_note={max_tags}")
    canonical_tags: list[str] = []
    seen: set[str] = set()
    for value in raw_tags:
        tag = str(value or "").strip().lstrip("#")
        if not tag or tag not in registry.tags:
            raise BrainManagerError(
                f"AI chỉ được chọn canonical tag đã đăng ký: {value!r}"
            )
        if tag in seen:
            raise BrainManagerError(f"AI canonical_tags bị trùng: {tag}")
        seen.add(tag)
        canonical_tags.append(tag)
    category_id = str(decision.get("category_id") or "").strip()
    category = None
    scope = _scope_for_type(document_type)
    if category_id:
        if not scope:
            raise BrainManagerError(
                f"{document_type.value} không được gán folder category bởi AI"
            )
        category = registry.resolve_category(scope, category_id)
        if category is None or category.id != category_id:
            raise BrainManagerError(
                f"AI category phải là id đã đăng ký trong scope {scope}: {category_id!r}"
            )
        if item.category_id and item.category_id != category_id:
            raise BrainManagerError(
                "AI không được override deterministic category "
                f"{item.category_id}->{category_id}"
            )
    normalized = dict(decision)
    normalized["document_type"] = document_type.value
    normalized["category_id"] = category_id
    normalized["canonical_tags"] = canonical_tags
    normalized["route"] = route
    normalized["confidence"] = round(confidence, 4)
    normalized["rationale"] = rationale
    return normalized, job, item, document_type, category


def _next_state(item: BrainFile, document_type: DocumentType, route: str) -> ProcessingState:
    if route in {"ingest", "incremental_ingest"}:
        return (
            ProcessingState.PENDING_REINGEST
            if item.last_ingested_hash
            else ProcessingState.PENDING_INGEST
        )
    if (
        document_type != DocumentType.UNKNOWN
        and item.state in {
            ProcessingState.DISCOVERED,
            ProcessingState.UNCLASSIFIED,
            ProcessingState.CLASSIFIED,
        }
    ):
        return ProcessingState.CLASSIFIED
    return item.state


def apply_ai_result(config: BrainOSConfig, result: dict[str, Any]) -> AppliedAIResult:
    if not config.db_path.is_file():
        raise BrainManagerError("Brain index chưa được khởi tạo")
    with BrainIndex(config.db_path) as index:
        job_id = str(result.get("job_id") or "").strip()
        try:
            decision, job, item, document_type, category = validate_ai_result(
                config, result, index=index,
            )
        except Exception as exc:
            if job_id and get_job(index, job_id) is not None:
                set_job_status(
                    index, job_id, status="failed",
                    last_error=str(exc), increment_attempt=True,
                )
            raise
        threshold = float(
            (config.core.get("classification") or {}).get("accept_confidence", 0.80)
        )
        accepted = float(decision["confidence"]) >= threshold
        candidate_id = ""
        next_state = item.state
        metadata = dict(item.metadata or {})
        metadata["brain_manager"] = {
            "schema_version": AI_OUTPUT_SCHEMA_VERSION,
            "ai_policy_version": AI_POLICY_VERSION,
            "policy_id": str((job.get("payload") or {}).get("policy_id") or ""),
            "job_id": job_id,
            "content_hash": item.content_hash,
            "decision": decision,
            "accepted": accepted,
            "applied_at": utc_now(),
            "writes_user_files": False,
            "executes_javis_ingest": False,
            "writes_wiki": False,
            "writes_memory": False,
        }
        if accepted:
            next_state = _next_state(item, document_type, str(decision["route"]))
            committed_category = (
                str(decision["category_id"])
                if decision["category_id"] else item.category_id
            )
            index.upsert_file(
                replace(
                    item,
                    document_type=(
                        document_type
                        if document_type != DocumentType.UNKNOWN
                        else item.document_type
                    ),
                    category_id=committed_category,
                    state=next_state,
                    metadata=metadata,
                    updated_at="",
                )
            )
            route = str(decision["route"])
            kind = "wiki" if route == "wiki_candidate" else (
                "memory" if route == "memory_candidate" else ""
            )
            if kind:
                candidate_id = make_candidate_id(
                    item.source_id, kind, item.content_hash, route
                )
                upsert_candidate(
                    index,
                    candidate_id=candidate_id,
                    source_id=item.source_id,
                    kind=kind,
                    title=title_for_path(item.path),
                    confidence=float(decision["confidence"]),
                    payload={
                        "job_id": job_id,
                        "path": item.path,
                        "content_hash": item.content_hash,
                        "decision": decision,
                        "provenance": {
                            "source_id": item.source_id,
                            "source_path": item.path,
                        },
                    },
                )
        else:
            index.upsert_file(replace(item, metadata=metadata, updated_at=""))
            candidate_id = make_candidate_id(
                item.source_id, "ai_review", item.content_hash, str(decision["route"])
            )
            upsert_candidate(
                index,
                candidate_id=candidate_id,
                source_id=item.source_id,
                kind="ai_review",
                title=title_for_path(item.path),
                confidence=float(decision["confidence"]),
                payload={
                    "job_id": job_id,
                    "path": item.path,
                    "content_hash": item.content_hash,
                    "decision": decision,
                    "reason": "below_accept_confidence",
                },
            )
        set_job_status(index, job_id, status="completed", last_error="")
        latest = index.get_file(item.source_id)
        return AppliedAIResult(
            job_id=job_id,
            source_id=item.source_id,
            accepted=accepted,
            confidence=float(decision["confidence"]),
            document_type=(
                latest.document_type.value
                if latest is not None else item.document_type.value
            ),
            category_id=(latest.category_id if latest is not None else item.category_id),
            route=str(decision["route"]),
            next_state=(latest.state.value if latest is not None else next_state.value),
            candidate_id=candidate_id,
        )
