# AGY_MCP — Handoff

调试细节、API 发现过程、pytermgui 踩坑记录。架构总览见
[`agy_knowledge/architecture.md`](agy_knowledge/architecture.md)，
斜线指令接入机制见
[`agy_knowledge/command_access_tiers.md`](agy_knowledge/command_access_tiers.md)。

---

## 0. Current state (2026-06-17)

**全部 39 个 MCP 工具已完成**（Tier A–F），含：

| 层 | 机制 | 代码 |
|----|------|------|
| A | CLI flags + headless `--print` | `agy_client.py` |
| B | SQLite fork/rewind/export | `conversations.py` |
| C | 配置文件 read/write | `tier_c_commands.py` |
| D | shell diff/open/logout + plugin | `tier_d_commands.py` |
| E | gRPC attach（usage/tasks/agents） | `agy_models.py` / `tier_e_commands.py` |
| F | ConPTY 伪终端（goal/planning/fast/schedule/btw/grill-me/teamwork） | `tier_f_commands.py` |

**TUI**（`tui.py`，由 `run.bat` 启动）功能完整：
- Models 页：列出实时模型，点击切换（写 settings.json）
- Quota 页：Group Limits（gRPC 真实配额）/ Session Usage（SQLite）/ 个人模型配额（REST）
- Reload 按钮：强制刷新配额数据

**委托分工**（见 [`agy_knowledge/delegation.md`](agy_knowledge/delegation.md)）：
- **gemi-mcp**：上网搜索、图片生成、多模态分析
- **agy-mcp**：代码写入、文件修改、git 操作
- **Claude**：设计决策、复杂推理、最终验证

---

## 1. Where `agy` lives

```
AGY_BIN = %LOCALAPPDATA%\agy\bin\agy.exe        (override: AGY_BIN env var)
app data = %USERPROFILE%\.gemini\antigravity-cli\
```

`agy --help` subcommands: `models`, `changelog`, `install`, `plugin`, `update`.
Useful flags: `--print/-p <prompt>`, `--model <label>`, `--conversation <id>`,
`--continue/-c`, `--dangerously-skip-permissions`, `--add-dir <dir>`,
`--log-file <path>`.

---

## 2. OAuth credential (Windows Credential Manager)

- Target `gemini:antigravity`, type `CRED_TYPE_GENERIC` (read via `win32cred`).
- Blob JSON:
  ```json
  {"token":{"access_token":"ya29...","refresh_token":"1//...","expiry":"..."},"auth_method":"consumer"}
  ```
- **Token refresh:** 401 → run `agy models` headless (`CREATE_NO_WINDOW`) as a side effect → re-read credential and retry.
- `check_email_now()` in `tui.py`: reads credential + userinfo call to show profile email.

---

## 3. REST APIs (cloudcode-pa)

Same OAuth token. Headers: `Authorization: Bearer <token>`, `Content-Type: application/json`. All `POST`.

| Endpoint | Host | Body | Result |
|---|---|---|---|
| `:retrieveUserQuota` | `cloudcode-pa.googleapis.com` | `{"project":"app"}` | **200** — `buckets[]` of `{modelId, remainingFraction, resetTime}`. Gemini model IDs only, no Claude/GPT-OSS. |
| `:loadCodeAssist` | `daily-cloudcode-pa.googleapis.com` | `{}` | **200** — `cloudaicompanionProject` (dynamic per account), `currentTier`. Discover here, never hardcode. |
| `:fetchAvailableModels` | either host | `{"project": ...}` | **403 PERMISSION_DENIED** — gated to language-server internal auth. Use ConPTY `agy models` instead. |

---

## 4. gRPC language server — technical details

Full technique in [`AGENTS.md`](AGENTS.md). Key details not in architecture.md:

### Port discovery

```python
# snapshot ports before/after spawning agy:
ports_before = _local_ports()
proc = subprocess.Popen([AGY_BIN], ...)
# poll until len(new_ports) >= 2, sleep 0.5 s
grpc_port = min(_local_ports() - ports_before)  # lower = gRPC/TLS
```

### TLS cert extraction

```python
ctx = ssl.create_default_context()
ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
with socket.create_connection(("127.0.0.1", grpc_port), timeout=5) as s:
    with ctx.wrap_socket(s, server_hostname="localhost") as ts:
        cert_pem = ssl.DER_cert_to_PEM_cert(ts.getpeercert(binary_form=True))
```

