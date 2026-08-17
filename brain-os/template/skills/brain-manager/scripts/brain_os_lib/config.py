from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - Javis runtime probe catches this first
    yaml = None


SCHEMA_VERSION = 1


class BrainOSConfigError(RuntimeError):
    pass


def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise BrainOSConfigError("Brain OS cần PyYAML (`yaml`) trong runtime.")
    if not path.is_file():
        raise BrainOSConfigError(f"Thiếu file cấu hình: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise BrainOSConfigError(
            f"Không đọc được YAML {path}: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise BrainOSConfigError(f"YAML root phải là mapping: {path}")
    return data


def _safe_rel_path(value: Any, *, field: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/"):
        raise BrainOSConfigError(f"{field} phải là relative path an toàn: {value!r}")
    parts = raw.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise BrainOSConfigError(f"{field} chứa thành phần path không an toàn: {value!r}")
    return "/".join(parts)


def _safe_field_name(value: Any, *, field: str) -> str:
    raw = str(value or "").strip()
    if not raw or any(ch in raw for ch in "\r\n"):
        raise BrainOSConfigError(f"{field} phải là tên field không rỗng: {value!r}")
    return raw


@dataclass(frozen=True)
class BrainOSConfig:
    brain_root: Path
    core: dict[str, Any]
    folders: dict[str, Any]
    tags: dict[str, Any]
    tag_aliases: dict[str, Any]

    @classmethod
    def load(cls, brain_root: Path | str) -> "BrainOSConfig":
        root = Path(brain_root).expanduser().resolve()
        if not root.is_dir():
            raise BrainOSConfigError(f"Brain root không tồn tại hoặc không phải thư mục: {root}")

        core_path = root / "System" / "BrainOS" / "config.yml"
        folder_path = root / "System" / "Taxonomy" / "folders.yml"
        tags_path = root / "System" / "Taxonomy" / "tags.yml"
        aliases_path = root / "System" / "Taxonomy" / "tag-aliases.yml"

        core = _load_yaml(core_path)
        folders = _load_yaml(folder_path)
        tags = _load_yaml(tags_path)
        aliases = _load_yaml(aliases_path)

        for path, data in (
            (core_path, core),
            (folder_path, folders),
            (tags_path, tags),
            (aliases_path, aliases),
        ):
            if int(data.get("schema_version", 0) or 0) != SCHEMA_VERSION:
                raise BrainOSConfigError(
                    f"{path}: schema_version phải là {SCHEMA_VERSION}"
                )

        if core.get("mode") not in {"conservative", "balanced", "aggressive"}:
            raise BrainOSConfigError(f"{core_path}: mode không hợp lệ")

        path_map = core.get("paths")
        if not isinstance(path_map, dict):
            raise BrainOSConfigError(f"{core_path}: paths phải là mapping")
        for key, value in path_map.items():
            _safe_rel_path(value, field=f"paths.{key}")

        zones = core.get("zones")
        if not isinstance(zones, dict):
            raise BrainOSConfigError(f"{core_path}: zones phải là mapping")

        required_protected = ("00 - Dashboard", "wiki", ".javis")
        for zone in required_protected:
            policy = zones.get(zone)
            if not isinstance(policy, dict) or policy.get("ingest") != "never":
                raise BrainOSConfigError(
                    f"{core_path}: zone {zone!r} bắt buộc ingest: never"
                )

        scan = core.get("scan") or {}
        if not isinstance(scan, dict):
            raise BrainOSConfigError(f"{core_path}: scan phải là mapping")
        extensions = scan.get("extensions") or [".md", ".markdown"]
        if not isinstance(extensions, list) or not extensions:
            raise BrainOSConfigError(f"{core_path}: scan.extensions phải là list không rỗng")
        for ext in extensions:
            value = str(ext or "").strip()
            if not value.startswith(".") or "/" in value or "\\" in value:
                raise BrainOSConfigError(
                    f"{core_path}: scan extension không hợp lệ: {ext!r}"
                )
        if bool(scan.get("follow_symlinks", False)):
            raise BrainOSConfigError(
                f"{core_path}: Brain OS V1 bắt buộc scan.follow_symlinks: false"
            )
        try:
            hash_retries = int(scan.get("hash_retries", 1))
            max_snapshot = int(scan.get("max_snapshot_bytes", 2 * 1024 * 1024))
        except (TypeError, ValueError) as exc:
            raise BrainOSConfigError(
                f"{core_path}: scan.hash_retries/max_snapshot_bytes phải là số nguyên"
            ) from exc
        if not (0 <= hash_retries <= 5):
            raise BrainOSConfigError(f"{core_path}: scan.hash_retries phải trong 0..5")
        if max_snapshot < 0:
            raise BrainOSConfigError(f"{core_path}: scan.max_snapshot_bytes phải >= 0")
        if scan.get("deletion_policy", "mark_missing") != "mark_missing":
            raise BrainOSConfigError(
                f"{core_path}: Brain OS V1 chỉ hỗ trợ scan.deletion_policy: mark_missing"
            )

        classification = core.get("classification") or {}
        if not isinstance(classification, dict):
            raise BrainOSConfigError(f"{core_path}: classification phải là mapping")
        try:
            accept_confidence = float(classification.get("accept_confidence", 0.80))
            candidate_confidence = float(classification.get("candidate_confidence", 0.55))
            auto_move_confidence = float(classification.get("auto_move_confidence", 0.80))
        except (TypeError, ValueError) as exc:
            raise BrainOSConfigError(
                f"{core_path}: classification confidence phải là số"
            ) from exc
        if not 0.0 <= candidate_confidence <= accept_confidence <= 1.0:
            raise BrainOSConfigError(
                f"{core_path}: cần 0 <= candidate_confidence <= accept_confidence <= 1"
            )
        if not 0.0 <= auto_move_confidence <= 1.0:
            raise BrainOSConfigError(
                f"{core_path}: classification.auto_move_confidence phải trong 0..1"
            )
        _safe_field_name(
            classification.get("explicit_type_field", "javis_type"),
            field="classification.explicit_type_field",
        )
        _safe_field_name(
            classification.get("fallback_type_field", "type"),
            field="classification.fallback_type_field",
        )
        if classification.get("default_when_uncertain", "index") not in {"index", "needs_ai"}:
            raise BrainOSConfigError(
                f"{core_path}: classification.default_when_uncertain không hợp lệ"
            )

        manual = core.get("manual_override") or {}
        if not isinstance(manual, dict):
            raise BrainOSConfigError(f"{core_path}: manual_override phải là mapping")
        _safe_field_name(manual.get("field", "javis"), field="manual_override.field")
        allowed_modes = manual.get("allowed_values") or ["auto", "ignore", "index", "ingest", "wiki"]
        if not isinstance(allowed_modes, list) or not allowed_modes:
            raise BrainOSConfigError(
                f"{core_path}: manual_override.allowed_values phải là list không rỗng"
            )
        allowed_set = {str(value or "").strip().casefold() for value in allowed_modes}
        supported_modes = {"auto", "ignore", "index", "ingest", "wiki"}
        if "auto" not in allowed_set or not allowed_set.issubset(supported_modes):
            raise BrainOSConfigError(
                f"{core_path}: manual_override.allowed_values chỉ hỗ trợ {sorted(supported_modes)} và phải có 'auto'"
            )

        return cls(
            brain_root=root,
            core=core,
            folders=folders,
            tags=tags,
            tag_aliases=aliases,
        )

    @property
    def dry_run(self) -> bool:
        return bool(self.core.get("dry_run", True))

    @property
    def mode(self) -> str:
        return str(self.core.get("mode", "balanced"))

    @property
    def db_path(self) -> Path:
        index = self.core.get("index") or {}
        rel = _safe_rel_path(index.get("database", ".javis/brain-index.db"), field="index.database")
        return self.brain_root.joinpath(*rel.split("/"))

    @property
    def ignore_paths(self) -> tuple[str, ...]:
        values = self.core.get("ignore_paths") or []
        if not isinstance(values, list):
            raise BrainOSConfigError("ignore_paths phải là list")
        return tuple(_safe_rel_path(v, field="ignore_paths[]") for v in values)

    def path(self, key: str) -> Path:
        path_map = self.core.get("paths") or {}
        if key not in path_map:
            raise BrainOSConfigError(f"Không có paths.{key}")
        rel = _safe_rel_path(path_map[key], field=f"paths.{key}")
        return self.brain_root.joinpath(*rel.split("/"))

    def zone_policy(self, zone: str) -> dict[str, Any]:
        zones = self.core.get("zones") or {}
        value = zones.get(zone) or {}
        if not isinstance(value, dict):
            raise BrainOSConfigError(f"Zone policy không hợp lệ: {zone}")
        return dict(value)

    def summary(self) -> dict[str, Any]:
        scan = self.core.get("scan") or {}
        classification = self.core.get("classification") or {}
        manual = self.core.get("manual_override") or {}
        return {
            "schema_version": SCHEMA_VERSION,
            "brain_root": str(self.brain_root),
            "mode": self.mode,
            "dry_run": self.dry_run,
            "database": str(self.db_path),
            "zones": sorted((self.core.get("zones") or {}).keys()),
            "ignore_paths": list(self.ignore_paths),
            "scan": {
                "extensions": list(scan.get("extensions") or [".md", ".markdown"]),
                "ignore_hidden": bool(scan.get("ignore_hidden", True)),
                "follow_symlinks": bool(scan.get("follow_symlinks", False)),
                "hash_retries": int(scan.get("hash_retries", 1)),
                "max_snapshot_bytes": int(scan.get("max_snapshot_bytes", 2 * 1024 * 1024)),
                "deletion_policy": str(scan.get("deletion_policy", "mark_missing")),
            },
            "classification": {
                "accept_confidence": float(classification.get("accept_confidence", 0.80)),
                "candidate_confidence": float(classification.get("candidate_confidence", 0.55)),
                "auto_move_confidence": float(classification.get("auto_move_confidence", 0.80)),
                "explicit_type_field": str(classification.get("explicit_type_field", "javis_type")),
                "fallback_type_field": str(classification.get("fallback_type_field", "type")),
                "default_when_uncertain": str(classification.get("default_when_uncertain", "index")),
            },
            "manual_override": {
                "field": str(manual.get("field", "javis")),
                "allowed_values": list(manual.get("allowed_values") or ["auto", "ignore", "index", "ingest", "wiki"]),
            },
        }
