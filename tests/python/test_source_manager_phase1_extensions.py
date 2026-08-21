"""Source Manager V1 - Phase 1 integration canary.

Chạy tay / CI:

    python tests/run.py source_manager_phase1

Mục tiêu DUY NHẤT của Phase 1: chứng minh Javis upstream sạch đã có đủ extension point để
Source Manager sống HOÀN TOÀN trong Brain, không cần sửa server/system/.claude/requirements.

Canary này dựng một Brain TẠM và kiểm đường chạy THẬT của ba contract:
1. Brain-local canonical skill `skills/ingest-source` thắng bản system + sống qua system_sync.
2. Brain-local vault plugin `plugins/source-manager-canary` bị gate khi chưa opt-in, rồi được
   nạp vào MCP hub + gọi được tool khi JAVIS_ENABLE_USER_PLUGINS=true.
3. Native loop `Javis/loops/source-watch-canary.md` được registry đọc, scheduler chọn và
   run_due thực thi qua LoopFeature (engine được fake để test không gọi mạng/Claude).

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


_BRAIN = Path(tempfile.mkdtemp(prefix="javis-sm-p1-brain-"))


# ============================================================================
# 1) CANONICAL SKILL OVERRIDE + SYSTEM SYNC SURVIVAL
# ============================================================================
# Tiền đề thật: main hiện ship ingest-source dưới tầng system. sync_brain phải cài nó vào
# canonical Brain trước; nếu upstream bỏ skill này trong tương lai thì override vẫn route được,
# nhưng canary "sống qua system update" không còn đúng bài toán và phải được review lại.
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

# Sync lại mô phỏng Javis update/system_sync. Contract bắt buộc: user-modified canonical skill
# được giữ nguyên, không bị bản app ghi đè.
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

# Không chỉ gọi router helper: đi qua builtin tool mà engine API dùng thật.
_builtin_tools, _builtin_route = mcp_hub._builtin_tools("full", str(_BRAIN))
try:
    loaded_skill = asyncio.run(_builtin_route["javis_use_skill"]["call"]({"name": "ingest-source"}))
except Exception as e:  # noqa: BLE001 - canary phải biến mọi lỗi đường thật thành FAIL rõ
    loaded_skill = f"ERROR {type(e).__name__}: {e}"
check("skill engine path: javis_use_skill nạp đúng marker Brain-local",
      "PHASE1_BRAIN_LOCAL_INGEST_OVERRIDE" in str(loaded_skill))


# ============================================================================
# 2) BRAIN-LOCAL VAULT PLUGIN -> PLUGIN HOST -> MCP HUB -> CALL
# ============================================================================
_pdir = _BRAIN / "plugins" / "source-manager-canary"
_pdir.mkdir(parents=True, exist_ok=True)
(_pdir / "plugin.yaml").write_text(
    "name: Source Manager Phase 1 Canary\n"
    "slug: source-manager-canary\n"
    "description: Proves Brain-local plugin loading without patching Javis.\n"
    "version: 0.0.1\n"
    "enabled: true\n"
    "min_mode: readonly\n",
    encoding="utf-8", newline="\n",
)
(_pdir / "plugin.py").write_text(
    "import json\n\n"
    "def register(ctx):\n"
    "    def _ping(args, runtime_ctx):\n"
    "        return json.dumps({\n"
    "            'ok': True,\n"
    "            'marker': 'PHASE1_VAULT_PLUGIN_EXECUTED',\n"
    "            'vault_root': runtime_ctx.vault_root,\n"
    "        }, ensure_ascii=False)\n"
    "    ctx.register_tool(\n"
    "        name='source_manager_phase1_ping',\n"
    "        description='Phase 1 canary ping for Brain-local Source Manager plugin.',\n"
    "        handler=_ping,\n"
    "        schema={'type': 'object', 'properties': {}},\n"
    "        min_mode='readonly',\n"
    "    )\n",
    encoding="utf-8", newline="\n",
)

# Gate OFF: plugin có manifest nhưng Python KHÔNG được thực thi.
os.environ.pop("JAVIS_ENABLE_USER_PLUGINS", None)
os.environ.pop("JAVIS_ENABLE_VAULT_PLUGINS", None)
plugins_host.invalidate()
mcp_hub.invalidate_cache()
_desc_off = {x["slug"]: x for x in plugins_host.describe(str(_BRAIN))}
check("plugin safety: vault plugin được phát hiện nhưng gated khi chưa opt-in",
      _desc_off.get("source-manager-canary", {}).get("gated") is True)
_tools_off, _route_off = asyncio.run(mcp_hub.discover_all(
    "full", vault_root=str(_BRAIN), include_plugins=True, force_refresh=True))
_inv_tools_off, _inv_route_off = mcp_hub.registry_inventory(
    "full", str(_BRAIN), include_plugins=True, include_ambient=False, force_lazy=False)
check("plugin safety: tool không lọt vào MCP hub khi gate OFF",
      "source_manager_phase1_ping" not in _inv_route_off)

# Gate ON: dùng tên env mới được upstream khuyến nghị. Invalidate cả plugin cache + hub cache.
os.environ["JAVIS_ENABLE_USER_PLUGINS"] = "true"
plugins_host.invalidate()
mcp_hub.invalidate_cache()
_tools_on, _route_on = asyncio.run(mcp_hub.discover_all(
    "full", vault_root=str(_BRAIN), include_plugins=True, force_refresh=True))
_inv_tools_on, _inv_route_on = mcp_hub.registry_inventory(
    "full", str(_BRAIN), include_plugins=True, include_ambient=False, force_lazy=False)
check("plugin hub: Brain-local tool xuất hiện trong pre-lazy inventory của MCP hub",
      "source_manager_phase1_ping" in _inv_route_on)

_plugin_result = ""
try:
    _plugin_result = asyncio.run(_inv_route_on["source_manager_phase1_ping"]["call"]({}))
except Exception as e:  # noqa: BLE001
    _plugin_result = f"ERROR {type(e).__name__}: {e}"
try:
    _plugin_json = json.loads(_plugin_result)
except Exception:
    _plugin_json = {}
check("plugin call: handler Python trong Brain thực sự được thực thi",
      _plugin_json.get("marker") == "PHASE1_VAULT_PLUGIN_EXECUTED")
check("plugin call: PluginContext nhận đúng Brain đang active",
      Path(str(_plugin_json.get("vault_root") or "")).resolve() == _BRAIN.resolve())

# Chứng minh đường HTTP/Codex dùng cùng hub dispatcher, không chỉ gọi plugin host riêng.
_http_list = asyncio.run(mcp_hub._handle_one(
    {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    "full", include_plugins=True, include_ambient=False, vault_root=str(_BRAIN)))
_http_names = {t.get("name") for t in ((_http_list.get("result") or {}).get("tools") or [])}
# Nếu lazy đang bật thì canary có thể nằm sau javis_search_tools, nên tools/list không bắt buộc
# phơi trực tiếp. Inventory ở trên mới là source of truth; HTTP call dưới đây gọi dispatcher
# theo đúng tên và sẽ chứng minh route thực tế.
_http_call = asyncio.run(mcp_hub._handle_one(
    {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
     "params": {"name": "source_manager_phase1_ping", "arguments": {}}},
    "full", include_plugins=True, include_ambient=False, vault_root=str(_BRAIN)))
_http_text = str((((_http_call.get("result") or {}).get("content") or [{}])[0]).get("text") or "")
check("plugin HTTP hub: tools/call thực thi được vault plugin cho Codex/HTTP engine path",
      "PHASE1_VAULT_PLUGIN_EXECUTED" in _http_text)


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
# Dọn env canary để không ảnh hưởng module khác nếu file này được import trong một runner khác.
os.environ.pop("JAVIS_ENABLE_USER_PLUGINS", None)
plugins_host.invalidate()
mcp_hub.invalidate_cache()

if _fails:
    print(f"\nFAIL - Source Manager Phase 1: {len(_fails)} lỗi: {_fails}")
    sys.exit(1)

print("\nOK - SOURCE MANAGER PHASE 1 EXTENSION POINTS VERIFIED")
print("Verified: Brain skill override + vault plugin/MCP hub + native registered loop execution")
