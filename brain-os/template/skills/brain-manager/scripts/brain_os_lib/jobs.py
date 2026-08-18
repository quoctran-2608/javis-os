from __future__ import annotations

import hashlib
import json
from typing import Any

from .db import BrainIndex, utc_now

VALID_JOB_STATUSES = {"pending", "processing", "completed", "failed"}


def make_job_id(source_id: str, content_hash: str, policy_id: str) -> str:
    raw = f"{source_id}\0{content_hash}\0{policy_id}".encode("utf-8")
    return "brain_ai_" + hashlib.sha256(raw).hexdigest()[:24]


def _decode_json(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _row_to_job(row: Any) -> dict[str, Any]:
    return {
        "job_id": str(row["job_id"]),
        "source_id": str(row["source_id"]),
        "job_type": str(row["job_type"]),
        "status": str(row["status"]),
        "priority": int(row["priority"]),
        "payload": _decode_json(str(row["payload_json"])),
        "attempts": int(row["attempts"]),
        "last_error": str(row["last_error"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def enqueue_job(
    index: BrainIndex,
    *,
    job_id: str,
    source_id: str,
    payload: dict[str, Any],
    priority: int = 100,
    force: bool = False,
) -> tuple[dict[str, Any], bool]:
    conn = index._require()
    row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    now = utc_now()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if row is not None and not force:
        return _row_to_job(row), False
    if row is None:
        with conn:
            conn.execute(
                """INSERT INTO jobs(
                    job_id, source_id, job_type, status, priority, payload_json,
                    attempts, last_error, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    job_id,
                    source_id,
                    "brain_manager_review",
                    "pending",
                    int(priority),
                    encoded,
                    0,
                    "",
                    now,
                    now,
                ),
            )
    else:
        with conn:
            conn.execute(
                """UPDATE jobs
                   SET source_id=?, job_type='brain_manager_review', status='pending',
                       priority=?, payload_json=?, attempts=0, last_error='', updated_at=?
                   WHERE job_id=?""",
                (source_id, int(priority), encoded, now, job_id),
            )
    row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    return _row_to_job(row), True


def get_job(index: BrainIndex, job_id: str) -> dict[str, Any] | None:
    row = index._require().execute(
        "SELECT * FROM jobs WHERE job_id=?", (job_id,)
    ).fetchone()
    return _row_to_job(row) if row is not None else None


def list_jobs(
    index: BrainIndex,
    *,
    status: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    conn = index._require()
    bounded = max(1, min(int(limit), 500))
    if status:
        if status not in VALID_JOB_STATUSES:
            raise ValueError(f"Job status không hợp lệ: {status!r}")
        rows = conn.execute(
            """SELECT * FROM jobs
               WHERE job_type='brain_manager_review' AND status=?
               ORDER BY priority ASC, created_at ASC LIMIT ?""",
            (status, bounded),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM jobs
               WHERE job_type='brain_manager_review'
               ORDER BY created_at DESC LIMIT ?""",
            (bounded,),
        ).fetchall()
    return [_row_to_job(row) for row in rows]


def set_job_status(
    index: BrainIndex,
    job_id: str,
    *,
    status: str,
    last_error: str = "",
    increment_attempt: bool = False,
) -> None:
    if status not in VALID_JOB_STATUSES:
        raise ValueError(f"Job status không hợp lệ: {status!r}")
    conn = index._require()
    now = utc_now()
    with conn:
        if increment_attempt:
            cur = conn.execute(
                """UPDATE jobs
                   SET status=?, attempts=attempts+1, last_error=?, updated_at=?
                   WHERE job_id=? AND job_type='brain_manager_review'""",
                (status, str(last_error), now, job_id),
            )
        else:
            cur = conn.execute(
                """UPDATE jobs
                   SET status=?, last_error=?, updated_at=?
                   WHERE job_id=? AND job_type='brain_manager_review'""",
                (status, str(last_error), now, job_id),
            )
    if cur.rowcount != 1:
        raise ValueError(f"Không tìm thấy Brain Manager job: {job_id}")
