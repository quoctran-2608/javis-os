from __future__ import annotations

from pathlib import Path
from typing import Any

from . import DocumentExtractionError, ExtractionResult


def extract_pdf(path: Path | str, *, max_pages: int = 1000) -> ExtractionResult:
    source = Path(path)
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DocumentExtractionError(
            "PDF extraction cần dependency `pypdf`. Cài "
            "`brain-os/requirements-documents.txt` trước khi import PDF."
        ) from exc

    try:
        reader = PdfReader(str(source), strict=False)
    except Exception as exc:
        raise DocumentExtractionError(f"PDF không đọc được: {source}: {exc}") from exc

    try:
        encrypted = bool(reader.is_encrypted)
    except Exception:
        encrypted = False
    if encrypted:
        raise DocumentExtractionError(
            "PDF được mã hóa/password-protected; Stage 10 từ chối fail-closed."
        )

    page_count = len(reader.pages)
    if page_count > max(1, int(max_pages)):
        raise DocumentExtractionError(
            f"PDF có quá nhiều trang: {page_count}>{max_pages}"
        )

    parts: list[str] = []
    warnings: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            raise DocumentExtractionError(
                f"Không extract được PDF page {index}: {exc}"
            ) from exc
        text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            warnings.append(f"page_without_text:{index}")
            continue
        parts.append(f"## Page {index}\n\n{text}")

    if not parts:
        raise DocumentExtractionError(
            "PDF không có text layer extractable. Stage 10 V1 không tự OCR; "
            "hãy dùng Javis/document vision pipeline để tạo normalized source có provenance."
        )

    metadata: dict[str, Any] = {"page_count": page_count}
    raw_meta = getattr(reader, "metadata", None)
    if raw_meta:
        for source_key, target_key in (
            ("/Title", "title"),
            ("/Author", "author"),
            ("/Subject", "subject"),
            ("/Creator", "creator"),
            ("/Producer", "producer"),
        ):
            try:
                value = raw_meta.get(source_key)
            except Exception:
                value = None
            if value is not None and str(value).strip():
                metadata[target_key] = str(value).strip()

    return ExtractionResult(
        source_format="pdf",
        backend="pypdf",
        text="\n\n".join(parts).strip() + "\n",
        metadata=metadata,
        warnings=tuple(warnings),
    )
