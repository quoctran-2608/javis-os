from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .classifier import classify_brain
from .config import BrainOSConfig
from .frontmatter import update_frontmatter
from .importer import ImportResult, import_markdown
from .originals import sha256_file
from .reconcile import reconcile_brain
from .taxonomy import TaxonomyRegistry, plan_brain_taxonomy
from .extractors import DocumentExtractionError, ExtractionResult
from .extractors.docx import extract_docx
from .extractors.pdf import extract_pdf
from .extractors.sheets import extract_delimited, extract_xlsx

DOCUMENT_VERSION = 1
SUPPORTED_SUFFIXES = {".pdf":"pdf",".docx":"docx",".xlsx":"xlsx",".csv":"csv",".tsv":"tsv"}
DEFAULT_MAX_FILE_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_NORMALIZED_BYTES = 20 * 1024 * 1024

class DocumentImportError(RuntimeError):
    """Fail-safe error for Stage 10 document normalization."""

@dataclass(frozen=True)
class DocumentImportResult:
    ok: bool
    dry_run: bool
    source_id: str
    source_sha256: str
    source_format: str
    extraction_backend: str
    library_path: str
    normalized_working_path: str
    normalized_snapshot_path: str
    normalized_manifest_path: str
    reused_original: bool
    reused_normalized_source: bool
    indexed: bool
    warnings: tuple[str, ...] = ()
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self); data["warnings"] = list(self.warnings); return data

def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def _document_options(config: BrainOSConfig) -> dict[str, int]:
    raw = config.core.get("documents") or {}
    return {
        "max_file_bytes": max(1,int(raw.get("max_file_bytes",DEFAULT_MAX_FILE_BYTES))),
        "max_normalized_bytes": max(1024,int(raw.get("max_normalized_bytes",DEFAULT_MAX_NORMALIZED_BYTES))),
        "max_pdf_pages": max(1,int(raw.get("max_pdf_pages",1000))),
        "max_docx_uncompressed_bytes": max(1024,int(raw.get("max_docx_uncompressed_bytes",512*1024*1024))),
        "max_xlsx_uncompressed_bytes": max(1024,int(raw.get("max_xlsx_uncompressed_bytes",1024*1024*1024))),
        "max_sheet_rows": max(1,int(raw.get("max_sheet_rows",10000))),
        "max_sheet_columns": max(1,int(raw.get("max_sheet_columns",256))),
    }

def _source_format(source: Path) -> str:
    fmt = SUPPORTED_SUFFIXES.get(source.suffix.casefold())
    if not fmt:
        raise DocumentImportError(f"Stage 10 chỉ hỗ trợ PDF/DOCX/XLSX/CSV/TSV; không nhận {source.name!r}.")
    return fmt

def _stable_source_id(source_hash: str, source_format: str) -> str:
    digest = hashlib.sha256(f"{source_format}:{source_hash}".encode("ascii")).hexdigest()
    return f"src_{digest[:20]}"

def _document_root(config: BrainOSConfig, source_id: str) -> Path:
    return config.brain_root / ".javis" / "originals" / "documents" / source_id

def _document_manifest_path(config: BrainOSConfig, source_id: str) -> Path:
    return _document_root(config, source_id) / "manifest.json"

def _library_rel(config: BrainOSConfig, source_id: str, source_format: str) -> str:
    library = str((config.core.get("paths") or {}).get("library") or "Library")
    return PurePosixPath(library,"Documents",source_id,f"original.{source_format}").as_posix()

def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp_name=tempfile.mkstemp(prefix=f".{path.name}.brain-os-",suffix=".tmp",dir=str(path.parent)); tmp=Path(temp_name)
    try:
        with os.fdopen(fd,"w",encoding="utf-8",newline="\n") as fh:
            json.dump(payload,fh,ensure_ascii=False,indent=2,sort_keys=True); fh.write("\n"); fh.flush(); os.fsync(fh.fileno())
        os.replace(tmp,path)
    finally:
        try: tmp.unlink(missing_ok=True)
        except OSError: pass

