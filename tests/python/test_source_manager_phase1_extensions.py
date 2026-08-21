"""Source Manager V1 - Phase 1 integration canary.

Chạy tay / CI:

    python tests/run.py source_manager_phase1

Mục tiêu DUY NHẤT của Phase 1: chứng minh Javis upstream sạch đã có đủ extension point để
Source Manager không cần sửa server/system/.claude/requirements.

Canary dựng Brain + STATE_DIR TẠM và kiểm đường chạy THẬT của ba contract:
1. Brain-local canonical skill `skills/ingest-source` thắng bản system + sống qua system_sync.
2. Plugin: chứng minh vault plugin KHÔNG phù hợp cross-engine (Claude cố ý scope_vault=False),
   rồi chứng minh USER/GLOBAL plugin trong JAVIS_STATE_DIR/plugins chạy được cả đường Claude
   in-process và MCP hub/Codex, trong khi ctx vẫn nhận đúng active Brain.
3. Native loop `Javis/loops/source-watch-canary.md` được registry đọc, scheduler chọn và
   run_due thực thi qua LoopFeature (chỉ fake engine để CI không gọi mạng/Claude).

Nếu test này cần vá production code mới xanh thì Phase 1 phải coi là FAIL về kiến trúc.
"""
from _paths import ROOT, SERVER  # noqa: E402,F401

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

# Phải đặt trước mọi import server module: config/plugins_host/mcp_store chụp STATE_DIR lúc import.
_STATE = Path(tempfile.mkdtemp(prefix="javis-sm-p1-state-"))
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


