# CLAUDE.md — AGY_MCP

Project guidance for AI assistants working in this repo. Keep this file current.

## What this is

`AGY_MCP` is an MCP server wrapping Google's **Antigravity CLI (`agy`)**. It lets
MCP clients delegate a prompt to `agy` and read the answer back — no API key, it
rides the CLI's local OAuth login. Full details in [README.md](README.md).

**Tools exposed:** `ask_antigravity`, `list_conversations`, `read_conversation`.

## Layout

| File | Role |
|------|------|
| `server.py` | FastMCP server — entry point for MCP clients. |
| `agy_client.py` | Spawns `agy.exe`, reads answers from the CLI's SQLite store. |
| `agy_models.py` | Live model list — runs `agy models` under a Windows ConPTY. |
| `conversations.py` | List / read past conversations. |
| `tui.py` | **Terminal control panel** (pytermgui). Launched by `run.bat`. |
| `requirements.txt` | `mcp[cli]`, `pytermgui`, `grpcio`. |
| `setup.bat` / `run.bat` | Install deps / launch the TUI. |

## Active work

The pytermgui TUI control panel is **functional**:
- Left rail: `Models` / `Quota` navigation.
- Models view: lists live models and allows selection.
- Quota view: **Completed with real gRPC data**.
  - *Group Limits*: real Weekly / Five-Hour progress bars for Gemini and Claude & GPT
    groups, sourced from `agy`'s local gRPC language server
    (`RetrieveUserQuotaSummary`). See AGENTS.md for the full technique and code.
  - *Session Usage*: active model, elapsed time, workspace, estimated tokens from SQLite.
  - *Individual Model Quotas*: daily request counts from REST `retrieveUserQuota`.
- **Next steps**: Incrementally add chat session capabilities, hook up real OAuth
  login triggers, or refine the layout based on owner visual feedback.


## Conventions

- **Platform is Windows.** Python 3.10+ (dev machine runs 3.14). Paths use
  backslashes; the dev shell is PowerShell.
- **TUI = pytermgui**, chosen for minimal disk footprint (only `wcwidth`). Do not
  swap to Textual/Rich without asking the owner.
- **Frame colour is uniform grey `240`.** Palette is in HANDOFF.md §8.
- **Smoke-test the TUI without a TTY:**
  `PYTHONIOENCODING=utf-8 python -c "import tui; tui.build_window()"`.
  Set `PYTHONIOENCODING=utf-8` for anything that prints box-drawing characters
  (Windows cp1252 otherwise crashes).
- `run.bat` launches the **TUI**, not the server. The server is launched by the
  MCP client config (`python server.py`).

## How to run

```bat
setup.bat     REM one-time: install dependencies
run.bat       REM launch the control-panel TUI
```

## Working agreement

- The owner iterates visually via screenshots and wants a **minimalist** UI
  ("越精简越好" — the simpler the better). Prefer flat, low-chrome widgets.
- Build features **incrementally** and confirm look/behaviour before expanding.
- See [AGENTS.md](AGENTS.md) for the shared agent working agreement.