def _atomic_copy(source: Path,target: Path,expected_hash: str)->bool:
    if target.is_symlink(): raise DocumentImportError(f"Library original không được là symlink: {target}")
    if target.exists():
        if not target.is_file(): raise DocumentImportError(f"Library original path không phải file: {target}")
        actual=sha256_file(target)
        if actual!=expected_hash: raise DocumentImportError(f"Library original immutable bị thay đổi: {target}; expected={expected_hash} actual={actual}")
        return True
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,temp_name=tempfile.mkstemp(prefix=f".{target.name}.brain-os-",suffix=".tmp",dir=str(target.parent)); tmp=Path(temp_name)
    try:
        with source.open("rb") as src,os.fdopen(fd,"wb") as dst:
            shutil.copyfileobj(src,dst,length=1024*1024); dst.flush(); os.fsync(dst.fileno())
        if sha256_file(tmp)!=expected_hash: raise DocumentImportError(f"Copy Library original không khớp SHA-256: {source}")
        os.replace(tmp,target)
    finally:
        try: tmp.unlink(missing_ok=True)
        except OSError: pass
    return False

def _load_document_manifest(config:BrainOSConfig,*,source_id:str,source_hash:str,source_format:str)->dict[str,Any]|None:
    manifest=_document_manifest_path(config,source_id)
    if not manifest.exists():
        root=manifest.parent
        if root.exists() and any(root.iterdir()): raise DocumentImportError(f"Document provenance incomplete/corrupt: thiếu {manifest}")
        return None
    try: payload=json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc: raise DocumentImportError(f"Document manifest hỏng: {manifest}: {exc}") from exc
    if not isinstance(payload,dict): raise DocumentImportError(f"Document manifest phải là object: {manifest}")
    if str(payload.get("source_id") or "")!=source_id: raise DocumentImportError(f"Document manifest sai source_id: {manifest}")
    if str(payload.get("source_sha256") or "")!=source_hash: raise DocumentImportError(f"Document manifest sai source_sha256: {manifest}")
    if str(payload.get("source_format") or "")!=source_format: raise DocumentImportError(f"Document manifest sai source_format: {manifest}")
    library_rel=str(payload.get("library_path") or "")
    if not library_rel: raise DocumentImportError(f"Document manifest thiếu library_path: {manifest}")
    library_path=config.brain_root/PurePosixPath(library_rel)
    if not library_path.is_file(): raise DocumentImportError(f"Library original bị thiếu: {library_path}")
    actual=sha256_file(library_path)
    if actual!=source_hash: raise DocumentImportError(f"Library original immutable bị thay đổi: {library_path}; expected={source_hash} actual={actual}")
    normalized_snapshot=config.brain_root/Path(str(payload.get("normalized_snapshot_path") or "")); normalized_hash=str(payload.get("normalized_source_sha256") or "")
    if not normalized_snapshot.is_file() or len(normalized_hash)!=64: raise DocumentImportError(f"Document manifest thiếu normalized provenance hợp lệ: {manifest}")
    actual_normalized=sha256_file(normalized_snapshot)
    if actual_normalized!=normalized_hash: raise DocumentImportError(f"Normalized immutable snapshot bị thay đổi: {normalized_snapshot}; expected={normalized_hash} actual={actual_normalized}")
    return payload

def _extract(source:Path,source_format:str,*,options:dict[str,int])->ExtractionResult:
    try:
        if source_format=="pdf": return extract_pdf(source,max_pages=options["max_pdf_pages"])
        if source_format=="docx": return extract_docx(source,max_uncompressed_bytes=options["max_docx_uncompressed_bytes"])
        if source_format=="xlsx": return extract_xlsx(source,max_rows=options["max_sheet_rows"],max_columns=options["max_sheet_columns"],max_uncompressed_bytes=options["max_xlsx_uncompressed_bytes"])
        if source_format=="csv": return extract_delimited(source,delimiter=",",source_format="csv",max_rows=options["max_sheet_rows"],max_columns=options["max_sheet_columns"])
        if source_format=="tsv": return extract_delimited(source,delimiter="\t",source_format="tsv",max_rows=options["max_sheet_rows"],max_columns=options["max_sheet_columns"])
    except DocumentExtractionError: raise
    raise DocumentImportError(f"Không có extractor cho format {source_format!r}")

