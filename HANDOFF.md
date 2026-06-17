# AGY_MCP — Handoff

How to drive Google's **Antigravity CLI (`agy`)** from code, plus the current
state of the pytermgui control panel (`tui.py`). The next agent should be able to
add features against `agy` using only this document.

---

## 0. Current state (2026-06-17)

### 本次会话新增

**委托分工指南** [`agy_knowledge/delegation.md`](agy_knowledge/delegation.md)：三方分工指南，供 Claude 自用。
- **gemi-mcp**：上网搜索（需先 `apply_settings(tool="Google Search")`）、图片生成、文件多模态分析。控制 Gemini 网页 UI，依赖 engine_service.py port 18800。
- **agy-mcp**：代码写入、文件修改、git 操作，用 `ask_antigravity`。
- **Claude**：设计决策、复杂推理、最终验证。

**`working_dir` 参数已完成**（`agy_client.py` + `server.py`）：

`ask_antigravity` 现在接受可选 `working_dir` 参数。传入目标项目路径后，agy 以该目录
为 workspace 启动——完整扫描项目树、git 操作落在正确 repo、相对路径工具全部正常。
不传则沿用 `_resolve_trusted_cwd()`（旧行为）。`working_dir` 是每次调用时传入的参数，
可随时指向不同项目。`GEMINI_CLI_TRUST_WORKSPACE=true` 已在环境变量里，无需额外 trust。

**TUI 新增 Quota 页 reload 按钮**（`tui.py`）：点击 `↻ reload` 强制刷新配额数据。

**已知 next steps**：
- 重启 agy-mcp server 后 `working_dir` 参数才对 MCP client 可见。
- 可考虑为 TUI 增加 chat session 启动或日志查看功能。

---

## 0b. Original state (2026-06-16)

The TUI (`tui.py`, launched by `run.bat`) is **functional**:

- **Layout** (owner-confirmed via screenshots): a full-height left rail **B**
  (`Models` / `Quota` nav) beside a right column stacking the status panel **C**
  (`log in` / `log out` + profile) over the main panel **D** (content).
- **Models view**: lists the **live** model labels from `agy models`, with a green `●`
  on the currently-selected one; click a row to switch (writes `settings.json`).
- **Quota & Usage view**: **Fully completed** with **real gRPC data** (Method E below).
  - **Account Group Limits**: real Weekly / Five-Hour progress bars for Gemini group AND
    Claude & GPT group, fetched live from `agy`'s local language-server gRPC endpoint
    `RetrieveUserQuotaSummary`. Color-coded: green ≥ 40%, yellow 10–40%, red < 10%.
    Countdown timers compute seconds-until-reset from the Unix timestamps in the response.
  - **Session Usage**: active model, elapsed time, workspace dir, estimated tokens from
    the newest conversation SQLite DB.
  - **Individual Model Quotas**: daily request counts from the REST `retrieveUserQuota`
    endpoint (Gemini model IDs only, all separate from the group token limits above).
- Login/logout + profile email work; a 3 s background poll keeps the profile line
  and the selection `●` in sync with external `/model` changes.

Backups/scratch: `tui.sidebar-layout.bak.py` is an obsolete intermediate layout
(safe to delete). `tui_crash.log` is written by the crash logger (gitignored).

---

## 1. Where `agy` lives

```
AGY_BIN = %LOCALAPPDATA%\agy\bin\agy.exe        (override with the AGY_BIN env var)
app data = %USERPROFILE%\.gemini\antigravity-cli\
```

`agy --help` subcommands: `models`, `changelog`, `install`, `plugin`, `update`.
Useful flags: `--print/-p <prompt>`, `--model <label>`, `--conversation <id>`,
`--continue/-c`, `--dangerously-skip-permissions`, `--add-dir <dir>`,
`--log-file <path>`.

There are **four** ways to get data out of `agy` (plus a prompt round-trip).
Pick per need.

---

## 2. Method A — shared config files (the "real record")

`agy`, the MCP server, and the TUI all share these files. Reading/writing them is
the canonical, dependency-free way to observe and change CLI state.

### `settings.json` — `~/.gemini/antigravity-cli/settings.json`
```json
{ "model": "Gemini 3.5 Flash (Medium)", "trustedWorkspaces": [...],
  "permissions": {...}, "enableTelemetry": false, "allowNonWorkspaceAccess": true }
```
- `"model"` = the **currently selected** model **display label**. Read it for the
  active selection; write it to switch models. The change is picked up by the CLI
  and MCP server on their next call (bidirectional sync).
