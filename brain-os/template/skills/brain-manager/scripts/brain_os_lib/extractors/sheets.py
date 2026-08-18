from __future__ import annotations

import csv
import io
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from . import DocumentExtractionError, ExtractionResult


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MAX_ZIP_ENTRIES = 20_000
DEFAULT_MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
_CELL_RE = re.compile(r"^([A-Z]+)([1-9][0-9]*)$")


def _safe_zip_name(name: str) -> str:
    raw = str(name or "").replace("\\", "/")
    pure = PurePosixPath(raw)
    if not raw or raw.startswith("/") or any(part in ("", ".", "..") for part in pure.parts):
        raise DocumentExtractionError(f"Spreadsheet ZIP path không an toàn: {name!r}")
    if pure.parts and ":" in pure.parts[0]:
        raise DocumentExtractionError(f"Spreadsheet ZIP drive path không an toàn: {name!r}")
    return pure.as_posix()


def _validate_zip(archive: zipfile.ZipFile, *, max_uncompressed_bytes: int) -> None:
    infos = archive.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise DocumentExtractionError(
            f"Spreadsheet ZIP có quá nhiều entries: {len(infos)}>{MAX_ZIP_ENTRIES}"
        )
    total = 0
    seen: set[str] = set()
    for info in infos:
        safe = _safe_zip_name(info.filename)
        key = safe.casefold()
        if key in seen:
            raise DocumentExtractionError(f"Spreadsheet ZIP có duplicate path: {safe}")
        seen.add(key)
        if info.flag_bits & 0x1:
            raise DocumentExtractionError(f"Spreadsheet ZIP có encrypted entry: {safe}")
        if stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
            raise DocumentExtractionError(f"Spreadsheet ZIP chứa symlink: {safe}")
        total += int(info.file_size)
        if total > max_uncompressed_bytes:
            raise DocumentExtractionError(
                "Spreadsheet ZIP vượt giới hạn uncompressed an toàn "
                f"{max_uncompressed_bytes} bytes"
            )


def _column_index(name: str) -> int:
    value = 0
    for ch in name:
        value = value * 26 + (ord(ch) - ord("A") + 1)
    return value - 1


def _column_name(index: int) -> str:
    value = index + 1
    chars: list[str] = []
    while value:
        value, remainder = divmod(value - 1, 26)
        chars.append(chr(ord("A") + remainder))
    return "".join(reversed(chars))


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    cell_type = str(cell.attrib.get("t") or "")
    formula = cell.find(f"{{{MAIN_NS}}}f")
    value = cell.find(f"{{{MAIN_NS}}}v")
    if cell_type == "inlineStr":
        result = "".join(node.text or "" for node in cell.findall(f".//{{{MAIN_NS}}}t"))
    elif value is None:
        result = ""
    else:
        raw = value.text or ""
        if cell_type == "s":
            try:
                result = shared[int(raw)]
            except (ValueError, IndexError) as exc:
                raise DocumentExtractionError(f"XLSX sharedStrings index không hợp lệ: {raw!r}") from exc
        elif cell_type == "b":
            result = "TRUE" if raw == "1" else "FALSE"
        else:
            result = raw
    if formula is not None and (formula.text or "").strip():
        expr = "=" + (formula.text or "").strip()
        return f"{expr} [cached: {result}]" if result and result != expr else expr
    return result


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise DocumentExtractionError(f"XLSX sharedStrings.xml không parse được: {exc}") from exc
    return [
        "".join(node.text or "" for node in si.findall(f".//{{{MAIN_NS}}}t"))
        for si in root.findall(f"{{{MAIN_NS}}}si")
    ]


def _sheet_targets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    try:
        workbook_raw = archive.read("xl/workbook.xml")
        rels_raw = archive.read("xl/_rels/workbook.xml.rels")
    except KeyError as exc:
        raise DocumentExtractionError("XLSX thiếu workbook.xml hoặc workbook relationships") from exc
    try:
        workbook = ET.fromstring(workbook_raw)
        rels = ET.fromstring(rels_raw)
    except ET.ParseError as exc:
        raise DocumentExtractionError(f"XLSX workbook XML hỏng: {exc}") from exc
    relation_map: dict[str, str] = {}
    for rel in rels.findall(f"{{{PKG_REL_NS}}}Relationship"):
        rel_id = str(rel.attrib.get("Id") or "")
        target = str(rel.attrib.get("Target") or "").replace("\\", "/")
        if not rel_id or not target:
            continue
        resolved = target.lstrip("/") if target.startswith("/") else PurePosixPath("xl", target).as_posix()
        relation_map[rel_id] = _safe_zip_name(resolved)
    sheets: list[tuple[str, str]] = []
    for sheet in workbook.findall(f".//{{{MAIN_NS}}}sheet"):
        name = str(sheet.attrib.get("name") or "Sheet").strip() or "Sheet"
        rel_id = str(sheet.attrib.get(f"{{{DOC_REL_NS}}}id") or "")
        target = relation_map.get(rel_id)
        if not target:
            raise DocumentExtractionError(f"XLSX sheet {name!r} thiếu relationship target")
        sheets.append((name, target))
    if not sheets:
        raise DocumentExtractionError("XLSX không có worksheet")
    return sheets