def _safe_title(value:Any,fallback:str)->str:
    text=str(value or "").replace("\x00","").strip(); return text or fallback

def _safe_markdown_filename(title:str,source_id:str)->str:
    value=re.sub(r'[\\/:*?"<>|\x00-\x1f]+'," ",title).strip(" ."); value=re.sub(r"\s+"," ",value)
    if not value: value=f"Document {source_id[-8:]}"
    return f"{value[:120].rstrip(' .')}.md"

def _normalized_markdown(*,source:Path,source_id:str,source_hash:str,source_format:str,library_rel:str,extraction:ExtractionResult)->tuple[str,str]:
    title=_safe_title(extraction.metadata.get("title"),source.stem)
    metadata={"javis_id":source_id,"javis_type":"reference_source","origin":"document_import","source_format":source_format,"source_sha256":source_hash,"library_original":library_rel,"extraction_backend":extraction.backend,"extraction_version":DOCUMENT_VERSION}
    if extraction.metadata: metadata["document_metadata"]=extraction.metadata
    if extraction.warnings: metadata["extraction_warnings"]=list(extraction.warnings)
    frontmatter=yaml.safe_dump(metadata,allow_unicode=True,sort_keys=False,default_flow_style=False).rstrip(); body=extraction.text.strip()
    return _safe_markdown_filename(title,source_id),f"---\n{frontmatter}\n---\n\n# {title}\n\n{body}\n"

def _refresh_after_origin_update(config:BrainOSConfig,working_path:str)->None:
    reconcile_brain(config,full_hash=True); classify_brain(config,paths={working_path}); plan_brain_taxonomy(config,paths={working_path})

def _result_from_existing(config:BrainOSConfig,payload:dict[str,Any],*,dry_run:bool)->DocumentImportResult:
    snapshot_path=config.brain_root/Path(str(payload["normalized_snapshot_path"])); import_result=import_markdown(config,snapshot_path,document_type="reference_source",category_id=str(payload.get("category_id") or ""),dry_run=dry_run)
    if not dry_run and not import_result.reused_working_copy:
        update_frontmatter(config.brain_root/import_result.working_path,updates={"origin":"document_import"},dry_run=False); _refresh_after_origin_update(config,import_result.working_path)
    return DocumentImportResult(True,dry_run,str(payload["source_id"]),str(payload["source_sha256"]),str(payload["source_format"]),str(payload.get("extraction_backend") or ""),str(payload["library_path"]),import_result.working_path,import_result.snapshot_path,import_result.manifest_path,True,True,import_result.indexed,tuple(payload.get("warnings") or ()))

