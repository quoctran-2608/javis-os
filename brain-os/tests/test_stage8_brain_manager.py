from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = BRAIN_OS_ROOT / "template" / "skills" / "brain-manager" / "scripts"
TEMPLATE_SYSTEM = BRAIN_OS_ROOT / "template" / "System"
SKILL = BRAIN_OS_ROOT / "template" / "skills" / "brain-manager" / "SKILL.md"
SCHEMA_DOC = BRAIN_OS_ROOT / "template" / "skills" / "brain-manager" / "references" / "ai-output-schema.md"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from brain_os_lib.ai_manager import BrainManagerError, apply_ai_result, queue_ai_jobs
from brain_os_lib.candidates import list_candidates
from brain_os_lib.classifier import classify_brain
from brain_os_lib.config import BrainOSConfig
from brain_os_lib.db import BrainIndex
from brain_os_lib.jobs import get_job, list_jobs
from brain_os_lib.models import DocumentType, ProcessingState
from brain_os_lib.reconcile import reconcile_brain
from brain_os_lib.taxonomy import plan_brain_taxonomy


@pytest.fixture()
def brain(tmp_path: Path) -> Path:
    root = tmp_path / "Brain Stage 8"
    root.mkdir()
    shutil.copytree(TEMPLATE_SYSTEM, root / "System")
    return root


def _prepare_unresolved(brain: Path, *, name: str = "Loose.md"):
    note = brain / name
    note.write_text(
        "# Ghi chú đang phân loại\n"
        "Tôi đang tổng hợp cách học, hệ thống hoá kiến thức và vài ý tưởng dài hạn.\n",
        encoding="utf-8",
    )
    cfg = BrainOSConfig.load(brain)
    reconcile_brain(cfg, full_hash=True)
    classify_brain(cfg, force=True)
    plan_brain_taxonomy(cfg, force=True)
    queued = queue_ai_jobs(cfg)
    assert queued.queued == 1
    assert len(queued.jobs) == 1
    return cfg, note, queued.jobs[0]


def _result_for(
    job: dict,
    *,
    document_type: str = "living_note",
    category_id: str = "notes_personal_learning",
    tags: list[str] | None = None,
    route: str = "index",
    confidence: float = 0.92,
) -> dict:
    source = job["payload"]["source"]
    return {
        "schema_version": 1,
        "job_id": job["job_id"],
        "source_id": source["source_id"],
        "content_hash": source["content_hash"],
        "decision": {
            "document_type": document_type,
            "category_id": category_id,
            "canonical_tags": tags if tags is not None else ["personal/learning"],
            "route": route,
            "confidence": confidence,
            "rationale": "Nội dung là living note dài hạn; taxonomy hiện hữu phù hợp.",
        },
    }


def test_stage8_queues_only_unresolved_and_deduplicates(brain: Path):
    cfg, note, first_job = _prepare_unresolved(brain)
    before = note.read_bytes()
    again = queue_ai_jobs(cfg)
    assert again.queued == 0
    assert again.reused == 1
    assert note.read_bytes() == before
    assert first_job["job_id"] == again.jobs[0]["job_id"]
    payload = first_job["payload"]
    assert payload["contract"] == {
        "writes_user_files": False,
        "executes_javis_ingest": False,
        "writes_wiki": False,
        "writes_memory": False,
        "candidate_first": True,
    }
    assert len(payload["evidence"]["body_excerpt"]) <= 16000
    assert "notes_personal_learning" in {
        item["id"] for item in payload["constraints"]["categories"]
    }
    assert "personal/learning" in payload["constraints"]["canonical_tags"]


def test_stage8_high_confidence_commits_only_derived_state_and_routes_ingest(brain: Path):
    cfg, note, job = _prepare_unresolved(brain)
    before = note.read_bytes()
    applied = apply_ai_result(cfg, _result_for(job, route="ingest", confidence=0.94))
    assert applied.accepted is True
    assert applied.document_type == "living_note"
    assert applied.category_id == "notes_personal_learning"
    assert applied.route == "ingest"
    assert applied.next_state == "pending_ingest"
    assert applied.candidate_id == ""
    assert note.read_bytes() == before
    with BrainIndex(cfg.db_path) as index:
        item = index.get_file(applied.source_id)
        assert item is not None
        assert item.document_type == DocumentType.LIVING_NOTE
        assert item.category_id == "notes_personal_learning"
        assert item.state == ProcessingState.PENDING_INGEST
        manager = item.metadata["brain_manager"]
        assert manager["accepted"] is True
        assert manager["decision"]["canonical_tags"] == ["personal/learning"]
        assert manager["writes_user_files"] is False
        assert manager["executes_javis_ingest"] is False
        assert get_job(index, job["job_id"])["status"] == "completed"
    assert not (brain / "wiki").exists()
    assert not (brain / "memory").exists()


