"""Focused regression tests for Second Brain managed Markdown.

Run manually / CI:

    python tests/run.py managed_markdown -v

These tests intentionally cover only the critical Gate behavior:
- canonical hash ignores Javis bookkeeping but sees user edits;
- scanning is restricted to managed Markdown/user roots and ignores system areas;
- mark is path-safe and an immediate second scan is unchanged;
- marking preserves the user Markdown body, including leading indentation;
- malformed top frontmatter fails closed instead of being silently rewritten;
- setup creates exactly one persistent refresh loop without overwriting user edits;
- scheduled refresh with no changes returns BEFORE prompt/CLI/model construction (zero AI);
- a real body edit reaches the AI path and injects only the changed note path.
"""
from _paths import ROOT, SERVER, SYSTEM  # noqa: E402,F401

import asyncio
import importlib.util
import json
import tempfile
from pathlib import Path

import yaml

import managed_markdown
from self_improve import LoopDeps, LoopFeature

fails = []


def check(name, cond):
    print(("  OK   " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


def atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def read_body(path):
    _fm, body, _had = managed_markdown._parse(Path(path).read_text(encoding="utf-8"))
    return body


def render_with_body(fm, body):
    rendered = yaml.safe_dump(
        fm, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000
    ).strip()
    return f"---\n{rendered}\n---\n{body}"


with tempfile.TemporaryDirectory(prefix="javis-managed-md-") as td:
    base = Path(td)
    vault = base / "Brain"
    state = base / "state"
    sources = vault / "06 - Sources" / "Công việc" / "Kế toán"
    sources.mkdir(parents=True)
    state.mkdir(parents=True)

    note = sources / "hoa-don.md"
    # Leading blank line + indented code are intentional: mark must preserve them exactly.
    original = "---\ntags:\n  - hoa-don\ncategory: accounting\n---\n\n    code block đầu note\nNội dung gốc.\n"
    note.write_text(original, encoding="utf-8")
    original_body = read_body(note)

    # ---- mark + immediate unchanged ----
    marked = managed_markdown.mark_ingested(str(vault), "06 - Sources/Công việc/Kế toán/hoa-don.md")
    check("mark tra canonical sha256", len(marked.get("hash", "")) == 64)
    check("mark khong lam mat/thay body nguoi dung", read_body(note) == original_body)
    check("mark giu nguyen indentation dau body", read_body(note).startswith("\n    code block đầu note"))

    scan = managed_markdown.scan_managed_notes(str(vault))
    check("scan ngay sau mark = unchanged", scan["changed_count"] == 0)
    check("scan nhan managed note", scan["managed_markdown"] == 1)

    # ---- operational metadata does not self-trigger ----
    text = note.read_text(encoding="utf-8")
    fm, body, had = managed_markdown._parse(text)
    check("frontmatter sau mark hop le", had is True)
    fm["status"] = "processed"
    fm["processed_at"] = "2026-08-23T07:00:00+07:00"
    fm["wiki_links"] = ["Hoa don dien tu"]
    fm["javis_last_checked"] = "2026-08-23T07:15:00+07:00"
    note.write_text(render_with_body(fm, body), encoding="utf-8")
    scan_ops = managed_markdown.scan_managed_notes(str(vault))
    check("chi metadata van hanh doi = unchanged", scan_ops["changed_count"] == 0)

    # ---- user semantic metadata DOES trigger ----
    fm2, body2, _ = managed_markdown._parse(note.read_text(encoding="utf-8"))
    fm2["tags"] = ["hoa-don", "ho-kinh-doanh"]
    note.write_text(render_with_body(fm2, body2), encoding="utf-8")
    scan_tag = managed_markdown.scan_managed_notes(str(vault))
    check("user doi tags = changed", scan_tag["changed_count"] == 1)

    # mark again, then edit body
    managed_markdown.mark_ingested(str(vault), "06 - Sources/Công việc/Kế toán/hoa-don.md")
    _fm3, body3, _ = managed_markdown._parse(note.read_text(encoding="utf-8"))
    note.write_text(
        note.read_text(encoding="utf-8").replace(body3, body3 + "Người dùng bổ sung từ Obsidian.\n"),
        encoding="utf-8",
    )
    scan_body = managed_markdown.scan_managed_notes(str(vault))
    check("user doi body = changed", scan_body["changed_count"] == 1)
    check(
        "changed tra dung relative path",
        scan_body["changed"][0]["path"] == "06 - Sources/Công việc/Kế toán/hoa-don.md",
    )

    # ---- unmanaged and system areas are ignored ----
    unmanaged = sources / "chua-tieu-hoa.md"
    unmanaged.write_text("Chưa managed", encoding="utf-8")
    sys_note = vault / "06 - Sources" / "Javis" / "internal.md"
    sys_note.parent.mkdir(parents=True)
    sys_note.write_text(
        "---\njavis_managed: true\njavis_last_ingested_hash: bad\n---\ninternal",
        encoding="utf-8",
    )
    scan_scope = managed_markdown.scan_managed_notes(str(vault))
    changed_paths = {x["path"] for x in scan_scope["changed"]}
    check(
        "unmanaged markdown khong vao changed",
        "06 - Sources/Công việc/Kế toán/chua-tieu-hoa.md" not in changed_paths,
    )
    check("Javis/system subtree bi bo qua", "06 - Sources/Javis/internal.md" not in changed_paths)

    # ---- path traversal is refused ----
    outside = base / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    traversal_refused = False
    try:
        managed_markdown.mark_ingested(str(vault), "../outside.md")
    except ValueError:
        traversal_refused = True
    check("mark chan path traversal", traversal_refused)

    # ---- malformed top frontmatter is fail-closed and untouched ----
    malformed = sources / "malformed.md"
    malformed_text = "---\ntags: [broken\n---\nNội dung phải còn nguyên.\n"
    malformed.write_text(malformed_text, encoding="utf-8")
    malformed_refused = False
    try:
        managed_markdown.mark_ingested(
            str(vault), "06 - Sources/Công việc/Kế toán/malformed.md"
        )
    except ValueError:
        malformed_refused = True
    check("frontmatter malformed bi tu choi", malformed_refused)
    check("frontmatter malformed khong bi sua", malformed.read_text(encoding="utf-8") == malformed_text)

    # ---- plugin setup: exactly one loop, preserve user edits, register brain ----
    plugin_path = ROOT / "system" / "plugins" / "managed-markdown" / "plugin.py"
    spec = importlib.util.spec_from_file_location("managed_markdown_plugin_test", plugin_path)
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)

    class FakeCtx:
        def __init__(self):
            self.vault_root = str(vault)
            self.state_dir = str(state)
            self.tools = {}

        def register_tool(self, name, description, handler, min_mode, schema):
            self.tools[name] = handler

    ctx = FakeCtx()
    plugin.register(ctx)
    check(
        "plugin expose du 3 tools",
        set(ctx.tools)
        == {
            "javis_managed_notes_scan",
            "javis_managed_notes_mark",
            "javis_managed_notes_setup",
        },
    )
    setup1 = json.loads(ctx.tools["javis_managed_notes_setup"]({}, ctx))
    loop_path = vault / "Javis" / "loops" / "second-brain-refresh.md"
    check("setup tao refresh loop lan dau", setup1["created"] is True and loop_path.is_file())
    loop_text = loop_path.read_text(encoding="utf-8")
    check(
        "refresh loop 15 phut + precheck deterministic",
        "interval_min: 15" in loop_text and "precheck: managed-markdown" in loop_text,
    )
    custom_loop = loop_text.replace("interval_min: 15", "interval_min: 30") + "\nUSER CUSTOM\n"
    loop_path.write_text(custom_loop, encoding="utf-8")
    setup2 = json.loads(ctx.tools["javis_managed_notes_setup"]({}, ctx))
    check(
        "setup lan hai khong ghi de loop user",
        setup2["created"] is False and loop_path.read_text(encoding="utf-8") == custom_loop,
    )
    registry = json.loads((state / "loop_config.json").read_text(encoding="utf-8"))
    check("setup dang ky current Brain voi scheduler", str(vault.resolve()) in registry.get("brains", []))

    # Restore 15m loop for scheduler checks.
    loop_path.write_text(loop_text, encoding="utf-8")

    deps = LoopDeps(
        build_system_prompt=lambda brain: "SYS",
        brain_root=lambda brain: str(vault),
        aux_model=lambda: None,
        atomic_write_text=atomic_write_text,
        project_root=ROOT,
        state_dir=state,
        safe_tools=[],
        readonly_tools=[],
    )
    feature = LoopFeature(deps)
    lp = feature.get_loop("brain", "second-brain-refresh")
    check(
        "scheduler doc duoc precheck/source_roots",
        lp and lp["precheck"] == "managed-markdown" and lp["source_roots"] == [],
    )

    # Mark current user body so there is no change. If either method below runs, AI path was started.
    managed_markdown.mark_ingested(str(vault), "06 - Sources/Công việc/Kế toán/hoa-don.md")

    async def forbidden_build(loop):
        raise AssertionError("_build_prompt must not run when precheck finds no changes")

    feature._build_prompt = forbidden_build
    feature._make_cli = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("CLI/model must not be constructed")
    )
    no_change = asyncio.run(feature.run_cycle("brain", "second-brain-refresh", "scheduled"))
    check("scheduled no-change tra ai_started=False", no_change.get("ai_started") is False)
    check(
        "scheduled no-change khong tang runs_today",
        feature.read_state("brain").get("second-brain-refresh", {}).get("runs_today", 0) == 0,
    )

    # ---- real edit reaches AI path and receives only deterministic changed list ----
    with note.open("a", encoding="utf-8") as fh:
        fh.write("Thay đổi thật sau precheck.\n")

    captured = {"prompt": "", "cli_made": 0}

    async def fake_build(loop):
        return "BASE PROMPT", ""

    class FakeCLI:
        def is_available(self):
            return True

        async def query(self, prompt):
            captured["prompt"] = prompt
            yield {"type": "final", "content": "Không có việc mới"}

    feature._build_prompt = fake_build

    def fake_make_cli(*args, **kwargs):
        captured["cli_made"] += 1
        return FakeCLI()

    feature._make_cli = fake_make_cli
    changed_run = asyncio.run(feature.run_cycle("brain", "second-brain-refresh", "scheduled"))
    check("scheduled changed moi tao CLI/model path", captured["cli_made"] == 1)
    check("changed prompt co marker", "JAVIS_CHANGED_MANAGED_NOTES" in captured["prompt"])
    check(
        "changed prompt co dung note",
        "06 - Sources/Công việc/Kế toán/hoa-don.md" in captured["prompt"],
    )
    check("changed run ket thuc hop le", changed_run.get("ok") is True)


if fails:
    print("\nFAILURES:")
    for name in fails:
        print(" -", name)
    raise SystemExit(1)

print("\nALL MANAGED MARKDOWN SECOND-BRAIN TESTS PASSED")
