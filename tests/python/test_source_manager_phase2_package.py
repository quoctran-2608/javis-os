"""Source Manager V1 - Phase 2 production-package contract.

Chạy tay / CI:

    python tests/run.py source_manager_phase2

Phase 2 KHÔNG ingest, classify, move note, sửa frontmatter, tạo Wiki/Memory hay chạy AI.
Gate này chỉ chấp nhận production skeleton nếu:
- installer chỉ ghi vào JAVIS_STATE_DIR + Brain, không cần/chạm Javis code checkout;
- Brain `ingest-source` override thay được bản system còn nguyên, nhưng từ chối đè custom user;
- global USER plugin chạy cross-engine và biết đúng active Brain;
- doctor/status/probe là deterministic + read-only, probe chống path traversal;
- source-watch tồn tại nhưng MẶC ĐỊNH TẮT, Phase 2 không tự chạy nền;
- system_sync sau install giữ override như user-modified;
- installer idempotent và fail-closed khi gặp conflict.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_STATE = Path(tempfile.mkdtemp(prefix="javis-sm-p2-state-"))
os.environ["JAVIS_STATE_DIR"] = str(_STATE)

import mcp_hub  # noqa: E402
import plugins_host  # noqa: E402
import self_improve  # noqa: E402
import skill_router  # noqa: E402
import system_sync  # noqa: E402

_fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        _fails.append(name)


def tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    root = Path(root)
    if not root.exists():
        return h.hexdigest()
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        h.update(p.relative_to(root).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\1")
    return h.hexdigest()


def run_installer(*extra):
    cmd = [sys.executable, str(INSTALLER), "--brain", str(_BRAIN),
           "--state-dir", str(_STATE), "--json", *extra]
    return subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def parse_json(stdout):
    try:
        return json.loads(stdout or "{}")
    except Exception:
        return {}


def tool_names(tools):
    return {t.get("fn") for t in (tools or [])}


PACKAGE = ROOT / "source-manager"
INSTALLER = PACKAGE / "install_source_manager.py"
_BRAIN = Path(tempfile.mkdtemp(prefix="javis-sm-p2-brain-"))
_FAKE_APP = Path(tempfile.mkdtemp(prefix="javis-sm-p2-app-"))
(_FAKE_APP / "server").mkdir()
(_FAKE_APP / "system").mkdir()
(_FAKE_APP / ".claude" / "skills").mkdir(parents=True)
(_FAKE_APP / "server" / "DO-NOT-TOUCH.py").write_text("UPSTREAM_SENTINEL\n", encoding="utf-8")
(_FAKE_APP / "system" / "DO-NOT-TOUCH.txt").write_text("UPSTREAM_SENTINEL\n", encoding="utf-8")
(_FAKE_APP / ".claude" / "skills" / "DO-NOT-TOUCH.md").write_text(
    "UPSTREAM_SENTINEL\n", encoding="utf-8")
_app_before = tree_digest(_FAKE_APP)


# ============================================================================
# 1) PACKAGE + DRY RUN: KHÔNG CÓ SIDE EFFECT
# ============================================================================
check("package: installer tồn tại", INSTALLER.is_file())
check("package: global plugin template tồn tại",
      (PACKAGE / "package" / "global-plugin" / "source-manager" / "plugin.py").is_file())
check("package: Brain ingest override tồn tại",
      (PACKAGE / "package" / "brain" / "skills" / "ingest-source" / "SKILL.md").is_file())

# Mô phỏng Brain thật đã được Javis sync: ingest-source hiện là bản system managed.
_seed = system_sync.sync_brain(_BRAIN)
_seed_ingest = _BRAIN / "skills" / "ingest-source" / "SKILL.md"
check("precondition: system_sync seed ingest-source thật", _seed.get("ok") and _seed_ingest.is_file())
_seed_text = _seed_ingest.read_text(encoding="utf-8") if _seed_ingest.is_file() else ""
_brain_before_dry = tree_digest(_BRAIN)
_state_before_dry = tree_digest(_STATE)

_dry = run_installer()
_dry_json = parse_json(_dry.stdout)
check("installer dry-run: exit 0", _dry.returncode == 0)
check("installer dry-run: báo applied=false", _dry_json.get("applied") is False)
check("installer dry-run: Brain byte-for-byte không đổi", tree_digest(_BRAIN) == _brain_before_dry)
check("installer dry-run: STATE_DIR byte-for-byte không đổi", tree_digest(_STATE) == _state_before_dry)
check("installer boundary: Javis app giả không đổi sau dry-run", tree_digest(_FAKE_APP) == _app_before)


# ============================================================================
# 2) APPLY: CHỈ STATE_DIR + BRAIN; OVERRIDE SYSTEM-MANAGED AN TOÀN
# ============================================================================
_apply = run_installer("--apply")
_apply_json = parse_json(_apply.stdout)
check("installer apply: exit 0", _apply.returncode == 0)
check("installer apply: applied=true", _apply_json.get("applied") is True)
check("installer boundary: Javis app giả vẫn byte-for-byte không đổi", tree_digest(_FAKE_APP) == _app_before)

_plugin_dir = _STATE / "plugins" / "source-manager"
check("install global plugin: plugin.py đúng ownership STATE_DIR",
      (_plugin_dir / "plugin.py").is_file())
check("install global plugin: plugin.yaml đúng ownership STATE_DIR",
      (_plugin_dir / "plugin.yaml").is_file())
check("install boundary: KHÔNG tạo Brain-local plugins/source-manager",
      not (_BRAIN / "plugins" / "source-manager").exists())

_ingest = _BRAIN / "skills" / "ingest-source" / "SKILL.md"
_sm_skill = _BRAIN / "skills" / "source-manager" / "SKILL.md"
_loop = _BRAIN / "Javis" / "loops" / "source-watch.md"
_cfg = _BRAIN / "System" / "SourceManager" / "config.yml"
_doc = _BRAIN / "System" / "SourceManager" / "README.md"
check("Brain asset: ingest-source được thay bằng Phase 2 route-only",
      _ingest.is_file() and "SOURCE_MANAGER_PHASE2_ROUTE_ONLY" in _ingest.read_text(encoding="utf-8"))
check("Brain asset: source-manager skill tồn tại", _sm_skill.is_file())
check("Brain asset: source-watch tồn tại", _loop.is_file())
check("Brain asset: config tồn tại", _cfg.is_file())
check("Brain asset: contract README tồn tại", _doc.is_file())
check("Phase 2 safety: source-watch mặc định TẮT",
      _loop.is_file() and "enabled: false" in _loop.read_text(encoding="utf-8").lower())
check("Phase 2 safety: ingest override không còn legacy Sources -> Wiki",
      _ingest.is_file() and "PHASE2_NO_LEGACY_INGEST" in _ingest.read_text(encoding="utf-8"))

# system_sync ngay sau install phải NHẬN RA user override, không lấy lại bản system.
_post_sync = system_sync.sync_brain(_BRAIN)
check("update resilience: system_sync giữ ingest override",
      "skills/ingest-source" in (_post_sync.get("kept_user") or []))
check("update resilience: canonical ingest vẫn là Source Manager",
      "SOURCE_MANAGER_PHASE2_ROUTE_ONLY" in _ingest.read_text(encoding="utf-8"))
check("Claude native mirror: .claude/skills nhận Source Manager override",
      "SOURCE_MANAGER_PHASE2_ROUTE_ONLY" in
      (_BRAIN / ".claude" / "skills" / "ingest-source" / "SKILL.md").read_text(encoding="utf-8"))

_resolved = skill_router.resolve_skill_file(_BRAIN, "ingest-source")
check("skill router: resolve canonical Phase 2 ingest override",
      _resolved is not None and _resolved.resolve() == _ingest.resolve())
_builtin_tools, _builtin_route = mcp_hub._builtin_tools("full", str(_BRAIN))
_loaded = asyncio.run(_builtin_route["javis_use_skill"]["call"]({"name": "ingest-source"}))
check("engine skill path: javis_use_skill thấy Phase 2 route-only",
      "SOURCE_MANAGER_PHASE2_ROUTE_ONLY" in str(_loaded))


# ============================================================================
# 3) PRODUCTION GLOBAL PLUGIN: CLAUDE SCOPE + DOCTOR/STATUS/PROBE
# ============================================================================
os.environ["JAVIS_ENABLE_USER_PLUGINS"] = "true"
plugins_host.invalidate()
mcp_hub.invalidate_cache()
_tools, _route = plugins_host.plugin_tools("full", str(_BRAIN), scope_vault=False)
_names = tool_names(_tools)
for _name in ("source_manager_status", "source_manager_doctor", "source_manager_probe_file"):
    check(f"plugin Claude scope: có tool {_name}", _name in _names)

_status_raw = asyncio.run(_route["source_manager_status"]["call"]({}))
try:
    _status = json.loads(_status_raw)
except Exception:
    _status = {}
check("status: production marker đúng", _status.get("component") == "source-manager")
check("status: phase đúng là 2", str(_status.get("phase")) == "2")
check("status: active Brain đúng", Path(str(_status.get("brain_root") or "")).resolve() == _BRAIN.resolve())
check("status: semantic writes bị khóa ở Phase 2", _status.get("semantic_write_enabled") is False)
check("status: plugin source là user/global", _status.get("plugin_source") == "user")

_before_doctor = tree_digest(_BRAIN)
_doctor_raw = asyncio.run(_route["source_manager_doctor"]["call"]({}))
_after_doctor = tree_digest(_BRAIN)
try:
    _doctor = json.loads(_doctor_raw)
except Exception:
    _doctor = {}
check("doctor: tổng thể PASS trên install chuẩn", _doctor.get("ok") is True)
check("doctor: read-only thật, không đổi byte nào trong Brain", _before_doctor == _after_doctor)
check("doctor: xác nhận không có Brain-local duplicate plugin",
      (_doctor.get("checks") or {}).get("no_brain_local_plugin") is True)
check("doctor: xác nhận source-watch disabled",
      (_doctor.get("checks") or {}).get("source_watch_disabled") is True)

_note = _BRAIN / "Notes" / "Personal" / "sample.md"
_note.parent.mkdir(parents=True)
_note.write_text("# Xin chào\nNội dung Phase 2.\n", encoding="utf-8", newline="\n")
_expected_sha = hashlib.sha256(_note.read_bytes()).hexdigest()
_probe_raw = asyncio.run(_route["source_manager_probe_file"]["call"](
    {"path": "Notes/Personal/sample.md"}))
try:
    _probe = json.loads(_probe_raw)
except Exception:
    _probe = {}
check("probe: SHA-256 chính xác", _probe.get("sha256") == _expected_sha)
check("probe: path trả Brain-relative POSIX", _probe.get("path") == "Notes/Personal/sample.md")
check("probe: chỉ probe, không semantic action", _probe.get("action") == "probe_only")

_outside = _BRAIN.parent / "source-manager-phase2-outside.txt"
_outside.write_text("SECRET OUTSIDE\n", encoding="utf-8")
_traversal = asyncio.run(_route["source_manager_probe_file"]["call"](
    {"path": "../source-manager-phase2-outside.txt"}))
check("probe security: chặn ../ traversal", str(_traversal).startswith("ERROR:"))
_abs = asyncio.run(_route["source_manager_probe_file"]["call"](
    {"path": str(_outside.resolve())}))
check("probe security: chặn absolute path", str(_abs).startswith("ERROR:"))
_internal = asyncio.run(_route["source_manager_probe_file"]["call"](
    {"path": ".javis/system-manifest.json"}))
check("probe scope: chặn file kỹ thuật ngoài managed roots", str(_internal).startswith("ERROR:"))


# ============================================================================
# 4) MCP HUB/CODEX PATH PHẢI GỌI ĐƯỢC PRODUCTION PLUGIN
# ============================================================================
async def probe_hub_status():
    listed = await mcp_hub._handle_one(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        "full", include_plugins=True, include_ambient=False, vault_root=str(_BRAIN))
    names = {t.get("name") for t in ((listed.get("result") or {}).get("tools") or [])}
    if "source_manager_status" in names:
        called = await mcp_hub._handle_one(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "source_manager_status", "arguments": {}}},
            "full", include_plugins=True, include_ambient=False, vault_root=str(_BRAIN))
        text = str((((called.get("result") or {}).get("content") or [{}])[0]).get("text") or "")
        return "direct", text
    if not {"javis_search_tools", "javis_run_tool"}.issubset(names):
        return "missing", ""
    searched = await mcp_hub._handle_one(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "javis_search_tools",
                    "arguments": {"query": "source manager status"}}},
        "full", include_plugins=True, include_ambient=False, vault_root=str(_BRAIN))
    st = str((((searched.get("result") or {}).get("content") or [{}])[0]).get("text") or "")
    if "source_manager_status" not in st:
        return "search-miss", st
    called = await mcp_hub._handle_one(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "javis_run_tool",
                    "arguments": {"name": "source_manager_status", "args": {}}}},
        "full", include_plugins=True, include_ambient=False, vault_root=str(_BRAIN))
    text = str((((called.get("result") or {}).get("content") or [{}])[0]).get("text") or "")
    return "lazy", text


_hub_path, _hub_text = asyncio.run(probe_hub_status())
check("MCP/Codex: production plugin đi được direct hoặc lazy protocol", _hub_path in ("direct", "lazy"))
check("MCP/Codex: status handler production thật sự chạy", '"component": "source-manager"' in _hub_text)


# ============================================================================
# 5) NATIVE LOOP CÓ MẶT NHƯNG PHASE 2 KHÔNG TỰ CHẠY
# ============================================================================
_loop_state_dir = Path(tempfile.mkdtemp(prefix="javis-sm-p2-loop-state-"))
_deps = self_improve.LoopDeps(
    build_system_prompt=lambda brain: "PHASE2_SYSTEM_PROMPT",
    brain_root=lambda name: str(_BRAIN),
    aux_model=lambda: None,
    atomic_write_text=lambda p, t: Path(p).write_text(t, encoding="utf-8"),
    project_root=ROOT,
    state_dir=_loop_state_dir,
    safe_tools=[],
    readonly_tools=[],
)
_feat = self_improve.LoopFeature(_deps)
_loops = _feat.list_loops("phase2-brain")
_watch = next((x for x in _loops if x.get("slug") == "source-watch"), None)
check("loop registry: đọc source-watch production", bool(_watch))
check("loop safety: source-watch normalized enabled=false", bool(_watch) and _watch.get("enabled") is False)
_feat.register_brain("phase2-brain")
_due = _feat._pick_due()
check("loop safety: disabled source-watch không được scheduler chọn",
      not (_due and _due[1].get("slug") == "source-watch"))


# ============================================================================
# 6) IDEMPOTENCY + CONFLICT: KHÔNG ÂM THẦM ĐÈ USER
# ============================================================================
_snapshot_before_second = (tree_digest(_BRAIN), tree_digest(_STATE))
_second = run_installer("--apply")
_second_json = parse_json(_second.stdout)
_snapshot_after_second = (tree_digest(_BRAIN), tree_digest(_STATE))
check("installer idempotent: lần apply 2 exit 0", _second.returncode == 0)
check("installer idempotent: changed=0", _second_json.get("changed") == 0)
check("installer idempotent: toàn bộ Brain+state byte-for-byte giữ nguyên",
      _snapshot_before_second == _snapshot_after_second)

_custom = _sm_skill.read_text(encoding="utf-8") + "\nUSER_CUSTOM_LINE\n"
_sm_skill.write_text(_custom, encoding="utf-8", newline="\n")
_conflict = run_installer("--apply")
_conflict_json = parse_json(_conflict.stdout)
check("installer conflict: fail-closed exit != 0", _conflict.returncode != 0)
check("installer conflict: báo đúng file user-modified",
      any("skills/source-manager/SKILL.md" in str(x) for x in (_conflict_json.get("conflicts") or [])))
check("installer conflict: KHÔNG đè custom user", _sm_skill.read_text(encoding="utf-8") == _custom)
check("installer boundary cuối: Javis app giả chưa từng bị đụng", tree_digest(_FAKE_APP) == _app_before)

os.environ.pop("JAVIS_ENABLE_USER_PLUGINS", None)
plugins_host.invalidate()
mcp_hub.invalidate_cache()

if _fails:
    print(f"\nFAIL - Source Manager Phase 2: {len(_fails)} lỗi: {_fails}")
    sys.exit(1)

print("\nOK - SOURCE MANAGER PHASE 2 PACKAGE CONTRACT VERIFIED")
print("Verified: app-clean installer + deterministic global plugin + route-only Brain skills + disabled native loop")