def atomic_write(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(p)


def tool_names(tools):
    return {t.get("fn") for t in (tools or [])}


_BRAIN = Path(tempfile.mkdtemp(prefix="javis-sm-p1-brain-"))


# ============================================================================
# 1) CANONICAL SKILL OVERRIDE + SYSTEM SYNC SURVIVAL
# ============================================================================
first_sync = system_sync.sync_brain(_BRAIN)
_system_copy = _BRAIN / "skills" / "ingest-source" / "SKILL.md"
check("skill tiền đề: system_sync cài ingest-source vào Brain canonical", _system_copy.is_file())
check("skill tiền đề: lượt sync đầu không lỗi", bool(first_sync.get("ok")))

_CANARY_SKILL = """---
name: Source Manager Phase 1 Canary
description: Canary Brain-local override for Source Manager Phase 1.
---
# PHASE1_BRAIN_LOCAL_INGEST_OVERRIDE

Nếu đọc được marker này thì Javis đang nạp canonical skill trong Brain, không phải bản app.
"""
_system_copy.write_text(_CANARY_SKILL, encoding="utf-8", newline="\n")

# Sync lại mô phỏng system-sync sau một Javis update. Contract bắt buộc: canonical skill mà
# user/Source Manager đã override phải được giữ, không bị bản app ghi đè.
second_sync = system_sync.sync_brain(_BRAIN)
check("skill update-resilience: sync nhận diện và giữ user override",
      "skills/ingest-source" in (second_sync.get("kept_user") or []))
check("skill update-resilience: nội dung canonical không bị system_sync ghi đè",
      _system_copy.read_text(encoding="utf-8") == _CANARY_SKILL)

resolved = skill_router.resolve_skill_file(_BRAIN, "ingest-source")
check("skill router: resolve đúng <Brain>/skills/ingest-source/SKILL.md",
      resolved is not None and resolved.resolve() == _system_copy.resolve())
meta = {x["slug"]: x for x in skill_router.list_skills(_BRAIN)}.get("ingest-source", {})
check("skill router: canonical source='skills' thắng fallback", meta.get("source") == "skills")

# Đi qua builtin tool mà engine API dùng thật, không chỉ gọi helper resolve.
_builtin_tools, _builtin_route = mcp_hub._builtin_tools("full", str(_BRAIN))
try:
    loaded_skill = asyncio.run(_builtin_route["javis_use_skill"]["call"]({"name": "ingest-source"}))
except Exception as e:  # noqa: BLE001
    loaded_skill = f"ERROR {type(e).__name__}: {e}"
check("skill engine path: javis_use_skill nạp đúng marker Brain-local",
      "PHASE1_BRAIN_LOCAL_INGEST_OVERRIDE" in str(loaded_skill))


# ============================================================================
# 2) PLUGIN CONTRACT: VAULT-ONLY IS NOT CROSS-ENGINE; GLOBAL USER PLUGIN IS
# ============================================================================
# Bật gate user plugin giống production sẽ cần. Đây là config opt-in, không phải patch code.
os.environ["JAVIS_ENABLE_USER_PLUGINS"] = "true"

# 2A. Canary vault plugin: có thể chạy qua hub khi scope_vault=True, NHƯNG Claude SDK upstream
# cố ý gọi plugin_tools(..., scope_vault=False). Ta ghi nhận sự thật này thành regression guard
# để không xây Source Manager ở sai ownership rồi phát hiện muộn như Brain OS cũ.
_vault_pdir = _BRAIN / "plugins" / "phase1-vault-only"
_vault_pdir.mkdir(parents=True, exist_ok=True)
(_vault_pdir / "plugin.yaml").write_text(
    "name: Phase 1 Vault Only\n"
    "slug: phase1-vault-only\n"
    "description: Negative canary proving vault plugins are not loaded by Claude scope_vault false.\n"
    "version: 0.0.1\n"
    "enabled: true\n"
    "min_mode: readonly\n",
    encoding="utf-8", newline="\n",
)
(_vault_pdir / "plugin.py").write_text(
    "def register(ctx):\n"
    "    ctx.register_tool(\n"
    "        name='phase1_vault_only_ping',\n"
    "        description='Vault-only negative canary.',\n"
    "        handler=lambda args, runtime_ctx: 'PHASE1_VAULT_ONLY',\n"
    "        schema={'type': 'object', 'properties': {}},\n"
    "        min_mode='readonly',\n"
    "    )\n",
    encoding="utf-8", newline="\n",
)
plugins_host.invalidate()
_vault_scoped, _ = plugins_host.plugin_tools("full", str(_BRAIN), scope_vault=True)
_claude_scoped_before, _ = plugins_host.plugin_tools("full", str(_BRAIN), scope_vault=False)
check("plugin architecture: vault plugin nạp được khi scope_vault=True",
      "phase1_vault_only_ping" in tool_names(_vault_scoped))
check("plugin architecture: vault plugin KHÔNG nạp ở Claude scope_vault=False",
      "phase1_vault_only_ping" not in tool_names(_claude_scoped_before))

# 2B. Source Manager cross-engine canary phải là USER/GLOBAL plugin. Nguồn chính thức của nó là
# JAVIS_STATE_DIR/plugins (plugins_host.GLOBAL_DIR), không nằm trong code tree Javis.
_global_pdir = Path(plugins_host.GLOBAL_DIR) / "source-manager-canary"
_global_pdir.mkdir(parents=True, exist_ok=True)
(_global_pdir / "plugin.yaml").write_text(
    "name: Source Manager Phase 1 Canary\n"
    "slug: source-manager-canary\n"
    "description: Cross-engine Source Manager canary installed in persistent Javis user state.\n"
    "version: 0.0.1\n"
    "enabled: true\n"
    "min_mode: readonly\n",
    encoding="utf-8", newline="\n",
)
(_global_pdir / "plugin.py").write_text(
    "import json\n\n"
    "def register(ctx):\n"
    "    def _ping(args, runtime_ctx):\n"
    "        return json.dumps({\n"
    "            'ok': True,\n"
    "            'marker': 'PHASE1_GLOBAL_PLUGIN_EXECUTED',\n"
    "            'vault_root': runtime_ctx.vault_root,\n"
    "            'source': runtime_ctx.source,\n"
    "        }, ensure_ascii=False)\n"
    "    ctx.register_tool(\n"
    "        name='source_manager_phase1_ping',\n"
    "        description='Phase 1 cross-engine canary ping for Source Manager.',\n"
    "        handler=_ping,\n"
    "        schema={'type': 'object', 'properties': {}},\n"
    "        min_mode='readonly',\n"
    "    )\n",
    encoding="utf-8", newline="\n",
)
plugins_host.invalidate()
mcp_hub.invalidate_cache()

# Đây chính là scope mà ClaudeSDK._plugins_server() đang dùng. Nếu canary không có ở đây,
# Source Manager sẽ không dùng được với Claude dù Codex/hub có thể thấy nó.
_claude_tools, _claude_route = plugins_host.plugin_tools(
    "full", str(_BRAIN), scope_vault=False)
check("plugin Claude path: global Source Manager tool có mặt với scope_vault=False",
      "source_manager_phase1_ping" in tool_names(_claude_tools))

_global_result = ""
try:
    _global_result = asyncio.run(_claude_route["source_manager_phase1_ping"]["call"]({}))
except Exception as e:  # noqa: BLE001
    _global_result = f"ERROR {type(e).__name__}: {e}"
try:
    _global_json = json.loads(_global_result)
except Exception:
    _global_json = {}
check("plugin Claude path: handler global thực sự chạy",
      _global_json.get("marker") == "PHASE1_GLOBAL_PLUGIN_EXECUTED")
check("plugin Claude path: global PluginContext vẫn nhận đúng active Brain",
      Path(str(_global_json.get("vault_root") or "")).resolve() == _BRAIN.resolve())
check("plugin Claude path: plugin source đúng là user/global",
      _global_json.get("source") == "user")

# Gate OFF phải fail-closed cho user/global Python plugin.
os.environ.pop("JAVIS_ENABLE_USER_PLUGINS", None)
plugins_host.invalidate()
_gate_off_tools, _ = plugins_host.plugin_tools("full", str(_BRAIN), scope_vault=False)
_desc_off = {x["slug"]: x for x in plugins_host.describe(str(_BRAIN))}
check("plugin safety: global plugin bị chặn khi opt-in env bị tắt",
      "source_manager_phase1_ping" not in tool_names(_gate_off_tools))
check("plugin safety: metadata báo gated thay vì âm thầm coi là loaded",
      _desc_off.get("source-manager-canary", {}).get("gated") is True)

# Bật lại để chứng minh đường MCP hub / Codex. HTTP hub có lazy-tool layer, nên probe phải tôn
# trọng protocol thật: nếu tool visible thì gọi thẳng; nếu bị lazy thì search -> run.
os.environ["JAVIS_ENABLE_USER_PLUGINS"] = "true"
plugins_host.invalidate()
mcp_hub.invalidate_cache()


async def _probe_http_plugin():
    listed = await mcp_hub._handle_one(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        "full", include_plugins=True, include_ambient=False, vault_root=str(_BRAIN))
    names = {t.get("name") for t in ((listed.get("result") or {}).get("tools") or [])}

    if "source_manager_phase1_ping" in names:
        called = await mcp_hub._handle_one(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "source_manager_phase1_ping", "arguments": {}}},
            "full", include_plugins=True, include_ambient=False, vault_root=str(_BRAIN))
        text = str((((called.get("result") or {}).get("content") or [{}])[0]).get("text") or "")
        return {"path": "direct", "names": names, "text": text, "search_text": ""}

    # Lazy mode: model phải tìm schema trước rồi dispatch bằng javis_run_tool.
    if not {"javis_search_tools", "javis_run_tool"}.issubset(names):
        return {"path": "missing", "names": names, "text": "", "search_text": ""}
    searched = await mcp_hub._handle_one(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "javis_search_tools",
                    "arguments": {"query": "source manager phase1 ping"}}},
        "full", include_plugins=True, include_ambient=False, vault_root=str(_BRAIN))
    search_text = str((((searched.get("result") or {}).get("content") or [{}])[0]).get("text") or "")
    if "source_manager_phase1_ping" not in search_text:
        return {"path": "lazy-search-miss", "names": names, "text": "", "search_text": search_text}
    called = await mcp_hub._handle_one(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "javis_run_tool",
                    "arguments": {"name": "source_manager_phase1_ping", "args": {}}}},
        "full", include_plugins=True, include_ambient=False, vault_root=str(_BRAIN))
    text = str((((called.get("result") or {}).get("content") or [{}])[0]).get("text") or "")
    return {"path": "lazy", "names": names, "text": text, "search_text": search_text}


