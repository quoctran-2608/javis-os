from __future__ import annotations

import stat
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from . import DocumentExtractionError, ExtractionResult


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MAX_ZIP_ENTRIES = 20_000
DEFAULT_MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024


def _safe_zip_name(name: str) -> str:
    raw = str(name or "").replace("\\", "/")
    pure = PurePosixPath(raw)
    if not raw or raw.startswith("/") or any(part in ("", ".", "..") for part in pure.parts):
        raise DocumentExtractionError(f"Office ZIP path không an toàn: {name!r}")
    if pure.parts and ":" in pure.parts[0]:
        raise DocumentExtractionError(f"Office ZIP drive path không an toàn: {name!r}")
    return pure.as_posix()


def _validate_zip(archive: zipfile.ZipFile, *, max_uncompressed_bytes: int) -> None:
    infos = archive.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise DocumentExtractionError(
            f"Office ZIP có quá nhiều entries: {len(infos)}>{MAX_ZIP_ENTRIES}"
        )
    total = 0
    seen: set[str] = set()
    for info in infos:
        safe = _safe_zip_name(info.filename)
        key = safe.casefold()
        if key in seen:
            raise DocumentExtractionError(f"Office ZIP có duplicate path: {safe}")
        seen.add(key)
        if info.flag_bits & 0x1:
            raise DocumentExtractionError(f"Office ZIP có encrypted entry: {safe}")
        if stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
            raise DocumentExtractionError(f"Office ZIP chứa symlink: {safe}")
        total += int(info.file_size)
        if total > max_uncompressed_bytes:
            raise DocumentExtractionError(
                "Office ZIP vượt giới hạn uncompressed an toàn "
                f"{max_uncompressed_bytes} bytes"
            )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _paragraph_text(node: ET.Element) -> str:
    out: list[str] = []
    for child in node.iter():
        local = _local(child.tag)
        if local == "t":
            out.append(child.text or "")
        elif local == "tab":
            out.append("\t")
        elif local in {"br", "cr"}:
            out.append("\n")
    return "".join(out).strip()


def _table_markdown(table: ET.Element) -> str:
    rows: list[list[str]] = []
    for tr in table.findall(f".//{{{W_NS}}}tr"):
        row: list[str] = []
        for tc in tr.findall(f"./{{{W_NS}}}tc"):
            paragraphs = [_paragraph_text(p) for p in tc.findall(f".//{{{W_NS}}}p")]
            text = " ".join(value for value in paragraphs if value).strip()
            row.append(text.replace("|", r"\|").replace("\n", " "))
        rows.append(row)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    if width <= 0:
        return ""
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = [f"Column {i}" for i in range(1, width + 1)]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in normalized:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _core_metadata(archive: zipfile.ZipFile) -> dict[str, str]:
    try:
        raw = archive.read("docProps/core.xml")
    except KeyError:
        return {}
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return {}
    result: dict[str, str] = {}
    for element in root.iter():
        local = _local(element.tag)
        text = (element.text or "").strip()
        if text and local in {"title", "creator", "subject", "description", "created", "modified"}:
            result[local] = text
    return result


def extract_docx(path: Path | str, *, max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES) -> ExtractionResult:
    source = Path(path)
    try:
        archive = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile) as exc:
        raise DocumentExtractionError(f"DOCX không hợp lệ: {source}: {exc}") from exc
    with archive:
        _validate_zip(archive, max_uncompressed_bytes=max(1, int(max_uncompressed_bytes)))
        try:
            raw = archive.read("word/document.xml")
        except KeyError as exc:
            raise DocumentExtractionError("DOCX thiếu word/document.xml") from exc
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise DocumentExtractionError(f"DOCX document.xml không parse được: {exc}") from exc
        body = root.find(f".//{{{W_NS}}}body")
        if body is None:
            raise DocumentExtractionError("DOCX thiếu w:body")
        blocks: list[str] = []
        for child in list(body):
            local = _local(child.tag)
            if local == "p":
                text = _paragraph_text(child)
                if text:
                    blocks.append(text)
            elif local == "tbl":
                table = _table_markdown(child)
                if table:
                    blocks.append(table)
        if not blocks:
            raise DocumentExtractionError(
                "DOCX không có text/table extractable. Stage 10 V1 không OCR embedded images."
            )
        return ExtractionResult(
            source_format="docx",
            backend="stdlib-docx-xml",
            text="\n\n".join(blocks).strip() + "\n",
            metadata=_core_metadata(archive),
            warnings=(),
        )
