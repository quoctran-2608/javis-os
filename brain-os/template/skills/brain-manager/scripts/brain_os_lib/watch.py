from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .ai_manager import (
    _job_payload,
    _manual_mode,
    _needs_ai_review,
    ai_policy_id,
)
from .classifier import classify_brain
from .config import BrainOSConfig
from .db import BrainIndex, utc_now
from .jobs import enqueue_job, get_job, list_jobs, make_job_id, set_job_status
from .models import ProcessingState
from .reconcile import reconcile_brain
from .taxonomy import TaxonomyRegistry, plan_brain_taxonomy

WATCH_VERSION = 1
MAX_EVENTS_PER_CYCLE = 5000
MAX_JOB_RETRIES = 3
MIN_PROCESSING_TIMEOUT_SECONDS = 30 * 60


class BrainWatchError(RuntimeError):
    pass


@dataclass
class BrainWatchReport:
    ok: bool = True
    watch_version: int = WATCH_VERSION
    full_hash: bool = False
    locked: bool = False
    stale_lock_recovered: bool = False
    changes_detected: int = 0
    unhandled_events: int = 0
    handled_events: int = 0
    events_remaining: int = 0
    affected_paths: list[str] = field(default_factory=list)
    scan: dict[str, Any] = field(default_factory=dict)
    classification: dict[str, Any] = field(default_factory=dict)
    taxonomy: dict[str, Any] = field(default_factory=dict)
    ai_queue: dict[str, Any] = field(default_factory=dict)
    handoff_jobs: list[dict[str, Any]] = field(default_factory=list)
    active_processing_jobs: int = 0
    should_stop: bool = True
    stop_reason: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _watch_options(config: BrainOSConfig) -> dict[str, int]:
    raw = config.core.get("watch") or {}
    interval_min = max(1, int(raw.get("interval_min", 5) or 5))
    max_ai_jobs = max(1, min(int(raw.get("max_ai_jobs_per_cycle", 2) or 2), 20))
    sparse_minutes = max(1, int(raw.get("sparse_reconcile_minutes", 60) or 60))
    return {
        "interval_min": interval_min,
        "max_ai_jobs": max_ai_jobs,
        "sparse_reconcile_minutes": sparse_minutes,
        "processing_timeout_seconds": max(
            MIN_PROCESSING_TIMEOUT_SECONDS,
            interval_min * 60 * 4,
        ),
        "lock_timeout_seconds": max(
            MIN_PROCESSING_TIMEOUT_SECONDS,
            interval_min * 60 * 4,
        ),
    }


def _parse_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_seconds(value: str) -> float | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())


def _should_full_hash(config: BrainOSConfig, *, force: bool | None) -> bool:
    if force is not None:
        return bool(force)
    if not config.db_path.is_file():
        return True
    sparse_minutes = _watch_options(config)["sparse_reconcile_minutes"]
    with BrainIndex(config.db_path) as index:
        last = index.get_meta("last_full_reconcile_at", "")
    age = _age_seconds(last)
    return age is None or age >= sparse_minutes * 60


def _lock_path(config: BrainOSConfig) -> Path:
    return config.db_path.parent / "brain-watch.lock"