def import_document(config:BrainOSConfig,source_path:Path|str,*,category_id:str="",apply:bool=False)->DocumentImportResult:
    raw_source=Path(source_path).expanduser()
    if raw_source.is_symlink(): raise DocumentImportError(f"Document source symlink bị từ chối fail-closed: {raw_source}")
    source=raw_source.resolve()
    if not source.is_file(): raise DocumentImportError(f"Không tìm thấy document source: {source}")
    source_format=_source_format(source); options=_document_options(config); size=source.stat().st_size
    if size>options["max_file_bytes"]: raise DocumentImportError(f"Document vượt max_file_bytes: {size}>{options['max_file_bytes']}")
    source_hash=sha256_file(source); source_id=_stable_source_id(source_hash,source_format); effective_dry_run=not bool(apply)
    existing=_load_document_manifest(config,source_id=source_id,source_hash=source_hash,source_format=source_format)
    if existing is not None:
        requested=str(category_id or "").strip(); existing_category=str(existing.get("category_id") or "").strip()
        if requested and requested!=existing_category:
            registry=TaxonomyRegistry.from_config(config); resolved=registry.resolve_category("knowledge",requested)
            if resolved is None or resolved.id!=existing_category: raise DocumentImportError("Exact document đã có provenance/category ổn định; Stage 10 không re-route silently khi re-import.")
        return _result_from_existing(config,existing,dry_run=effective_dry_run)
    requested_category=str(category_id or "").strip(); resolved_category=""
    if requested_category:
        registry=TaxonomyRegistry.from_config(config); category=registry.resolve_category("knowledge",requested_category)
        if category is None: raise DocumentImportError(f"Category {requested_category!r} không tồn tại trong knowledge taxonomy.")
        resolved_category=category.id
    try: extraction=_extract(source,source_format,options=options)
    except DocumentExtractionError as exc: raise DocumentImportError(str(exc)) from exc
    library_rel=_library_rel(config,source_id,source_format); filename,normalized=_normalized_markdown(source=source,source_id=source_id,source_hash=source_hash,source_format=source_format,library_rel=library_rel,extraction=extraction); normalized_bytes=normalized.encode("utf-8")
    if len(normalized_bytes)>options["max_normalized_bytes"]: raise DocumentImportError(f"Normalized Markdown vượt max_normalized_bytes: {len(normalized_bytes)}>{options['max_normalized_bytes']}")
    normalized_hash=hashlib.sha256(normalized_bytes).hexdigest(); stage6_manifest=config.brain_root/".javis"/"originals"/"imports"/source_id/"manifest.json"
    if stage6_manifest.exists():
        try: stage6_payload=json.loads(stage6_manifest.read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError) as exc: raise DocumentImportError(f"Stage 6 provenance manifest hỏng cho {source_id}: {exc}") from exc
        prior_hash=str((stage6_payload or {}).get("source_sha256") or "")
        if prior_hash and prior_hash!=normalized_hash: raise DocumentImportError(f"Stable identity collision trước Library write: {source_id}")
    with tempfile.TemporaryDirectory(prefix="brain-os-document-") as tmpdir:
        normalized_source=Path(tmpdir)/filename; normalized_source.write_bytes(normalized_bytes)
        preview=import_markdown(config,normalized_source,document_type="reference_source",category_id=resolved_category,dry_run=True)
        if effective_dry_run:
            return DocumentImportResult(True,True,source_id,source_hash,source_format,extraction.backend,library_rel,preview.working_path,preview.snapshot_path,preview.manifest_path,False,False,False,extraction.warnings)
        library_path=config.brain_root/PurePosixPath(library_rel); reused_original=_atomic_copy(source,library_path,source_hash)
        imported:ImportResult=import_markdown(config,normalized_source,document_type="reference_source",category_id=resolved_category,dry_run=False)
        if imported.source_id!=source_id: raise DocumentImportError(f"Normalized source identity mismatch: {imported.source_id}!={source_id}")
        if not imported.reused_working_copy:
            update_frontmatter(config.brain_root/imported.working_path,updates={"origin":"document_import"},dry_run=False); _refresh_after_origin_update(config,imported.working_path)
        payload={"schema_version":1,"document_version":DOCUMENT_VERSION,"source_id":source_id,"source_sha256":source_hash,"source_format":source_format,"original_name":source.name,"library_path":library_rel,"imported_at":_utc_now(),"extraction_backend":extraction.backend,"extraction_metadata":extraction.metadata,"warnings":list(extraction.warnings),"category_id":imported.category_id,"normalized_source_sha256":imported.source_sha256,"normalized_working_path":imported.working_path,"normalized_snapshot_path":imported.snapshot_path,"normalized_manifest_path":imported.manifest_path}
        _atomic_write_json(_document_manifest_path(config,source_id),payload)
        return DocumentImportResult(True,False,source_id,source_hash,source_format,extraction.backend,library_rel,imported.working_path,imported.snapshot_path,imported.manifest_path,reused_original,imported.reused_snapshot,True,extraction.warnings)
