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
| `requirements.txt` | `mcp[cli]`, `pytermgui`, `pywin32`. |
| `setup.bat` / `run.bat` | Install deps / launch the TUI. |

## Active work

The pytermgui TUI control panel is **functional**: full-height left rail
(`Models` / `Quota`) + status panel + main panel. The Models view lists the live
models (click to switch); the Quota view is the next task. **Before editing
`tui.py`, read [HANDOFF.md](HANDOFF.md)** — it documents how to talk to `agy`
(config files, ConPTY model list, cloudcode-pa REST APIs, `--print` round-trip)
and the pytermgui gotchas already worked around.

## Conventions

- **Platform is Windows.** Python 3.10+ (dev machine runs 3.14). Paths use
  backslashes; the dev shell is PowerShell.
- **TUI = pytermgui**, chosen for minimal disk footprint (only `wcwidth`). Do not
  swap to Textual/Rich without asking the owner.
- **Frame colour is uniform grey `240`.** Palette is in HANDOFF.md §7.
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
