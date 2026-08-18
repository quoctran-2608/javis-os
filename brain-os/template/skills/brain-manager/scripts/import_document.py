#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from brain_os_lib.config import BrainOSConfig, BrainOSConfigError
from brain_os_lib.documents import DocumentImportError, import_document


def infer_brain_root(script_path: Path) -> Path | None:
    p = script_path.resolve(); scripts_dir=p.parent; skill_dir=scripts_dir.parent; skills_dir=skill_dir.parent
    if scripts_dir.name=="scripts" and skill_dir.name=="brain-manager" and skills_dir.name=="skills": return skills_dir.parent.resolve()
    return None

def resolve_root(value:str|None)->Path:
    if value: return Path(value).expanduser().resolve()
    inferred=infer_brain_root(Path(__file__))
    if inferred is None: raise BrainOSConfigError("Không suy được Brain root; truyền --brain-root hoặc cài script đúng template.")
    return inferred

def build_parser()->argparse.ArgumentParser:
    parser=argparse.ArgumentParser(description="Brain OS Stage 10 document normalization/import")
    parser.add_argument("path",help="PDF/DOCX/XLSX/CSV/TSV source path."); parser.add_argument("--brain-root"); parser.add_argument("--category",default="",help="Existing knowledge category id/alias."); parser.add_argument("--apply",action="store_true",help="Write Library original + normalized Markdown. Default is dry-run."); parser.add_argument("--compact",action="store_true"); return parser

def main()->int:
    parser=build_parser(); args=parser.parse_args()
    try:
        config=BrainOSConfig.load(resolve_root(args.brain_root)); result=import_document(config,args.path,category_id=str(args.category or ""),apply=bool(args.apply))
        payload={"ok":result.ok,"action":"import-document","dry_run":result.dry_run,"uses_ai":False,"executes_javis_ingest":False,"writes_wiki":False,"writes_memory":False,"moves_user_files":False,"mutates_existing_user_notes":False,"result":result.to_dict()}
        print(json.dumps(payload,ensure_ascii=False,separators=(",",":") if args.compact else None,indent=None if args.compact else 2)); return 0
    except (BrainOSConfigError,DocumentImportError,OSError,ValueError) as exc:
        payload={"ok":False,"error":f"{type(exc).__name__}: {exc}"}; print(json.dumps(payload,ensure_ascii=False,separators=(",",":") if getattr(args,"compact",False) else None,indent=None if getattr(args,"compact",False) else 2)); return 2

if __name__=="__main__": raise SystemExit(main())
