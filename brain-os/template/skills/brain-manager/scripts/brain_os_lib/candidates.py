from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any

from .db import BrainIndex, utc_now

VALID_CANDIDATE_STATUSES = {"pending", "accepted", "rejected"}
VALID_CANDIDATE_KINDS = {"wiki", "memory", "ai_review"}


def make_candidate_id(
    source_id: str,
    kind: str,
    content_hash: str,
    route: str = "",
) -> str:
    raw = f"{source_id}\0{kind}\0{content_hash}\0{route}".encode("utf-8")
    return "cand_" + hashlib.sha256(raw).hexdigest()[:24]


def _decode_json(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _row_to_candidate(row: Any) -> dict[str, Any]:
    return {
        "candidate_id": str(row["candidate_id"]),
        "source_id": str(row["source_id"]),
        "kind": str(row["kind"]),
        "title": str(row["title"]),
        "confidence": float(row["confidence"]),
        "status": str(row["status"]),
        "payload": _decode_json(str(row["payload_json"])),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def upsert_candidate(
    index: BrainIndex,
    *,
    candidate_id: str,
    source_id: str,
    kind: str,
    title: str,
    confidence: float,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if kind not in VALID_CANDIDATE_KINDS:
        raise ValueError(f"Candidate kind không hợp lệ: {kind!r}")
    conn = index._require()
    now = utc_now()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    with conn:
        conn.execute(
            """INSERT INTO candidates(
                candidate_id, source_id, kind, title, confidence, status,
                payload_json, created_at, updated_at
            ) VALUES(?,?,?,?,?,'pending',?,?,?)
            ON CONFLICT(candidate_id) DO UPDATE SET
                title=excluded.title,
                confidence=excluded.confidence,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at""",
            (
                candidate_id,
                source_id,
                kind,
                title,
                float(confidence),
                encoded,
                now,
                now,
            ),
        )
    row = conn.execute(
        "SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,)
    ).fetchone()
    return _row_to_candidate(row)


def list_candidates(
    index: BrainIndex,
    *,
    kind: str = "",
    status: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    if kind and kind not in VALID_CANDIDATE_KINDS:
        raise ValueError(f"Candidate kind không hợp lệ: {kind!r}")
    if status and status not in VALID_CANDIDATE_STATUSES:
        raise ValueError(f"Candidate status không hợp lệ: {status!r}")
    clauses: list[str] = []
    args: list[Any] = []
    if kind:
        clauses.append("kind=?")
        args.append(kind)
    if status:
        clauses.append("status=?")
        args.append(status)
    sql = "SELECT * FROM candidates"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY confidence DESC, created_at ASC LIMIT ?"
    args.append(max(1, min(int(limit), 500)))
    rows = index._require().execute(sql, args).fetchall()
    return [_row_to_candidate(row) for row in rows]


def title_for_path(path: str) -> str:
    return PurePosixPath(path).stem
