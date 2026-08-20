from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class OriginalsError(RuntimeError):
    """Fail-safe error for immutable import provenance."""


@dataclass(frozen=True)
class OriginalSnapshot:
    source_id: str
    source_sha256: str
    snapshot_path: str
    manifest_path: str
    working_path: str
    document_type: str
    category_id: str
    reused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path | str) -> str:
    fp = Path(path)
    digest = hashlib.sha256()
    with fp.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.brain-os-",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(path, encoded)


class OriginalStore:
    """Immutable snapshots for imported source material.

    Every import gets exactly one byte-for-byte `original.md` plus a manifest under
    `.javis/originals/imports/<javis_id>/`. Existing snapshots are verified before
    reuse and are never overwritten.
    """

    def __init__(self, brain_root: Path | str):
        self.brain_root = Path(brain_root).expanduser().resolve()
        self.root = self.brain_root / ".javis" / "originals" / "imports"

    def _load_manifest(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OriginalsError(f"Manifest provenance hỏng: {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise OriginalsError(f"Manifest provenance phải là object: {path}")
        return payload

    def _snapshot_from_manifest(
        self,
        manifest_path: Path,
        payload: dict[str, Any],
        *,
        reused: bool,
    ) -> OriginalSnapshot:
        source_id = str(payload.get("source_id") or "").strip()
        source_hash = str(payload.get("source_sha256") or "").strip()
        if not source_id or len(source_hash) != 64:
            raise OriginalsError(f"Manifest provenance thiếu identity/hash: {manifest_path}")

        original = manifest_path.parent / "original.md"
        if not original.is_file():
            raise OriginalsError(f"Snapshot provenance bị thiếu: {original}")
        actual = sha256_file(original)
        if actual != source_hash:
            raise OriginalsError(
                f"Snapshot immutable bị thay đổi: {original}; expected={source_hash} actual={actual}"
            )

        # Public provenance paths are Brain-relative, like working_path and all
        # dry-run plans. Keep absolute Paths only for internal filesystem I/O so
        # preview/apply results are deterministic and portable across Brain roots.
        snapshot_path = original.relative_to(self.brain_root).as_posix()
        manifest_rel = manifest_path.relative_to(self.brain_root).as_posix()

        return OriginalSnapshot(
            source_id=source_id,
            source_sha256=source_hash,
            snapshot_path=snapshot_path,
            manifest_path=manifest_rel,
            working_path=str(payload.get("working_path") or ""),
            document_type=str(payload.get("document_type") or ""),
            category_id=str(payload.get("category_id") or ""),
            reused=reused,
        )

    def find_by_hash(self, source_sha256: str) -> OriginalSnapshot | None:
        if not self.root.is_dir():
            return None
        for manifest in sorted(self.root.glob("*/manifest.json")):
            payload = self._load_manifest(manifest)
            if str(payload.get("source_sha256") or "") == source_sha256:
                return self._snapshot_from_manifest(manifest, payload, reused=True)
        return None

    def preserve(
        self,
        *,
        source_id: str,
        source_bytes: bytes,
        source_path: Path | str,
        working_path: str,
        document_type: str,
        category_id: str = "",
    ) -> OriginalSnapshot:
        source_hash = sha256_bytes(source_bytes)
        existing = self.find_by_hash(source_hash)
        if existing is not None:
            return existing

        target = self.root / source_id
        original = target / "original.md"
        manifest = target / "manifest.json"

        if target.exists():
            if not manifest.is_file():
                raise OriginalsError(
                    f"Import provenance collision cho {source_id}: thiếu manifest tại {target}"
                )
            payload = self._load_manifest(manifest)
            snapshot = self._snapshot_from_manifest(manifest, payload, reused=True)
            if snapshot.source_sha256 != source_hash:
                raise OriginalsError(
                    f"Stable identity collision: {source_id} đã thuộc snapshot khác"
                )
            return snapshot

        target.mkdir(parents=True, exist_ok=False)
        _atomic_write_bytes(original, source_bytes)
        payload = {
            "schema_version": 1,
            "source_id": source_id,
            "source_sha256": source_hash,
            "source_path": str(Path(source_path).expanduser().resolve()),
            "original_name": Path(source_path).name,
            "document_type": document_type,
            "category_id": category_id,
            "working_path": working_path,
            "imported_at": _utc_now(),
        }
        _atomic_write_json(manifest, payload)

        # Read back and hash-verify before reporting success.
        return self._snapshot_from_manifest(manifest, payload, reused=False)
