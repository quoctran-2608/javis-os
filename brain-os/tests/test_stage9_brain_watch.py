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
LOOP = BRAIN_OS_ROOT / "template" / "Javis" / "loops" / "brain-watch.md"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from brain_os_lib.config import BrainOSConfig
from brain_os_lib.db import BrainIndex
from brain_os_lib.jobs import get_job, list_jobs, set_job_status
from brain_os_lib.watch import fail_handoff_job, run_brain_watch_cycle


@pytest.fixture()
def brain(tmp_path: Path) -> Path:
    root = tmp_path / "Brain Stage 9"
    root.mkdir()
    shutil.copytree(TEMPLATE_SYSTEM, root / "System")
    return root


def _loose_note(brain: Path, name: str) -> Path:
    path = brain / name
    path.write_text(
        "# Ghi chú chưa rõ loại\n"
        "Tôi đang tổng hợp cách học, vài ý tưởng và một số ghi chú dài hạn chưa có taxonomy rõ.\n",
        encoding="utf-8",
    )
    return path


def test_watch_cycle_processes_change_claims_bounded_ai_and_preserves_note(brain: Path):
    note = _loose_note(brain, "Loose.md")
    before = note.read_bytes()
    cfg = BrainOSConfig.load(brain)

    report = run_brain_watch_cycle(cfg, max_ai_jobs=1)

    assert report.ok is True
    assert report.full_hash is True
    assert report.changes_detected == 1
    assert report.unhandled_events == 1
    assert report.handled_events == 1
    assert report.events_remaining == 0
    assert report.affected_paths == ["Loose.md"]
    assert report.should_stop is False
    assert report.stop_reason == "ai_handoff_required"
    assert len(report.handoff_jobs) == 1
    assert report.handoff_jobs[0]["status"] == "processing"
    assert report.ai_queue["claimed"] == 1
    assert note.read_bytes() == before
    assert not (brain / "wiki").exists()
    assert not (brain / "memory").exists()

    with BrainIndex(cfg.db_path) as index:
        assert int(index._require().execute(
            "SELECT COUNT(*) FROM events WHERE handled_at=''"
        ).fetchone()[0]) == 0
        processing = list_jobs(index, status="processing")
        assert [job["job_id"] for job in processing] == [report.handoff_jobs[0]["job_id"]]


def test_watch_no_change_stops_when_no_backlog(brain: Path):
    note = brain / "Notes" / "Personal" / "Learning" / "Known.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\njavis_type: living_note\njavis_category: notes_personal_learning\n---\n"
        "# Known\nStable living note.\n",
        encoding="utf-8",
    )
    cfg = BrainOSConfig.load(brain)

    first = run_brain_watch_cycle(cfg)
    assert first.ok is True
    assert first.handoff_jobs == []

    second = run_brain_watch_cycle(cfg)
    assert second.ok is True
    assert second.full_hash is False
    assert second.changes_detected == 0
    assert second.unhandled_events == 0
    assert second.handoff_jobs == []
    assert second.should_stop is True
    assert second.stop_reason == "no_changes_or_ai_backlog"


def test_watch_drains_unresolved_backlog_after_completed_job_without_new_changes(brain: Path):
    _loose_note(brain, "A.md")
    _loose_note(brain, "B.md")
    cfg = BrainOSConfig.load(brain)

    first = run_brain_watch_cycle(cfg, max_ai_jobs=1)
    assert len(first.handoff_jobs) == 1
    first_job = first.handoff_jobs[0]

    # Simulate a completed current-hash AI review. Metadata may still carry old
    # deterministic `needs_ai`; Stage 9 must not let this completed job consume
    # the next cycle's only slot and starve B.md.
    with BrainIndex(cfg.db_path) as index:
        set_job_status(index, first_job["job_id"], status="completed")

    second = run_brain_watch_cycle(cfg, max_ai_jobs=1)
    assert second.changes_detected == 0
    assert second.unhandled_events == 0
    assert len(second.handoff_jobs) == 1
    second_job = second.handoff_jobs[0]
    assert second_job["job_id"] != first_job["job_id"]
    assert second.ai_queue["skipped_reviewed_current"] >= 1
    assert second.stop_reason == "ai_handoff_required"


