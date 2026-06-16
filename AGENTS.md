# AGENTS.md — AGY_MCP

Working agreement for any AI coding agent (Antigravity IDE, Claude Code, etc.)
in this repository.

## Start here

1. Read [README.md](README.md) — what the project does.
2. Read [HANDOFF.md](HANDOFF.md) — **current state of the in-progress TUI work,
   the two pending edits, and pytermgui gotchas.** Do this before touching `tui.py`.
3. [CLAUDE.md](CLAUDE.md) has the file map and conventions.

## What's in flight

We completed the **Quota & Usage** panel implementation in `tui.py`:
- Individual model API quotas are fetched dynamically via GCP CloudCode API.
- Account-level group quotas (Gemini and Claude groups) are formatted with progress bars.
- Session usage stats (active model, session timer, current workspace, and estimated tokens used) are tracked dynamically by reading the newest SQLite trajectory database of the active chat.
- Green selection dot styling was also successfully applied to the selected model row in `Models` view.

**Next tasks / Handoff**:
1. Hook up interactive chat session launching or logs directly inside the TUI.
2. Hook up authentications triggers directly.
3. Clean up backup files if no longer needed.


## Rules of engagement

- **Platform: Windows**, Python 3.10+ (PowerShell shell, backslash paths).
- **TUI library is pytermgui** — picked for tiny disk footprint. Don't replace it
  without the owner's OK.
- **Keep it minimalist.** The owner repeatedly asks for the simplest possible UI:
  flat widgets, no 3D, uniform grey `240` frames. Match the existing style in
  `tui.py` (palette in HANDOFF.md §7).
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
