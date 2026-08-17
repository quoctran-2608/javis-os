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
        return {
            "schema_version": SCHEMA_VERSION,
            "brain_root": str(self.brain_root),
            "mode": self.mode,
            "dry_run": self.dry_run,
            "database": str(self.db_path),
            "zones": sorted((self.core.get("zones") or {}).keys()),
            "ignore_paths": list(self.ignore_paths),
        }