_http_probe = asyncio.run(_probe_http_plugin())
check("plugin HTTP/Codex path: hub expose direct tool hoặc đúng lazy search/run protocol",
      _http_probe.get("path") in ("direct", "lazy"))
check("plugin HTTP/Codex path: dispatcher thực thi global Source Manager tool",
      "PHASE1_GLOBAL_PLUGIN_EXECUTED" in _http_probe.get("text", ""))


# ============================================================================
# 3) NATIVE Javis/loops/*.md -> REGISTRY -> SCHEDULER -> RUN
# ============================================================================
_loop_dir = _BRAIN / "Javis" / "loops"
_loop_dir.mkdir(parents=True, exist_ok=True)
_loop_path = _loop_dir / "source-watch-canary.md"
_loop_path.write_text(
    "---\n"
    "type: loop\n"
    "name: Source Watch Phase 1 Canary\n"
    "slug: source-watch-canary\n"
    "enabled: true\n"
    "goal: custom\n"
    "mode: suggest\n"
    "interval_min: 5\n"
    "workspace: vault\n"
    "tools_profile: vault-safe\n"
    "notify: false\n"
    "---\n\n"
    "PHASE1_SOURCE_WATCH_OBJECTIVE: inspect local Markdown changes only.\n",
    encoding="utf-8", newline="\n",
)

