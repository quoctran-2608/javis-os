from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


BRAIN_OS_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = BRAIN_OS_ROOT / "template" / "skills" / "brain-manager" / "scripts"
TEMPLATE_SYSTEM = BRAIN_OS_ROOT / "template" / "System"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from brain_os_lib.amplenote import AmplenoteMigrationError, migrate_amplenote
from brain_os_lib.config import BrainOSConfig
from brain_os_lib.frontmatter import load_markdown
from brain_os_lib.importer import MarkdownImportError
from brain_os_lib.originals import sha256_file


@pytest.fixture()
def brain(tmp_path: Path) -> Path:
    root = tmp_path / "Brain Amplenote"
    root.mkdir()
    shutil.copytree(TEMPLATE_SYSTEM, root / "System")
    return root


def _export_note(
    root: Path,
    *,
    name: str = "ĐIỀU TÔI HỌC ĐƯỢC.md",
    tags: tuple[str, ...] = ("dieutoihocduoc", "mylife"),
    body: str = "# ĐIỀU TÔI HỌC ĐƯỢC\nMột living note dài theo thời gian.\n",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    yaml_tags = "".join(f"  - {tag}\n" for tag in tags)
    path.write_text(
        "---\n"
        "title: ĐIỀU TÔI HỌC ĐƯỢC\n"
        "uuid: amplenote-fixture-123\n"
        "created: '2025-01-02T03:04:05Z'\n"
        "tags:\n"
        f"{yaml_tags}"
        "---\n"
        f"{body}",
        encoding="utf-8",
    )
    return path


def test_amplenote_dry_run_routes_fixture_and_writes_nothing(
    brain: Path, tmp_path: Path
):
    export = tmp_path / "amplenote-export"
    source = _export_note(export)
    before = source.read_bytes()
    cfg = BrainOSConfig.load(brain)

    report = migrate_amplenote(cfg, export, apply=False)

    assert report.ok is True
    assert report.dry_run is True
    assert report.source_kind == "directory"
    assert report.discovered_notes == 1
    assert report.migrated_notes == 1
    assert source.read_bytes() == before
    assert not (brain / ".javis").exists()

    plan = report.notes[0]
    assert plan["title"] == "ĐIỀU TÔI HỌC ĐƯỢC"
    assert plan["document_type"] == "living_note"
    assert plan["category_id"] == "notes_personal_learning"
    assert plan["working_path"] == "Notes/Personal/Learning/ĐIỀU TÔI HỌC ĐƯỢC.md"
    assert plan["raw_tags"] == ["dieutoihocduoc", "mylife"]
    assert plan["canonical_tags"] == ["personal/learning", "personal/life"]
    assert plan["legacy_tags"] == ["dieutoihocduoc", "mylife"]
    assert not (brain / plan["working_path"]).exists()


def test_amplenote_apply_preserves_original_and_normalizes_working_metadata(
    brain: Path, tmp_path: Path
):
    export = tmp_path / "amplenote-export"
    source = _export_note(export)
    original = source.read_bytes()
    cfg = BrainOSConfig.load(brain)

    report = migrate_amplenote(cfg, export, apply=True)
    item = report.notes[0]

    working = brain / item["working_path"]
    parsed = load_markdown(working)
    assert parsed.metadata["javis_id"] == item["source_id"]
    assert parsed.metadata["javis_type"] == "living_note"
    assert parsed.metadata["javis_category"] == "notes_personal_learning"
    assert parsed.metadata["origin"] == "amplenote_import"
    assert parsed.metadata["tags"] == ["personal/learning", "personal/life"]
    assert parsed.metadata["legacy_tags"] == ["dieutoihocduoc", "mylife"]
    assert parsed.metadata["uuid"] == "amplenote-fixture-123"
    assert parsed.metadata["created"] == "2025-01-02T03:04:05Z"
    assert "Một living note dài theo thời gian." in parsed.body

    manifests = list((brain / ".javis" / "originals" / "imports").glob("*/manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    snapshot = manifests[0].parent / "original.md"
    assert snapshot.read_bytes() == original
    assert sha256_file(snapshot) == manifest["source_sha256"]
    assert manifest["migration_provenance"] == [
        {
            "source_system": "amplenote",
            "source_entry": "ĐIỀU TÔI HỌC ĐƯỢC.md",
        }
    ]

    assert list((brain / "Notes" / "Personal" / "Learning").glob("*.md")) == [working]


def test_amplenote_reimport_is_idempotent_and_does_not_overwrite_user_edits(
    brain: Path, tmp_path: Path
):
    export = tmp_path / "amplenote-export"
    _export_note(export)
    cfg = BrainOSConfig.load(brain)

    first = migrate_amplenote(cfg, export, apply=True)
    item1 = first.notes[0]
    working = brain / item1["working_path"]
    working.write_text(
        working.read_text(encoding="utf-8") + "\nUser edit after migration.\n",
        encoding="utf-8",
    )
    edited = working.read_bytes()

    second = migrate_amplenote(cfg, export, apply=True)
    item2 = second.notes[0]

    assert item2["source_id"] == item1["source_id"]
    assert item2["working_path"] == item1["working_path"]
    assert item2["reused_snapshot"] is True
    assert item2["reused_working_copy"] is True
    assert second.reused_notes == 1
    assert working.read_bytes() == edited
    assert len(list((brain / ".javis" / "originals" / "imports").glob("*/manifest.json"))) == 1


def test_unknown_amplenote_tags_are_preserved_as_legacy_not_invented_as_canonical(
    brain: Path, tmp_path: Path
):
    export = tmp_path / "amplenote-export"
    _export_note(
        export,
        tags=("dieutoihocduoc", "very-old-custom-tag"),
    )
    cfg = BrainOSConfig.load(brain)

    report = migrate_amplenote(cfg, export, apply=True)
    parsed = load_markdown(brain / report.notes[0]["working_path"])

    assert parsed.metadata["tags"] == ["personal/learning"]
    assert parsed.metadata["legacy_tags"] == [
        "dieutoihocduoc",
        "very-old-custom-tag",
    ]


def test_zip_export_is_hash_preserved_and_assets_are_not_silently_lost(
    brain: Path, tmp_path: Path
):
    fixture_root = tmp_path / "fixture"
    note = _export_note(fixture_root / "notes")
    archive = tmp_path / "amplenote.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(note, arcname="notes/ĐIỀU TÔI HỌC ĐƯỢC.md")
        zf.writestr("media/amplenote-fixture-123/image.png", b"\x89PNG\r\nfixture")

    cfg = BrainOSConfig.load(brain)
    report = migrate_amplenote(cfg, archive, apply=True)

    assert report.source_kind == "zip"
    assert report.skipped_assets == 1
    assert report.archive_sha256 == sha256_file(archive)
    snapshot = Path(report.archive_snapshot_path)
    assert snapshot.is_file()
    assert sha256_file(snapshot) == report.archive_sha256
    assert report.warnings
    assert (brain / report.notes[0]["working_path"]).is_file()

    manifest = json.loads(
        next((brain / ".javis" / "originals" / "imports").glob("*/manifest.json")).read_text(
            encoding="utf-8"
        )
    )
    assert manifest["migration_provenance"][0]["source_entry"] == (
        "notes/ĐIỀU TÔI HỌC ĐƯỢC.md"
    )
    assert manifest["migration_provenance"][0]["export_sha256"] == report.archive_sha256


def test_zip_path_traversal_fails_closed_before_any_brain_write(
    brain: Path, tmp_path: Path
):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../outside.md", "# no\n")

    cfg = BrainOSConfig.load(brain)
    with pytest.raises(AmplenoteMigrationError):
        migrate_amplenote(cfg, archive, apply=True)

    assert not (brain / ".javis").exists()
    assert not (tmp_path / "outside.md").exists()


def test_batch_preflight_fails_before_writes_when_one_note_is_invalid(
    brain: Path, tmp_path: Path
):
    export = tmp_path / "amplenote-export"
    _export_note(export, name="Good.md")
    (export / "Bad.md").write_text(
        "---\ntags: [broken\n---\n# bad\n",
        encoding="utf-8",
    )
    cfg = BrainOSConfig.load(brain)

    with pytest.raises(MarkdownImportError):
        migrate_amplenote(cfg, export, apply=True)

    assert not (brain / ".javis").exists()
    assert not (brain / "Notes").exists()
    assert not (brain / "sources").exists()


def test_cli_defaults_to_dry_run_and_apply_is_explicit(brain: Path, tmp_path: Path):
    export = tmp_path / "amplenote-export"
    _export_note(export)
    script = SCRIPTS / "import_amplenote.py"

    preview = subprocess.run(
        [
            sys.executable,
            str(script),
            str(export),
            "--brain-root",
            str(brain),
            "--compact",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert preview.returncode == 0, preview.stderr
    preview_data = json.loads(preview.stdout)
    assert preview_data["dry_run"] is True
    assert preview_data["report"]["discovered_notes"] == 1
    assert not (brain / ".javis").exists()

    applied = subprocess.run(
        [
            sys.executable,
            str(script),
            str(export),
            "--brain-root",
            str(brain),
            "--apply",
            "--compact",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert applied.returncode == 0, applied.stderr
    applied_data = json.loads(applied.stdout)
    assert applied_data["dry_run"] is False
    assert applied_data["uses_ai"] is False
    assert applied_data["executes_javis_ingest"] is False
    assert (brain / applied_data["report"]["notes"][0]["working_path"]).is_file()