def _sheet_markdown(archive: zipfile.ZipFile, *, sheet_name: str, target: str, shared: list[str], max_rows: int, max_columns: int) -> str:
    try:
        raw = archive.read(target)
    except KeyError as exc:
        raise DocumentExtractionError(f"XLSX thiếu worksheet target {target!r}") from exc
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise DocumentExtractionError(f"XLSX worksheet {sheet_name!r} hỏng: {exc}") from exc
    rows: dict[int, dict[int, str]] = {}
    max_col = -1
    for row in root.findall(f".//{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row"):
        row_number = int(row.attrib.get("r") or (len(rows) + 1))
        if row_number > max_rows:
            raise DocumentExtractionError(f"XLSX sheet {sheet_name!r} vượt max_rows={max_rows}")
        cells: dict[int, str] = {}
        for cell in row.findall(f"{{{MAIN_NS}}}c"):
            ref = str(cell.attrib.get("r") or "")
            match = _CELL_RE.match(ref)
            if not match:
                raise DocumentExtractionError(f"XLSX cell reference không hợp lệ: {ref!r}")
            col = _column_index(match.group(1))
            if col >= max_columns:
                raise DocumentExtractionError(f"XLSX sheet {sheet_name!r} vượt max_columns={max_columns}")
            cells[col] = _cell_text(cell, shared)
            max_col = max(max_col, col)
        rows[row_number] = cells
    if max_col < 0:
        return f"## Sheet: {sheet_name}\n\n_(empty sheet)_"
    width = max_col + 1
    header = [_column_name(index) for index in range(width)]
    lines = [
        f"## Sheet: {sheet_name}", "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row_number in sorted(rows):
        cells = rows[row_number]
        values = [str(cells.get(index, "")).replace("|", r"\|").replace("\r", " ").replace("\n", " ") for index in range(width)]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def extract_xlsx(path: Path | str, *, max_rows: int = 10_000, max_columns: int = 256, max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES) -> ExtractionResult:
    source = Path(path)
    try:
        archive = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile) as exc:
        raise DocumentExtractionError(f"XLSX không hợp lệ: {source}: {exc}") from exc
    with archive:
        _validate_zip(archive, max_uncompressed_bytes=max(1, int(max_uncompressed_bytes)))
        shared = _shared_strings(archive)
        sheets = _sheet_targets(archive)
        blocks = [_sheet_markdown(archive, sheet_name=name, target=target, shared=shared, max_rows=max(1, int(max_rows)), max_columns=max(1, int(max_columns))) for name, target in sheets]
        return ExtractionResult(
            source_format="xlsx",
            backend="stdlib-xlsx-xml",
            text="\n\n".join(blocks).strip() + "\n",
            metadata={"sheet_names": [name for name, _ in sheets]},
            warnings=("xlsx_dates_are_preserved_as_raw_serials_without_style_interpretation",),
        )


def extract_delimited(path: Path | str, *, delimiter: str, source_format: str, max_rows: int = 10_000, max_columns: int = 256) -> ExtractionResult:
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise DocumentExtractionError(f"{source_format.upper()} phải là UTF-8/UTF-8-SIG: {source}: {exc}") from exc
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows: list[list[str]] = []
    width = 0
    for index, row in enumerate(reader, start=1):
        if index > max_rows:
            raise DocumentExtractionError(f"{source_format.upper()} vượt max_rows={max_rows}")
        if len(row) > max_columns:
            raise DocumentExtractionError(f"{source_format.upper()} vượt max_columns={max_columns}")
        rows.append(row)
        width = max(width, len(row))
    if not rows:
        raise DocumentExtractionError(f"{source_format.upper()} không có dòng dữ liệu")
    width = max(width, 1)
    header = [_column_name(index) for index in range(width)]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    for row in rows:
        padded = row + [""] * (width - len(row))
        values = [str(value).replace("|", r"\|").replace("\r", " ").replace("\n", " ") for value in padded]
        lines.append("| " + " | ".join(values) + " |")
    return ExtractionResult(source_format=source_format, backend="stdlib-csv", text="\n".join(lines).strip() + "\n", metadata={"row_count": len(rows), "column_count": width}, warnings=())