def test_stage8_low_confidence_stays_candidate_and_does_not_commit_type(brain: Path):
    cfg, note, job = _prepare_unresolved(brain)
    applied = apply_ai_result(cfg, _result_for(job, route="index", confidence=0.70))
    assert applied.accepted is False
    assert applied.document_type == "unknown"
    assert applied.category_id == ""
    assert applied.next_state == "unclassified"
    assert applied.candidate_id.startswith("cand_")
    with BrainIndex(cfg.db_path) as index:
        item = index.get_file(applied.source_id)
        assert item is not None
        assert item.document_type == DocumentType.UNKNOWN
        assert item.category_id == ""
        candidates = list_candidates(index, kind="ai_review", status="pending")
        assert len(candidates) == 1
        assert candidates[0]["source_id"] == applied.source_id
        assert candidates[0]["payload"]["reason"] == "below_accept_confidence"
    assert note.is_file()


def test_stage8_wiki_route_creates_candidate_not_wiki_write(brain: Path):
    cfg, _, job = _prepare_unresolved(brain)
    applied = apply_ai_result(
        cfg, _result_for(job, route="wiki_candidate", confidence=0.93)
    )
    assert applied.accepted is True
    assert applied.candidate_id.startswith("cand_")
    with BrainIndex(cfg.db_path) as index:
        candidates = list_candidates(index, kind="wiki", status="pending")
        assert len(candidates) == 1
        assert candidates[0]["payload"]["provenance"]["source_id"] == applied.source_id
    assert not (brain / "wiki").exists()


@pytest.mark.parametrize(
    ("field", "value", "error_fragment"),
    [
        ("category_id", "invented/category", "category"),
        ("canonical_tags", ["invented/tag"], "canonical tag"),
        ("document_type", "memory", "đặc quyền"),
    ],
)
def test_stage8_rejects_ai_invention_or_privileged_type(
    brain: Path, field: str, value, error_fragment: str
):
    cfg, _, job = _prepare_unresolved(brain)
    result = _result_for(job)
    result["decision"][field] = value
    with pytest.raises(BrainManagerError, match=error_fragment):
        apply_ai_result(cfg, result)
    with BrainIndex(cfg.db_path) as index:
        assert get_job(index, job["job_id"])["status"] == "failed"


def test_stage8_rejects_stale_result_after_source_changes(brain: Path):
    cfg, note, job = _prepare_unresolved(brain)
    result = _result_for(job)
    note.write_text(note.read_text(encoding="utf-8") + "\nnew line\n", encoding="utf-8")
    reconcile_brain(cfg, full_hash=True)
    with pytest.raises(BrainManagerError, match="stale"):
        apply_ai_result(cfg, result)
    with BrainIndex(cfg.db_path) as index:
        item = index.get_file(result["source_id"])
        assert item is not None
        assert item.document_type == DocumentType.UNKNOWN
        assert get_job(index, job["job_id"])["status"] == "failed"


def test_stage8_manual_index_never_enters_ai_queue(brain: Path):
    note = brain / "Manual.md"
    note.write_text(
        "---\njavis: index\n---\n# Manual index\nKhông dùng AI để nâng cấp route.\n",
        encoding="utf-8",
    )
    cfg = BrainOSConfig.load(brain)
    reconcile_brain(cfg, full_hash=True)
    classify_brain(cfg, force=True)
    plan_brain_taxonomy(cfg, force=True)
    queued = queue_ai_jobs(cfg)
    assert queued.queued == 0
    assert queued.skipped_manual == 1
    with BrainIndex(cfg.db_path) as index:
        assert list_jobs(index, status="pending") == []


def test_stage8_cli_and_skill_contract(brain: Path):
    cfg = BrainOSConfig.load(brain)
    note = brain / "CLI Loose.md"
    note.write_text("# Loose\nNo deterministic signal.\n", encoding="utf-8")
    reconcile_brain(cfg, full_hash=True)
    classify_brain(cfg, force=True)
    plan_brain_taxonomy(cfg, force=True)
    script = SCRIPTS / "brain_manager.py"
    proc = subprocess.run(
        [
            sys.executable, str(script), "--brain-root", str(brain),
            "--compact", "queue", "--limit", "2",
        ],
        check=False, capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["uses_ai"] is False
    assert payload["writes_user_files"] is False
    assert payload["report"]["queued"] == 1
    skill = SKILL.read_text(encoding="utf-8")
    schema = SCHEMA_DOC.read_text(encoding="utf-8")
    assert "brain_manager.py queue" in skill
    assert "brain_manager.py apply" in skill
    assert "không được tự ghi Wiki" in skill
    assert "schema_version" in schema
    assert "wiki_candidate" in schema