### Protobuf structure (`RetrieveUserQuotaSummary` response)

```
QuotaSummaryResponse {
  field[1] → QuotaSummaryPayload {
    field[2] repeated → QuotaGroup {
      field[1] repeated → QuotaBucket {
        field[1] → bucket id  ("gemini-weekly", "gemini-5h", "3p-weekly", "3p-5h")
        field[2] → display name ("Weekly Limit", "Five Hour Limit")
        field[4] → remaining fraction  float32  (0.0–1.0)
        field[6] → Timestamp { field[1] = Unix seconds }
        field[7] → human message ("Refreshes in 2 days, 7 hours")
        field[8] → is_hit bool
      }
      field[2] → group name ("Gemini Models", "Claude and GPT models")
    }
  }
}
```

Full decoder: `agy_models.py::_parse_quota_proto()`. Wire type 5 = float32, 0 = varint, 2 = length-delimited.

### Other available gRPC methods (verified)

Empty-request methods: `GetUserStatus` (4.7 KB account info), `GetWorkspaceInfos`, `GetAllCascadeTrajectories`.
Field-1-string methods: `GetCascadeTrajectory`, `GetCascadeTrajectorySteps`, `GetConversationMetadata`, `GetAgentTeamMetadata` (field1 = `file://` project URI).
`GetSlashCommands`: needs nested model sub-message + real trajectory id — not wrapped yet.
`RequestAgentStatePageUpdate`: push model, must have active `StreamAgentStateUpdates` subscriber first.

### Wait after port discovery

OAuth auth completes asynchronously inside agy. Wait **~4 s** after port discovery before making gRPC calls.

### Dead end: ConPTY `/usage`

ConPTY injection of `/usage` doesn't work — the first Enter selects the autocomplete item, the second triggers "No matches." The gRPC path is the only working `/usage` source.

---

## 5. Diagnostics

- **`agy --log-file <path> models`** — verbose log: OAuth flow, every backend URL hit, model propagation. How `fetchAvailableModels`/`loadCodeAssist` endpoints were discovered.
- **`_startup.log`** — written by `server.py` before MCP negotiation; confirms the process started.
- **`tui_crash.log`** — uncaught exceptions from any TUI thread (alt-screen eats tracebacks).
- Prior research scratch: `~/.gemini/antigravity/brain/<id>/scratch/test_*.py`

---

## 6. pytermgui gotchas (all fixed in `tui.py`)

1. **Splitter `KeyError: 'scroll_down'`** — `Container.handle_key` reads `self.keys["scroll_down"]` unconditionally. Fix: `sp.keys = {**sp.keys, "scroll_down": set(), "scroll_up": set()}` in `_split()`.

2. **Splitter `+1` position fudge** — bumps `pos.y` of direct children whose `type(...).__name__ == "Container"`. `_Column`/`_Frame` are subclasses, so name check misses them → hover/click hits wrong row.

3. **Splitter mispads unequal-height columns** — fills missing rows with mutated `target_width`. Fix: pad both children to the same `body_h` line count in `update_content_ui()`.

4. **Compositor draw thread race** — `set_widgets` (`self._widgets = []` then append) races the draw thread's `get_lines` → `RuntimeError: list changed size during iteration`. Fix: `_Column` guards with a re-entrant lock.

5. **Console encoding** — box-drawing chars crash under Windows cp1252. Set `PYTHONIOENCODING=utf-8`. Smoke test: `PYTHONIOENCODING=utf-8 python -c "import tui; tui.build_window()"`.

### Styling (don't redo)
- Uniform grey `240` for every frame/divider (`BORDER`).
- Flat widgets: grey frame at rest, grey-background highlight on hover/active.
- Owner wants minimalist ("越精简越好"), iterates visually, build incrementally.

---

## 7. Pending / Next steps

1. **Chat session / logs in TUI** — hook up session launch or log viewer inside the TUI.
2. **Interactive OAuth trigger** — wire re-auth flow in TUI directly.
3. `requirements.txt` lists `mcp[cli]`, `pytermgui`, `grpcio`, `pyyaml`. `tui.py` also uses `win32cred` — add `pywin32` explicitly if not implied by a dep.
