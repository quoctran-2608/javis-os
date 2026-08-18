from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

BRAIN_OS_ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=BRAIN_OS_ROOT/"template"/"skills"/"brain-manager"/"scripts"
TEMPLATE_SYSTEM=BRAIN_OS_ROOT/"template"/"System"
if str(SCRIPTS) not in sys.path: sys.path.insert(0,str(SCRIPTS))

from brain_os_lib.config import BrainOSConfig
from brain_os_lib.documents import DocumentImportError, import_document
from brain_os_lib.frontmatter import load_markdown
from brain_os_lib.originals import sha256_file

@pytest.fixture()
def brain(tmp_path:Path)->Path:
    root=tmp_path/"Brain Stage 10"; root.mkdir(); shutil.copytree(TEMPLATE_SYSTEM,root/"System"); return root

def _pdf_bytes(text:str="Hello PDF")->bytes:
    stream=f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects=[b"<< /Type /Catalog /Pages 2 0 R >>",b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",b"<< /Length "+str(len(stream)).encode("ascii")+b" >>\nstream\n"+stream+b"\nendstream"]
    output=bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"); offsets=[0]
    for index,body in enumerate(objects,start=1): offsets.append(len(output)); output.extend(f"{index} 0 obj\n".encode("ascii")); output.extend(body); output.extend(b"\nendobj\n")
    xref=len(output); output.extend(f"xref\n0 {len(objects)+1}\n".encode("ascii")); output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]: output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend((f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode("ascii")); return bytes(output)

def _write_docx(path:Path)->None:
    document='''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Xin chào DOCX</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Mã</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>001</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'''.encode("utf-8")
    core='''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Tài liệu DOCX mẫu</dc:title><dc:creator>Brain OS Test</dc:creator></cp:coreProperties>'''.encode("utf-8")
    with zipfile.ZipFile(path,"w",compression=zipfile.ZIP_DEFLATED) as zf: zf.writestr("word/document.xml",document); zf.writestr("docProps/core.xml",core)

def _write_xlsx(path:Path)->None:
    workbook='''<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>'''.encode("utf-8")
    rels='''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'''.encode("utf-8")
    shared='''<?xml version="1.0" encoding="UTF-8"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="2" uniqueCount="2"><si><t>Tên</t></si><si><t>Linh</t></si></sst>'''.encode("utf-8")
    sheet='''<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>100</v></c></row><row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><f>50+50</f><v>100</v></c></row></sheetData></worksheet>'''.encode("utf-8")
    with zipfile.ZipFile(path,"w",compression=zipfile.ZIP_DEFLATED) as zf: zf.writestr("xl/workbook.xml",workbook); zf.writestr("xl/_rels/workbook.xml.rels",rels); zf.writestr("xl/sharedStrings.xml",shared); zf.writestr("xl/worksheets/sheet1.xml",sheet)

def test_pdf_dry_run_writes_nothing_and_apply_preserves_original(brain:Path,tmp_path:Path):
    source=tmp_path/"Sample PDF.pdf"; source.write_bytes(_pdf_bytes("Hello PDF")); before=source.read_bytes(); cfg=BrainOSConfig.load(brain)
    preview=import_document(cfg,source,apply=False); assert preview.dry_run is True; assert preview.extraction_backend=="pypdf"; assert source.read_bytes()==before; assert not (brain/".javis").exists(); assert not (brain/"Library").exists(); assert not (brain/"sources").exists()
    result=import_document(cfg,source,apply=True); library=brain/result.library_path; working=brain/result.normalized_working_path; assert library.read_bytes()==before; parsed=load_markdown(working); assert parsed.metadata["javis_id"]==result.source_id; assert parsed.metadata["origin"]=="document_import"; assert parsed.metadata["source_format"]=="pdf"; assert "Hello PDF" in parsed.body; assert not (brain/"wiki").exists(); assert not (brain/"memory").exists()

def test_docx_extracts_paragraph_table_and_internal_title(brain:Path,tmp_path:Path):
    source=tmp_path/"document.docx"; _write_docx(source); result=import_document(BrainOSConfig.load(brain),source,apply=True); parsed=load_markdown(brain/result.normalized_working_path); assert "Xin chào DOCX" in parsed.body; assert "| Mã | 001 |" in parsed.body; assert "# Tài liệu DOCX mẫu" in parsed.body; assert parsed.metadata["document_metadata"]["creator"]=="Brain OS Test"

def test_xlsx_and_csv_are_normalized(brain:Path,tmp_path:Path):
    xlsx=tmp_path/"book.xlsx"; _write_xlsx(xlsx); cfg=BrainOSConfig.load(brain); result=import_document(cfg,xlsx,apply=True); parsed=load_markdown(brain/result.normalized_working_path); assert "## Sheet: Data" in parsed.body; assert "| Tên | 100 |" in parsed.body; assert "| Linh | =50+50 [cached: 100] |" in parsed.body
    csv_source=tmp_path/"data.csv"; csv_source.write_text("name,amount\nAlice,12\n",encoding="utf-8"); csv_result=import_document(cfg,csv_source,apply=True); csv_parsed=load_markdown(brain/csv_result.normalized_working_path); assert "| name | amount |" in csv_parsed.body; assert "| Alice | 12 |" in csv_parsed.body

def test_exact_reimport_reuses_identity_and_preserves_user_edits(brain:Path,tmp_path:Path):
    source=tmp_path/"first.docx"; _write_docx(source); cfg=BrainOSConfig.load(brain); first=import_document(cfg,source,apply=True); working=brain/first.normalized_working_path; working.write_text(working.read_text(encoding="utf-8")+"\nUser edit after normalization.\n",encoding="utf-8"); edited=working.read_bytes(); renamed=tmp_path/"renamed.docx"; renamed.write_bytes(source.read_bytes()); second=import_document(cfg,renamed,apply=True); assert second.source_id==first.source_id; assert second.reused_original is True; assert second.reused_normalized_source is True; assert working.read_bytes()==edited; assert len(list((brain/".javis"/"originals"/"documents").glob("*/manifest.json")))==1

def test_tampered_library_original_fails_closed(brain:Path,tmp_path:Path):
    source=tmp_path/"tamper.docx"; _write_docx(source); cfg=BrainOSConfig.load(brain); first=import_document(cfg,source,apply=True); library=brain/first.library_path; library.write_bytes(b"tampered"); before=(brain/first.normalized_working_path).read_bytes()
    with pytest.raises(DocumentImportError,match="Library original immutable"): import_document(cfg,source,apply=True)
    assert (brain/first.normalized_working_path).read_bytes()==before

def test_malformed_office_archive_fails_before_brain_write(brain:Path,tmp_path:Path):
    source=tmp_path/"evil.docx"
    with zipfile.ZipFile(source,"w") as zf: zf.writestr("../outside.xml","<x/>"); zf.writestr("word/document.xml",'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>')
    with pytest.raises(DocumentImportError,match="path không an toàn"): import_document(BrainOSConfig.load(brain),source,apply=True)
    assert not (brain/".javis").exists(); assert not (brain/"Library").exists()

def test_unsupported_format_and_unknown_category_fail_without_write(brain:Path,tmp_path:Path):
    cfg=BrainOSConfig.load(brain); old_xls=tmp_path/"legacy.xls"; old_xls.write_bytes(b"not-supported")
    with pytest.raises(DocumentImportError,match="chỉ hỗ trợ"): import_document(cfg,old_xls,apply=True)
    docx=tmp_path/"category.docx"; _write_docx(docx)
    with pytest.raises(DocumentImportError,match="không tồn tại"): import_document(cfg,docx,category_id="invented/category",apply=True)
    assert not (brain/".javis").exists()

def test_stage10_cli_defaults_to_dry_run_and_apply_is_explicit(brain:Path,tmp_path:Path):
    source=tmp_path/"cli.docx"; _write_docx(source); script=SCRIPTS/"import_document.py"; preview=subprocess.run([sys.executable,str(script),str(source),"--brain-root",str(brain),"--compact"],check=False,capture_output=True,text=True,encoding="utf-8"); assert preview.returncode==0,preview.stderr; payload=json.loads(preview.stdout); assert payload["dry_run"] is True; assert payload["uses_ai"] is False; assert payload["executes_javis_ingest"] is False; assert not (brain/".javis").exists(); applied=subprocess.run([sys.executable,str(script),str(source),"--brain-root",str(brain),"--compact","--apply"],check=False,capture_output=True,text=True,encoding="utf-8"); assert applied.returncode==0,applied.stderr; applied_payload=json.loads(applied.stdout); assert (brain/applied_payload["result"]["normalized_working_path"]).is_file()
