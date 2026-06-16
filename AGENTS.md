# AGENTS.md — AGY_MCP

Working agreement for any AI coding agent (Antigravity IDE, Claude Code, etc.)
in this repository.

## Start here

1. Read [README.md](README.md) — what the project does.
2. Read [HANDOFF.md](HANDOFF.md) — **current state of the TUI work, details of the quota API, and task overview.** Do this before touching `tui.py`.
3. [CLAUDE.md](CLAUDE.md) has the file map and conventions.

## What's in flight

Building a **pytermgui** terminal control panel (`tui.py`) for the MCP server.
We completed fixing the `ModelButton` line wrapping/highlighting bugs.
**Next task:** Redesign the TUI layout to add a left sidebar with `Models` and `Quota` navigation. Remove the split Models/MCP Tools card, Ask input, and Send button. The right content panel must display the models list or retrieved quota information depending on the sidebar selection.

## Rules of engagement

- **Platform: Windows**, Python 3.10+ (PowerShell shell, backslash paths).
- **TUI library is pytermgui** — picked for tiny disk footprint. Don't replace it without the owner's OK.
- **Keep it minimalist.** The owner repeatedly asks for the simplest possible UI: flat widgets, no 3D, uniform grey `240` frames. Match the existing style in `tui.py`.
- **Validate before claiming done:**
  `PYTHONIOENCODING=utf-8 python -c "import tui; tui.build_window()"` must pass. The full UI needs a real terminal (`run.bat`); `build_window()` does not.
- Use `PYTHONIOENCODING=utf-8` whenever printing rendered output (box-drawing chars crash under Windows cp1252).
- **Build incrementally**, confirm visuals with the owner (they review via screenshots), then expand. Don't batch speculative changes.
- `run.bat` runs the TUI; the MCP server runs via the client config / `server.py`.

## Definition of done for a TUI change

1. `build_window()` imports and constructs without error.
2. `run.bat` renders correctly in a real terminal.
3. Styling matches the grey-240 / flat / minimalist conventions.
4. Owner has seen and approved the result.
