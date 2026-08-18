from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


class DocumentExtractionError(RuntimeError):
    """Fail-safe extraction error for Stage 10 document normalization."""


@dataclass(frozen=True)
class ExtractionResult:
    source_format: str
    backend: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["warnings"] = list(self.warnings)
        return data


def normalized_suffix(path: Path | str) -> str:
    return Path(path).suffix.casefold().lstrip(".")
