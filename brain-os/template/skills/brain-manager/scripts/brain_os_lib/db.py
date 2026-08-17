from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import BrainFile, DocumentType, ProcessingState


SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    source_id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    file_type TEXT NOT NULL DEFAULT '',
    document_type TEXT NOT NULL DEFAULT 'unknown',
    category_id TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    mtime_ns INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL DEFAULT '',
    last_seen_hash TEXT NOT NULL DEFAULT '',
    last_ingested_hash TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'discovered',
    origin TEXT NOT NULL DEFAULT 'brain',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_indexed_at TEXT NOT NULL DEFAULT '',
    last_ingested_at TEXT NOT NULL DEFAULT '',
    deleted_at TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
CREATE INDEX IF NOT EXISTS idx_files_state ON files(state);
CREATE INDEX IF NOT EXISTS idx_files_hash ON files(content_hash);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    path TEXT NOT NULL DEFAULT '',
    old_path TEXT NOT NULL DEFAULT '',
    old_hash TEXT NOT NULL DEFAULT '',
    new_hash TEXT NOT NULL DEFAULT '',
    observed_at TEXT NOT NULL,
    handled_at TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_events_unhandled
    ON events(handled_at, event_id);
CREATE INDEX IF NOT EXISTS idx_events_source
    ON events(source_id, event_id);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL DEFAULT '',
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 100,
    payload_json TEXT NOT NULL DEFAULT '{}',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_queue
    ON jobs(status, priority, created_at);

CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'pending',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidates_status
    ON candidates(status, kind, confidence);

CREATE TABLE IF NOT EXISTS relationships (
    source_id TEXT NOT NULL,
    target TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source_id, target, relationship_type)
);
"""


class BrainIndexError(RuntimeError):
    pass


class BrainIndex:
    """Rebuildable operational index.

    The database never becomes the source of truth. Markdown/files remain
    authoritative; this DB can be deleted and reconstructed.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser().resolve()
        self.conn: sqlite3.Connection | None = None

    def open(self, *, initialize: bool = True) -> "BrainIndex":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass
        conn.execute("PRAGMA synchronous = NORMAL")
        self.conn = conn
        try:
            if initialize:
                self.initialize()
        except Exception:
            conn.close()
            self.conn = None
            raise
        return self

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def __enter__(self) -> "BrainIndex":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _require(self) -> sqlite3.Connection:
        if self.conn is None:
            raise BrainIndexError("BrainIndex chưa được open().")
        return self.conn

    def initialize(self) -> None:
        conn = self._require()
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise BrainIndexError(
                f"DB schema {version} mới hơn code hỗ trợ {SCHEMA_VERSION}; từ chối downgrade."
            )

        if version == 0:
            with conn:
                conn.executescript(SCHEMA_SQL)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO meta(key, value) VALUES('created_at', ?)",
                    (utc_now(),),
                )
        elif version == SCHEMA_VERSION:
            with conn:
                conn.executescript(SCHEMA_SQL)
        else:
            raise BrainIndexError(f"Không có migration từ schema {version}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._require()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()

    def set_meta(self, key: str, value: Any) -> None:
        conn = self._require()
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        with conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, text),
            )

    def get_meta(self, key: str, default: str = "") -> str:
        conn = self._require()
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def upsert_file(self, item: BrainFile) -> None:
        conn = self._require()
        now = utc_now()
        created_at = item.created_at or now
        updated_at = item.updated_at or now
        metadata_json = json.dumps(item.metadata or {}, ensure_ascii=False, sort_keys=True)

        values = (
            item.source_id,
            item.path,
            item.file_type,
            item.document_type.value if isinstance(item.document_type, DocumentType) else str(item.document_type),
            item.category_id,
            int(item.size),
            int(item.mtime_ns),
            item.content_hash,
            item.last_seen_hash,
            item.last_ingested_hash,
            item.state.value if isinstance(item.state, ProcessingState) else str(item.state),
            item.origin,
            created_at,
            updated_at,
            item.last_indexed_at,
            item.last_ingested_at,
            item.deleted_at,
            metadata_json,
        )
        sql = """
        INSERT INTO files(
            source_id, path, file_type, document_type, category_id,
            size, mtime_ns, content_hash, last_seen_hash, last_ingested_hash,
            state, origin, created_at, updated_at, last_indexed_at,
            last_ingested_at, deleted_at, metadata_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_id) DO UPDATE SET
            path=excluded.path,
            file_type=excluded.file_type,
            document_type=excluded.document_type,
            category_id=excluded.category_id,
            size=excluded.size,
            mtime_ns=excluded.mtime_ns,
            content_hash=excluded.content_hash,
            last_seen_hash=excluded.last_seen_hash,
            last_ingested_hash=excluded.last_ingested_hash,
            state=excluded.state,
            origin=excluded.origin,
            updated_at=excluded.updated_at,
            last_indexed_at=excluded.last_indexed_at,
            last_ingested_at=excluded.last_ingested_at,
            deleted_at=excluded.deleted_at,
            metadata_json=excluded.metadata_json
        """
        try:
            with conn:
                conn.execute(sql, values)
        except sqlite3.IntegrityError as exc:
            raise BrainIndexError(
                f"Không upsert được file {item.source_id!r} path={item.path!r}: {exc}"
            ) from exc

    def _row_to_file(self, row: sqlite3.Row) -> BrainFile:
        try:
            document_type = DocumentType(row["document_type"])
        except ValueError:
            document_type = DocumentType.UNKNOWN
        try:
            state = ProcessingState(row["state"])
        except ValueError:
            state = ProcessingState.DISCOVERED
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}

        return BrainFile(
            source_id=row["source_id"],
            path=row["path"],
            file_type=row["file_type"],
            document_type=document_type,
            category_id=row["category_id"],
            size=row["size"],
            mtime_ns=row["mtime_ns"],
            content_hash=row["content_hash"],
            last_seen_hash=row["last_seen_hash"],
            last_ingested_hash=row["last_ingested_hash"],
            state=state,
            origin=row["origin"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_indexed_at=row["last_indexed_at"],
            last_ingested_at=row["last_ingested_at"],
            deleted_at=row["deleted_at"],
            metadata=metadata if isinstance(metadata, dict) else {},
        )

    def get_file(self, source_id: str) -> BrainFile | None:
        conn = self._require()
        row = conn.execute(
            "SELECT * FROM files WHERE source_id=?", (source_id,)
        ).fetchone()
        return self._row_to_file(row) if row else None

    def get_file_by_path(self, path: str) -> BrainFile | None:
        conn = self._require()
        row = conn.execute("SELECT * FROM files WHERE path=?", (path,)).fetchone()
        return self._row_to_file(row) if row else None

    def counts(self) -> dict[str, int]:
        conn = self._require()
        tables = ("files", "events", "jobs", "candidates", "relationships")
        return {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }

    def status(self) -> dict[str, Any]:
        conn = self._require()
        return {
            "path": str(self.path),
            "schema_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
            "journal_mode": str(conn.execute("PRAGMA journal_mode").fetchone()[0]),
            "counts": self.counts(),
        }
