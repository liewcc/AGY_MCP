# AGENTS.md — AGY_MCP

Working agreement for any AI coding agent (Antigravity IDE, Claude Code, etc.)
in this repository.

## Start here

1. Read [README.md](README.md) — what the project does.
2. Read [HANDOFF.md](HANDOFF.md) — current state of the TUI, all four data-access
   methods (including the gRPC approach), and pytermgui gotchas. Do this before
   touching `tui.py`.
3. [CLAUDE.md](CLAUDE.md) has the file map and conventions.

## What's in flight

The **Quota & Usage** panel (`tui.py` Quota view) is **complete with real data**:

- **Account Group Limits** (Gemini / Claude & GPT) — real Weekly and Five-Hour
  progress bars sourced from `agy`'s local gRPC language server via
  `RetrieveUserQuotaSummary`. Implemented in `agy_models.get_quota_summary()`.
- **Session Usage** — active model, elapsed time, workspace, estimated tokens from
  the newest conversation SQLite DB.
- **Individual Model Quotas** — daily request counts from the REST
  `retrieveUserQuota` endpoint.

**Next tasks**:
1. Hook up interactive chat session launching or log printing inside the TUI.
2. Wire OAuth triggers in the TUI if needed.
3. Clean up obsolete backup files (`tui.sidebar-layout.bak.py`).

---

## How the gRPC quota method works (Method E)

This is the central technique that was missing before and is now implemented.
Any agent working on quota, usage limits, or anything involving `agy`'s internal
state should understand this.

### Why not REST / ConPTY?

- **REST `retrieveUserQuota`** returns only daily request counts for Gemini models.
  It does **not** return the Weekly / Five-Hour token-capacity group limits.
- **REST `retrieveUserQuotaSummary`** (remote Google API) returns 403 for user
  OAuth tokens — it requires the language server's internal credentials.
- **ConPTY `/usage` injection** fails silently: the first Enter selects autocomplete
  (fills the input), the second Enter shows "No matches" — the command never runs.

### The correct approach

`agy` fetches group quota by calling its own in-process language server over local
gRPC. We replicate that call:

**Step 1 — discover the gRPC port:**

```python
import subprocess, re, time

_C = 0x08000000  # CREATE_NO_WINDOW

def _local_ports():
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 "
         "-ErrorAction SilentlyContinue | Select-Object LocalPort | ConvertTo-Json"],
        capture_output=True, text=True, creationflags=_C)
    return {int(m.group(1)) for m in re.finditer(r'"LocalPort":\s*(\d+)', r.stdout)}

ports_before = _local_ports()
proc = subprocess.Popen([AGY_BIN], stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, creationflags=_C)

# Poll until agy opens exactly 2 new ports (gRPC + HTTP)
while True:
    time.sleep(0.5)
    new = _local_ports() - ports_before
    if len(new) >= 2:
        grpc_port = min(new)   # lower port = gRPC/TLS
        break
```

**Step 2 — extract the self-signed TLS cert:**

```python
import ssl, socket

ctx = ssl.create_default_context()
ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
with socket.create_connection(("127.0.0.1", grpc_port), timeout=5) as s:
    with ctx.wrap_socket(s, server_hostname="localhost") as ts:
        cert_pem = ssl.DER_cert_to_PEM_cert(ts.getpeercert(binary_form=True))
```

**Step 3 — wait for auth, then call gRPC:**

```python
import grpc

time.sleep(4.0)  # OAuth completes async inside agy; wait before calling

creds = grpc.ssl_channel_credentials(root_certificates=cert_pem.encode())
opts = [("grpc.ssl_target_name_override", "localhost"),
        ("grpc.default_authority", "localhost")]
channel = grpc.secure_channel(f"127.0.0.1:{grpc_port}", creds, options=opts)
stub = channel.unary_unary(
    "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary",
    request_serializer=bytes, response_deserializer=bytes)
resp = stub(b"", timeout=10)   # empty request; returns raw protobuf bytes
channel.close()
```

**Step 4 — kill agy:**

```python
subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_C)
```

### Protobuf response structure

`resp` is ~1120 bytes of raw protobuf (no .proto file needed — parse wire format
manually with `struct.unpack`). See `agy_models._parse_quota_proto()` for the full
decoder. The logical shape:

```
field[1] → payload:
  field[2] (repeated) → group:
    field[1] (repeated) → bucket:
      field[1] bytes   → id ("gemini-weekly", "gemini-5h", "3p-weekly", "3p-5h")
      field[2] bytes   → display name ("Weekly Limit", "Five Hour Limit")
      field[3] bytes   → period ("weekly", "5h")
      field[4] float32 → remaining fraction 0.0–1.0   ← the percentage
      field[6] bytes   → Timestamp { field[1] varint = Unix seconds }  ← reset time
      field[7] bytes   → human message string
      field[8] varint  → is_hit bool
    field[2] bytes → group name ("Gemini Models", "Claude and GPT models")
  field[3] bytes → top description
```

Wire types: `0` = varint, `2` = length-delimited, `5` = 32-bit float.

### End-to-end implementation

- `agy_models.get_quota_summary()` — full implementation of the above, ~20 s total.
- `agy_models._parse_quota_proto()` — manual protobuf decoder.
- `tui._fetch_quota_summary_async()` — background thread that calls `get_quota_summary()`
  and stores the result in `QUOTA_SUMMARY_CACHE`, then redraws the Quota view.
- `tui._content_widgets("Quota")` — renders the bars from `QUOTA_SUMMARY_CACHE`.

### Important caveats

- Takes **~15–20 s** (agy startup + auth wait + network). Always call off the UI thread.
- Requires `grpcio` (`pip install grpcio`). Listed in `requirements.txt`.
- Uses `127.0.0.1` explicitly — `localhost` resolves to `[::1]` on Windows 11
  and the connection is refused.
- Must use `grpc.ssl_target_name_override` = `"localhost"` because the cert's CN
  is `localhost`, not `127.0.0.1`.

---

## Rules of engagement

- **Platform: Windows**, Python 3.10+ (PowerShell shell, backslash paths).
- **TUI library is pytermgui** — picked for tiny disk footprint. Don't replace it
  without the owner's OK.
- **Keep it minimalist.** The owner repeatedly asks for the simplest possible UI:
  flat widgets, no 3D, uniform grey `240` frames. Match the existing style in
  `tui.py` (palette in HANDOFF.md §8).
- **Validate before claiming done:**
  `PYTHONIOENCODING=utf-8 python -c "import tui; tui.build_window()"` must pass.
  The full UI needs a real terminal (`run.bat`); `build_window()` does not.
- Use `PYTHONIOENCODING=utf-8` whenever printing rendered output (box-drawing
  chars crash under Windows cp1252).
- **Build incrementally**, confirm visuals with the owner (they review via
  screenshots), then expand. Don't batch many speculative changes.
- `run.bat` runs the TUI; the MCP server runs via the client config / `server.py`.

## Definition of done for a TUI change

1. `build_window()` imports and constructs without error.
2. `run.bat` renders correctly in a real terminal.
3. Styling matches the grey-240 / flat / minimalist conventions.
4. Owner has seen and approved the result.