- See `get_selected_model()` / `set_selected_model()` in `tui.py`.

### `trustedFolders.json` — `~/.gemini/trustedFolders.json`
Map of folder → `"TRUST_FOLDER"`. Used to pick a trusted `cwd` so headless
`--print` runs don't block on a trust prompt (see `agy_client.py`).

### OAuth credential — Windows Credential Manager
- Target `gemini:antigravity`, type `CRED_TYPE_GENERIC` (read via `win32cred`).
- Blob is JSON:
  `{"token":{"access_token":"ya29...","refresh_token":"1//...","expiry":"..."},"auth_method":"consumer"}`
- `check_email_now()` in `tui.py` shows the read + a userinfo call.
- **Token refresh:** if a token call returns 401, run `agy models` headless
  (`creationflags=0x08000000` = CREATE_NO_WINDOW) — it refreshes the keyring token
  via the refresh_token as a side effect — then re-read the credential and retry.

---

## 3. Method B — `agy models` via ConPTY (the live model list)

**The only way to get the full display labels** (`Gemini 3.5 Flash (Medium)`,
`Claude Sonnet 4.6 (Thinking)`, `GPT-OSS 120B (Medium)`, …). See `agy_models.py`.

### The catch
`agy models` prints the list **only when stdout is a real console** (it checks
`isatty`). Under a pipe / redirect / headless it authenticates and exits but
prints **nothing**. So you must attach it to a **pseudo-console**.