def test_watch_rejects_stale_processing_job_before_handoff_and_queues_new_hash(brain: Path):
    note = _loose_note(brain, "Changing.md")
    cfg = BrainOSConfig.load(brain)

    first = run_brain_watch_cycle(cfg, max_ai_jobs=1)
    old_job = first.handoff_jobs[0]
    old_hash = old_job["payload"]["source"]["content_hash"]

    note.write_text(
        note.read_text(encoding="utf-8") + "\nNội dung mới làm hash thay đổi.\n",
        encoding="utf-8",
    )
    second = run_brain_watch_cycle(cfg, max_ai_jobs=1)

    assert second.changes_detected == 1
    assert len(second.handoff_jobs) == 1
    new_job = second.handoff_jobs[0]
    assert new_job["job_id"] != old_job["job_id"]
    assert new_job["payload"]["source"]["content_hash"] != old_hash
    assert second.ai_queue["stale_failed"] >= 1
    with BrainIndex(cfg.db_path) as index:
        assert get_job(index, old_job["job_id"])["status"] == "failed"
        assert get_job(index, new_job["job_id"])["status"] == "processing"


def test_watch_external_failure_is_bounded_and_retried(brain: Path):
    _loose_note(brain, "Retry.md")
    cfg = BrainOSConfig.load(brain)

    first = run_brain_watch_cycle(cfg, max_ai_jobs=1)
    job_id = first.handoff_jobs[0]["job_id"]
    failed = fail_handoff_job(cfg, job_id, error="model unavailable")
    assert failed["status"] == "failed"
    assert failed["attempts"] == 1

    second = run_brain_watch_cycle(cfg, max_ai_jobs=1)
    assert [job["job_id"] for job in second.handoff_jobs] == [job_id]
    assert second.ai_queue["failed_requeued"] >= 1

    fail_handoff_job(cfg, job_id, error="model unavailable again")
    third = run_brain_watch_cycle(cfg, max_ai_jobs=1)
    assert [job["job_id"] for job in third.handoff_jobs] == [job_id]
    fail_handoff_job(cfg, job_id, error="third failure")

    fourth = run_brain_watch_cycle(cfg, max_ai_jobs=1)
    assert fourth.handoff_jobs == []
    assert fourth.ai_queue["failed_exhausted"] >= 1
    with BrainIndex(cfg.db_path) as index:
        exhausted = get_job(index, job_id)
        assert exhausted["status"] == "failed"
        assert exhausted["attempts"] == 3


def test_watch_recent_lock_prevents_overlapping_cycle(brain: Path):
    cfg = BrainOSConfig.load(brain)
    lock = cfg.db_path.parent / "brain-watch.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text('{"external":true}\n', encoding="utf-8")

    report = run_brain_watch_cycle(cfg)

    assert report.ok is True
    assert report.locked is True
    assert report.should_stop is True
    assert report.stop_reason == "cycle_already_running"
    assert not cfg.db_path.exists()


def test_stage9_cli_and_javis_loop_contract(brain: Path):
    _loose_note(brain, "CLI Watch.md")
    script = SCRIPTS / "brain_watch.py"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--brain-root",
            str(brain),
            "--compact",
            "cycle",
            "--max-ai-jobs",
            "1",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["scheduler_owner"] == "javis_loop"
    assert payload["uses_ai"] is False
    assert payload["writes_user_files"] is False
    assert payload["moves_user_files"] is False
    assert payload["mutates_frontmatter"] is False
    assert payload["executes_javis_ingest"] is False
    assert payload["writes_wiki"] is False
    assert payload["writes_memory"] is False
    assert len(payload["report"]["handoff_jobs"]) == 1

    loop = LOOP.read_text(encoding="utf-8")
    assert "type: loop" in loop
    assert "slug: brain-watch" in loop
    assert "enabled: false" in loop
    assert "interval_min: 5" in loop
    assert "max_runs_per_day: 288" in loop
    assert "notify: false" in loop
    assert "brain_watch.py --compact cycle" in loop
    assert "brain_manager.py --compact apply -" in loop
    assert "không trực tiếp chạy Javis INGEST" in loop
    assert "Không tạo scheduler hoặc daemon thứ hai" in loop