@contextmanager
def _watch_lock(config: BrainOSConfig) -> Iterator[bool]:
    """Single-cycle lock with fail-safe stale recovery.

    The lock covers only the deterministic cycle/claim phase. Claimed AI jobs are
    marked `processing`, so a later scheduled cycle will not hand them off twice.
    """

    path = _lock_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    timeout = _watch_options(config)["lock_timeout_seconds"]
    recovered = False

    for _ in range(2):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                age = max(0.0, time.time() - path.stat().st_mtime)
            except OSError:
                age = 0.0
            if age < timeout:
                raise BrainWatchError("brain_watch_cycle_locked")
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise BrainWatchError(
                    f"Không dọn được stale Brain Watch lock: {path}: {exc}"
                ) from exc
            recovered = True
            continue
        else:
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                    json.dump(
                        {"pid": os.getpid(), "created_at": utc_now()},
                        fh,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    fh.write("\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                yield recovered
            finally:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            return

    raise BrainWatchError("Không acquire được Brain Watch lock sau stale recovery")


def _unhandled_events(index: BrainIndex, *, limit: int = MAX_EVENTS_PER_CYCLE) -> tuple[list[dict[str, Any]], int]:
    conn = index._require()
    total = int(
        conn.execute("SELECT COUNT(*) FROM events WHERE handled_at='' ").fetchone()[0]
    )
    rows = conn.execute(
        """SELECT event_id, source_id, event_type, path, old_path, payload_json
           FROM events WHERE handled_at=''
           ORDER BY event_id ASC LIMIT ?""",
        (max(1, min(int(limit), MAX_EVENTS_PER_CYCLE)),),
    ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        events.append(
            {
                "event_id": int(row["event_id"]),
                "source_id": str(row["source_id"]),
                "event_type": str(row["event_type"]),
                "path": str(row["path"]),
                "old_path": str(row["old_path"]),
                "payload": payload if isinstance(payload, dict) else {},
            }
        )
    return events, total


def _mark_events_handled(index: BrainIndex, event_ids: list[int]) -> int:
    if not event_ids:
        return 0
    conn = index._require()
    handled = 0
    now = utc_now()
    for start in range(0, len(event_ids), 400):
        batch = event_ids[start : start + 400]
        placeholders = ",".join("?" for _ in batch)
        with conn:
            cur = conn.execute(
                f"UPDATE events SET handled_at=? WHERE handled_at='' AND event_id IN ({placeholders})",
                (now, *batch),
            )
        handled += int(cur.rowcount)
    return handled


def _current_source_matches_job(index: BrainIndex, job: dict[str, Any], policy_id: str) -> tuple[bool, str]:
    payload = job.get("payload") or {}
    source = payload.get("source") or {}
    if not isinstance(source, dict):
        return False, "job_payload_invalid"
    item = index.get_file(str(job.get("source_id") or ""))
    if item is None or item.state == ProcessingState.MISSING:
        return False, "source_missing"
    if str(source.get("source_id") or "") != item.source_id:
        return False, "source_id_changed"
    if str(source.get("content_hash") or "") != item.content_hash:
        return False, "content_hash_changed"
    if str(payload.get("policy_id") or "") != policy_id:
        return False, "ai_policy_changed"
    return True, ""


def _recover_and_prune_jobs(
    index: BrainIndex,
    *,
    policy_id: str,
    processing_timeout_seconds: int,
    warnings: list[str],
) -> dict[str, int]:
    stats = {
        "stale_failed": 0,
        "processing_recovered": 0,
        "failed_requeued": 0,
        "failed_exhausted": 0,
    }

    for status in ("pending", "processing"):
        for job in list_jobs(index, status=status, limit=500):
            current, reason = _current_source_matches_job(index, job, policy_id)
            if not current:
                set_job_status(
                    index,
                    job["job_id"],
                    status="failed",
                    last_error=f"stale_before_handoff:{reason}",
                )
                stats["stale_failed"] += 1
                continue
            if status == "processing":
                age = _age_seconds(str(job.get("updated_at") or ""))
                if age is not None and age >= processing_timeout_seconds:
                    if int(job.get("attempts", 0)) >= MAX_JOB_RETRIES:
                        set_job_status(
                            index,
                            job["job_id"],
                            status="failed",
                            last_error="processing_timeout_retry_exhausted",
                        )
                        stats["failed_exhausted"] += 1
                    else:
                        set_job_status(
                            index,
                            job["job_id"],
                            status="pending",
                            last_error="processing_timeout_recovered",
                        )
                        stats["processing_recovered"] += 1

    # Failed jobs from malformed/transient AI output may be retried, but bounded.
    for job in list_jobs(index, status="failed", limit=500):
        current, _ = _current_source_matches_job(index, job, policy_id)
        if not current:
            continue
        attempts = int(job.get("attempts", 0))
        if attempts >= MAX_JOB_RETRIES:
            stats["failed_exhausted"] += 1
            continue
        set_job_status(
            index,
            job["job_id"],
            status="pending",
            last_error=str(job.get("last_error") or "retry_scheduled"),
        )
        stats["failed_requeued"] += 1

    if stats["failed_exhausted"]:
        warnings.append(
            f"{stats['failed_exhausted']} Brain Manager job đã chạm retry limit={MAX_JOB_RETRIES}; cần review."
        )
    return stats


def _queue_and_claim_ai_jobs(
    config: BrainOSConfig,
    *,
    max_ai_jobs: int,
    warnings: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Queue/claim a bounded AI handoff without starving later unresolved notes.

    Stage 8's payload builder remains the single source of truth. Stage 9 adds
    scheduler semantics: completed current-hash jobs are considered reviewed,
    stale pending work is rejected before AI, and only free concurrency slots are
    filled/claimed.
    """

    policy_id = ai_policy_id(config)
    registry = TaxonomyRegistry.from_config(config)
    opts = _watch_options(config)
    stats: dict[str, Any] = {
        "policy_id": policy_id,
        "scanned_records": 0,
        "queued": 0,
        "reused_pending": 0,
        "skipped_missing": 0,
        "skipped_manual": 0,
        "skipped_resolved": 0,
        "skipped_reviewed_current": 0,
        "claimed": 0,
    }

    with BrainIndex(config.db_path) as index:
        stats.update(
            _recover_and_prune_jobs(
                index,
                policy_id=policy_id,
                processing_timeout_seconds=opts["processing_timeout_seconds"],
                warnings=warnings,
            )
        )

        processing = list_jobs(index, status="processing", limit=500)
        active_processing = len(processing)
        free_slots = max(0, max_ai_jobs - active_processing)

        pending = list_jobs(index, status="pending", limit=max_ai_jobs)
        if len(pending) < free_slots:
            conn = index._require()
            rows = conn.execute("SELECT source_id FROM files ORDER BY path").fetchall()
            for row in rows:
                if len(pending) >= free_slots:
                    break
                item = index.get_file(str(row["source_id"]))
                if item is None:
                    continue
                stats["scanned_records"] += 1
                if item.state == ProcessingState.MISSING or not (config.brain_root / item.path).is_file():
                    stats["skipped_missing"] += 1
                    continue
                if _manual_mode(item) in {"ignore", "index"}:
                    stats["skipped_manual"] += 1
                    continue
                if not _needs_ai_review(item):
                    stats["skipped_resolved"] += 1
                    continue

                job_id = make_job_id(item.source_id, item.content_hash, policy_id)
                existing = get_job(index, job_id)
                if existing is not None:
                    status = str(existing.get("status") or "")
                    if status == "completed":
                        # The same content+policy has already received AI review.
                        # Low-confidence outcomes live as ai_review candidates rather
                        # than being re-asked every scheduled cycle.
                        stats["skipped_reviewed_current"] += 1
                        continue
                    if status == "pending":
                        if all(job["job_id"] != job_id for job in pending):
                            pending.append(existing)
                        stats["reused_pending"] += 1
                        continue
                    if status == "processing":
                        continue
                    if status == "failed":
                        # Recovery above either requeued it or exhausted retry.
                        refreshed = get_job(index, job_id)
                        if refreshed and refreshed["status"] == "pending":
                            if all(job["job_id"] != job_id for job in pending):
                                pending.append(refreshed)
                        continue

                payload = _job_payload(
                    config,
                    item,
                    registry=registry,
                    policy_id=policy_id,
                )
                job, created = enqueue_job(
                    index,
                    job_id=job_id,
                    source_id=item.source_id,
                    payload=payload,
                    priority=100,
                    force=False,
                )
                if created:
                    stats["queued"] += 1
                else:
                    stats["reused_pending"] += 1
                if job["status"] == "pending":
                    pending.append(job)

        # Claim only the free slots. `processing` is accepted by Stage 8 apply.
        handoff: list[dict[str, Any]] = []
        pending = list_jobs(index, status="pending", limit=free_slots or 1) if free_slots else []
        for job in pending[:free_slots]:
            set_job_status(index, job["job_id"], status="processing", last_error="")
            claimed = get_job(index, job["job_id"])
            if claimed is not None:
                handoff.append(claimed)
        stats["claimed"] = len(handoff)
        stats["pending_remaining"] = len(list_jobs(index, status="pending", limit=500))
        stats["active_processing"] = len(list_jobs(index, status="processing", limit=500))
        return stats, handoff, int(stats["active_processing"])


def _scan_change_count(scan: dict[str, Any]) -> int:
    return sum(
        int(scan.get(key, 0) or 0)
        for key in ("created", "modified", "moved", "renamed", "deleted", "restored")
    )


def _store_watch_meta(config: BrainOSConfig, report: BrainWatchReport) -> None:
    if not config.db_path.is_file():
        return
    summary = report.to_dict()
    summary["handoff_jobs"] = [
        {
            "job_id": str(job.get("job_id") or ""),
            "source_id": str(job.get("source_id") or ""),
            "status": str(job.get("status") or ""),
        }
        for job in report.handoff_jobs
    ]
    with BrainIndex(config.db_path) as index:
        index.set_meta("last_brain_watch_at", utc_now())
        index.set_meta("last_brain_watch_report", summary)


def run_brain_watch_cycle(
    config: BrainOSConfig,
    *,
    max_ai_jobs: int | None = None,
    force_full_hash: bool | None = None,
) -> BrainWatchReport:
    """Run one deterministic Brain Watch cycle.

    Javis owns scheduling and actual AI execution. This function only writes
    rebuildable `.javis` operational state, classifies/plans changed notes, queues
    bounded Brain Manager work, and claims jobs for handoff.
    """

    report = BrainWatchReport()
    opts = _watch_options(config)
    bounded_ai = (
        opts["max_ai_jobs"]
        if max_ai_jobs is None
        else max(1, min(int(max_ai_jobs), 20))
    )

    try:
        with _watch_lock(config) as recovered:
            report.stale_lock_recovered = recovered
            report.full_hash = _should_full_hash(config, force=force_full_hash)
            scan_report = reconcile_brain(config, full_hash=report.full_hash)
            report.scan = scan_report.to_dict()
            report.changes_detected = _scan_change_count(report.scan)
            report.warnings.extend(scan_report.warnings)
            if not scan_report.ok:
                report.ok = False
                report.stop_reason = "scan_failed"
                _store_watch_meta(config, report)
                return report

            with BrainIndex(config.db_path) as index:
                events, total_unhandled = _unhandled_events(index)
            report.unhandled_events = total_unhandled
            if total_unhandled > len(events):
                report.warnings.append(
                    f"Unhandled events vượt batch limit {MAX_EVENTS_PER_CYCLE}; phần còn lại xử lý cycle sau."
                )

            affected = sorted(
                {
                    str(event.get("path") or "")
                    for event in events
                    if str(event.get("path") or "")
                    and (config.brain_root / str(event.get("path") or "")).is_file()
                }
            )
            report.affected_paths = affected

            if affected:
                classification = classify_brain(config, paths=set(affected))
                report.classification = classification.to_dict()
                taxonomy = plan_brain_taxonomy(config, paths=set(affected))
                report.taxonomy = taxonomy.to_dict()

            ai_stats, handoff, active_processing = _queue_and_claim_ai_jobs(
                config,
                max_ai_jobs=bounded_ai,
                warnings=report.warnings,
            )
            report.ai_queue = ai_stats
            report.handoff_jobs = handoff
            report.active_processing_jobs = active_processing

            with BrainIndex(config.db_path) as index:
                report.handled_events = _mark_events_handled(
                    index,
                    [int(event["event_id"]) for event in events],
                )
                report.events_remaining = int(
                    index._require().execute(
                        "SELECT COUNT(*) FROM events WHERE handled_at=''"
                    ).fetchone()[0]
                )

            if handoff:
                report.should_stop = False
                report.stop_reason = "ai_handoff_required"
            elif active_processing:
                report.should_stop = True
                report.stop_reason = "ai_jobs_in_progress"
            elif events:
                report.should_stop = True
                report.stop_reason = "deterministic_cycle_complete"
            else:
                report.should_stop = True
                report.stop_reason = "no_changes_or_ai_backlog"

            _store_watch_meta(config, report)
            return report
    except BrainWatchError as exc:
        if str(exc) == "brain_watch_cycle_locked":
            report.locked = True
            report.should_stop = True
            report.stop_reason = "cycle_already_running"
            return report
        raise


def fail_handoff_job(config: BrainOSConfig, job_id: str, *, error: str) -> dict[str, Any]:
    """Release a claimed handoff after an external Javis/AI failure.

    Validation failures inside `apply_ai_result` already mark the job failed. This
    command is for failures before a valid result reaches that function.
    """

    if not config.db_path.is_file():
        raise BrainWatchError("Brain index chưa được khởi tạo")
    message = str(error or "external_ai_failure").strip()[:1000]
    with BrainIndex(config.db_path) as index:
        job = get_job(index, job_id)
        if job is None:
            raise BrainWatchError(f"Không tìm thấy Brain Manager job: {job_id}")
        if job["status"] == "completed":
            raise BrainWatchError(f"Job đã completed, không được fail lại: {job_id}")
        set_job_status(
            index,
            job_id,
            status="failed",
            last_error=message,
            increment_attempt=True,
        )
        latest = get_job(index, job_id)
    return latest or {}