_LOOP_BRAIN_NAME = "phase1-external-brain"
_DEFAULT_BRAIN = Path(tempfile.mkdtemp(prefix="javis-sm-p1-default-brain-"))


def _brain_root(name):
    return str(_BRAIN if name == _LOOP_BRAIN_NAME else _DEFAULT_BRAIN)


_loop_state_dir = Path(tempfile.mkdtemp(prefix="javis-sm-p1-loop-state-"))
_deps = self_improve.LoopDeps(
    build_system_prompt=lambda brain: "PHASE1_SYSTEM_PROMPT",
    brain_root=_brain_root,
    aux_model=lambda: None,
    atomic_write_text=atomic_write,
    project_root=ROOT,
    state_dir=_loop_state_dir,
    safe_tools=[],
    readonly_tools=[],
)
_feat = self_improve.LoopFeature(_deps)

_loops = _feat.list_loops(_LOOP_BRAIN_NAME)
_lp = next((x for x in _loops if x.get("slug") == "source-watch-canary"), None)
check("loop registry: đọc đúng file <Brain>/Javis/loops/source-watch-canary.md", bool(_lp))
check("loop registry: body custom tới đúng objective",
      bool(_lp) and "PHASE1_SOURCE_WATCH_OBJECTIVE" in _lp.get("body", ""))
check("loop registry: interval native giữ 5 phút", bool(_lp) and _lp.get("interval_min") == 5)

# External Brain phải được register thì scheduler mới quét bền vững.
_feat.register_brain(_LOOP_BRAIN_NAME)
_sched_brains = _feat.scheduler_brains()
check("loop scheduler: external Brain được register vào danh sách quét",
      _LOOP_BRAIN_NAME in _sched_brains)
_due = _feat._pick_due()
check("loop scheduler: source-watch canary được chọn khi đến hạn",
      bool(_due) and _due[0] == _LOOP_BRAIN_NAME and _due[1].get("slug") == "source-watch-canary")

# Fake CHỈ engine Claude để CI không cần login/network. Mọi phần đọc file, normalize, scheduler,
# build prompt, state/log và run_cycle đều là code upstream THẬT.
_seen_prompts = []


class _FakeCLI:
    def is_available(self):
        return True

    async def query(self, prompt):
        _seen_prompts.append(prompt)
        yield {"type": "final", "content": "PHASE1_NATIVE_LOOP_EXECUTED"}


_feat._make_cli = lambda *args, **kwargs: _FakeCLI()
_run = asyncio.run(_feat.run_due("scheduled"))
check("loop execution: scheduler -> run_due chạy thành công", _run.get("ok") is True)
check("loop execution: custom body thật sự được đưa vào prompt chạy",
      any("PHASE1_SOURCE_WATCH_OBJECTIVE" in p for p in _seen_prompts))
check("loop execution: engine canary trả marker qua native run_cycle",
      "PHASE1_NATIVE_LOOP_EXECUTED" in str(_run.get("summary") or ""))
_loop_state = _feat.read_state(_LOOP_BRAIN_NAME).get("source-watch-canary", {})
check("loop state: Javis ghi last_run/last_status cho loop đã thực thi",
      float(_loop_state.get("last_run") or 0) > 0 and _loop_state.get("last_status") == "ok")
_log_files = list((_BRAIN / "Javis" / "loop-log").glob("*.md"))
check("loop log: native runner ghi log trong chính Brain", bool(_log_files))


# ============================================================================
# PHASE 1 VERDICT
# ============================================================================
os.environ.pop("JAVIS_ENABLE_USER_PLUGINS", None)
plugins_host.invalidate()
mcp_hub.invalidate_cache()

if _fails:
    print(f"\nFAIL - Source Manager Phase 1: {len(_fails)} lỗi: {_fails}")
    sys.exit(1)

print("\nOK - SOURCE MANAGER PHASE 1 EXTENSION POINTS VERIFIED")
print("Verified: Brain skill override + global user plugin cross-engine + native registered loop execution")