### The mechanism (`agy_models.py::list_models()`)
- Use the Windows **ConPTY** API via `ctypes` — `CreatePseudoConsole` +
  `CreateProcessW` with `PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE`. **No third-party
  dependency** (don't add `pywinpty`; the MSYS `winpty` binary needs a tty stdin
  and won't work here).
- Output is VT-encoded: a `⠋ Fetching available models...` spinner (braille frames
  redrawn with `\x1b[H`) followed by the labels, one per `\r\n`. Parse =
  strip ANSI/OSC escapes, strip the spinner regex, keep non-empty lines that
  aren't `Fetching available models`.
- `agy` spawns a **background language server + auto-updater** that keep the pty's
  write end open, so the reader never sees EOF. Pattern: read on a thread,
  `WaitForSingleObject` on the main `agy` process (it exits after printing, ~2-3s),
  then `taskkill /F /T /PID <pid>` the whole tree, then join the reader.
- Cost ~5-6s (CLI startup + a live backend fetch). **Call it off the UI thread.**
- "Live, never cached to disk" is the owner's requirement — fetch each session;
  the in-memory `MODELS_CACHE` is fine, but don't persist a model list.

---

## 4. Method C — cloudcode-pa REST APIs (quota, project)

Same OAuth token as §2. Headers: `Authorization: Bearer <access_token>`,
`Content-Type: application/json`. (The CLI also sends `User-Agent: Go-http-client/1.1`
and `x-goog-api-client: gl-go/...`; not required for the calls that work.)
All are `POST`.

| Endpoint | Host | Body | Result |
|---|---|---|---|
| `:retrieveUserQuota` | `cloudcode-pa.googleapis.com` | `{"project":"app"}` | **200** — `buckets[]` of `{modelId, remainingFraction, resetTime, tokenType}`. modelIds are internal Gemini ids only (`gemini-2.5-flash`, `gemini-3-pro-preview`, …) — **not** display labels, no Claude/GPT-OSS. |
| `:loadCodeAssist` | `daily-cloudcode-pa.googleapis.com` | `{}` | **200** — returns `cloudaicompanionProject` (dynamic per account, e.g. `daring-scheme-jf16k`), `currentTier`, `allowedTiers`. Don't hardcode the project — discover it here. |
| `:fetchAvailableModels` | either host | `{"project": ...}` | **403 PERMISSION_DENIED** for the user OAuth token, every host/project. Gated to the CLI's internal language-server auth. **Use Method B instead** for the model list. |

Notes:
- This build uses the `daily-cloudcode-pa` host for its own calls; quota works on
  the prod `cloudcode-pa` host.
- `:retrieveUserQuota` returns **daily request counts** for Gemini model IDs only.
  It does **not** return the Weekly/Five-Hour token-capacity group limits shown by
  `agy /usage`. Those come from Method E below.

---

## 5. Method E — local gRPC language server (`RetrieveUserQuotaSummary`)

This is the method used by `agy` itself for `/usage`. It is the **only** source for
the real Weekly / Five-Hour group quota limits (Gemini group and Claude/GPT group).

### How agy exposes this

When `agy` starts (any mode — interactive, `--print`, or headless), it spawns an
in-process language server that opens **two** random TCP ports on `127.0.0.1`:

| Port | Protocol |
|---|---|
| Lower number | gRPC over **self-signed TLS** |
| Higher number | HTTP (`/healthz` only) |

The ports are logged at startup:
```
Language server listening on random port at 52309 for HTTPS (gRPC)
Language server listening on random port at 52310 for HTTP
```
(See `~/.gemini/antigravity-cli/log/cli-YYYYMMDD_HHMMSS.log`)

### Port discovery

Snapshot `Get-NetTCPConnection` **before** and **after** starting agy, take the diff:

```python
import subprocess, re
_C = 0x08000000  # CREATE_NO_WINDOW

def _local_ports() -> set[int]:
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 "
         "-ErrorAction SilentlyContinue | Select-Object LocalPort | ConvertTo-Json"],
        capture_output=True, text=True, creationflags=_C)
    return {int(m.group(1)) for m in re.finditer(r'"LocalPort":\s*(\d+)', r.stdout)}

ports_before = _local_ports()
proc = subprocess.Popen([AGY_BIN], stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, creationflags=_C)
# poll until len(new) >= 2, sleep 0.5 s between polls
grpc_port = min(_local_ports() - ports_before)   # lower = gRPC/TLS
```

### TLS certificate

The language server uses a per-session **self-signed cert**. Python's `grpcio`
requires the cert as a PEM-encoded root CA:

```python
import ssl, socket
ctx = ssl.create_default_context()
ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
with socket.create_connection(("127.0.0.1", grpc_port), timeout=5) as s:
    with ctx.wrap_socket(s, server_hostname="localhost") as ts:
        cert_pem = ssl.DER_cert_to_PEM_cert(ts.getpeercert(binary_form=True))
```

### gRPC call

```python
import grpc
creds = grpc.ssl_channel_credentials(root_certificates=cert_pem.encode())
opts = [("grpc.ssl_target_name_override","localhost"),
        ("grpc.default_authority","localhost")]
channel = grpc.secure_channel(f"127.0.0.1:{grpc_port}", creds, options=opts)
stub = channel.unary_unary(
    "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary",
    request_serializer=bytes, response_deserializer=bytes)
resp = stub(b"", timeout=10)   # empty request; resp is raw protobuf bytes
```

Dependency: `pip install grpcio` (listed in `requirements.txt`). Wait **~4 s after
port discovery** before calling — OAuth auth completes asynchronously inside agy.

### Protobuf wire format (manual decode — no .proto file needed)

The 1120-byte response decodes to this logical structure:

```
QuotaSummaryResponse {
  field[1] (bytes) → QuotaSummaryPayload {
    field[2] (bytes, repeated) → QuotaGroup {
      field[1] (bytes, repeated) → QuotaBucket {
        field[1] (bytes)   → bucket id  ("gemini-weekly", "gemini-5h", "3p-weekly", "3p-5h")
        field[2] (bytes)   → display name ("Weekly Limit", "Five Hour Limit")
        field[3] (bytes)   → period id  ("weekly", "5h")
        field[4] (float32) → remaining fraction  0.0 – 1.0  (multiply by 100 for %)
        field[6] (bytes)   → Timestamp sub-message { field[1] (varint) = Unix seconds }
        field[7] (bytes)   → human-readable message (e.g. "Refreshes in 2 days, 7 hours")
        field[8] (varint)  → is_hit bool (1 = limit reached)
      }
      field[2] (bytes) → group display name ("Gemini Models", "Claude and GPT models")
      field[3] (bytes) → group description
    }
    field[3] (bytes) → top-level description
  }
}
```

Full manual decoder in `agy_models.py::_parse_quota_proto()`. Key: wire type 5 = 32-bit
float, wire type 0 = varint, wire type 2 = length-delimited (string / sub-message).

### Teardown

Always kill the spawned agy process tree after the call:

```python
subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_C)
```

### What you get (example values, 2026-06-16)

| Group | Limit | Remaining | Reset |
|---|---|---|---|
| Gemini Models | Weekly | 40.1% | ~56 h |
| Gemini Models | Five-Hour | 100.0% | n/a |
| Claude and GPT models | Weekly | 0.0% (hit) | ~46 h |
| Claude and GPT models | Five-Hour | 100.0% (hit flag) | n/a |

### Also tried and ruled out

- **REST `/v1internal:retrieveUserQuotaSummary`** on `cloudcode-pa` → **403
  PERMISSION_DENIED** with user OAuth token; this endpoint requires the language
  server's internal credentials.
- **ConPTY `/usage`** injection → first Enter selects autocomplete (fills input),
  second Enter triggers "No matches" — the command never executes. Dead end.

---

## 6. Method D — headless prompt round-trip (`agy --print`)

For delegating a prompt and reading the answer (this is what the MCP server does).
See `agy_client.py::ask_agy()`.

- `agy --model <label> --dangerously-skip-permissions --print <prompt>` writes the
  answer **only to its SQLite trajectory store**, never stdout. Read it back from
  the newest DB in `~/.gemini/antigravity-cli/conversations/*.db` (`steps` table:
  `step_type 14` = user turn, `step_type 15` = assistant text in protobuf field 1;
  `agy_client.py` walks the protobuf wire format without a `.proto`).
- Run in a trusted `cwd` (from `trustedFolders.json`) with
  `env GEMINI_CLI_TRUST_WORKSPACE=true`, `stdin=DEVNULL`, `CREATE_NO_WINDOW`.
- `--conversation <id>` resumes; the reply appends to the same DB (multi-turn).
- **`/slash` commands do NOT work via `--print`** — the agent treats `/quota`,
  `/model`, etc. as a normal prompt and hangs analyzing the workspace. Use the
  config files / REST APIs / ConPTY methods above instead.

---

## 7. Diagnostics & discovery

- **`agy --log-file <path> models`** writes a verbose log: the OAuth flow, every
  backend URL hit, and the selected-model propagation. This is how the
  `fetchAvailableModels` / `loadCodeAssist` endpoints were discovered. Invaluable
  when reverse-engineering a new capability.
- Prior research scratch (REST probes): `~/.gemini/antigravity/brain/<id>/scratch/test_*.py`.

---

## 8. pytermgui gotchas (all hit & fixed in `tui.py`)

These are pytermgui quirks that crash or misrender; the fixes are in the code.

1. **Splitter `KeyError: 'scroll_down'` crash.** `Splitter.keys` omits the scroll
   bindings, but inherited `Container.handle_key` reads `self.keys["scroll_down"]`
   *unconditionally* on every keypress → crash when any key/mouse-wheel routes to a
   splitter. Fix in `_split()`: `sp.keys = {**sp.keys, "scroll_down": set(), "scroll_up": set()}`.
2. **Splitter `+1` position fudge.** It bumps the stored `pos.y` of any direct
   child whose `type(...).__name__ == "Container"` (a hack for bordered boxes) →
   hover/click hit the neighbouring row. Fix: columns/frames are Container
   **subclasses** (`_Column`, `_Frame`) so the name check misses.
3. **Splitter mispads unequal-height columns** (fills missing rows with a mutated
   `target_width`). Fix: pad both Splitter children to the **same** line count —
   `update_content_ui()` computes `body_h` and pads B and D to it.
4. **Compositor draws on its own thread.** `set_widgets` (`self._widgets = []` then
   append) races the draw thread's `get_lines` → `RuntimeError: list changed size
   during iteration`. Fix: `_Column` guards `set_widgets`/`get_lines` with a
   re-entrant lock.
5. **Crash logging:** uncaught exceptions on any thread are appended to
   `tui_crash.log` via `_install_crash_logging()` — the alt-screen otherwise eats
   tracebacks. Read that file first when the TUI dies.
6. **Console encoding:** printing box-drawing chars under Windows cp1252 crashes —
   set `PYTHONIOENCODING=utf-8`. Smoke test without a TTY:
   `PYTHONIOENCODING=utf-8 python -c "import tui; tui.build_window()"`.

### Styling (don't redo)
- Uniform grey `240` for every frame/divider (`BORDER`).
- Flat widgets: grey frame at rest, grey-background highlight on hover/active.
- Owner wants minimalist ("越精简越好"), iterates visually, build incrementally.

---

## 9. Pending tasks

1. **Interactive Chat session / logs**: Hook up chat session launching or log
   printing inside the TUI directly.
2. **Interactive OAuth triggers**: Wire the OAuth triggers in TUI directly if needed.
3. Decide whether to keep the crash-logging hook for release.
4. `requirements.txt` now lists `mcp[cli]`, `pytermgui`, and `grpcio`.
   `tui.py` also uses `pywin32` (`win32cred`) — add it explicitly if not implied
   by another dep.

