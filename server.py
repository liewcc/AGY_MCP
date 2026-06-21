"""MCP server exposing Google's Antigravity CLI (`agy`) as delegatable tools.

Lets MCP clients (Claude Code, Cursor, ...) send a prompt to the Antigravity CLI and
get the model's text answer back — no API key, it uses the local OAuth login — plus
resume multi-turn conversations and read past sessions back.

Run standalone for a smoke test:  python server.py
Normally launched over stdio by the MCP client (see README).

## Delegation Protocol (Claude ↔ agy)

When Claude delegates a task to agy via `ask_antigravity`, a timeout error does NOT
mean failure — agy may have already completed the work silently in the background.

Rules Claude MUST follow every time it delegates to agy:

1. **Completion marker** — always append this instruction to the prompt:
       "When you finish, write the single word DONE to a file called `.agy_done`
        in the working directory. Overwrite if it already exists."

2. **After a timeout** — do NOT redo the work. Instead verify completion:
   a. Check `git log --oneline -3` — a new commit from agy means it succeeded.
   b. Call `read_conversation` with the last `conversation_id` — read what agy
      actually did; the trajectory DB is written even when the MCP transport times out.
   c. Check target file mtime — if modified after the call started, agy wrote it.
   d. Only if all three show no progress: agy is still running or crashed — wait or
      investigate before retrying.
   e. `.agy_done` marker (optional): append "write DONE to `.agy_done` when finished"
      to the prompt for an explicit signal on long tasks.

3. **Timeout sizing** — calibrate per task complexity:
   - Single-file edit  : 120 s
   - Multi-file refactor: 240 s
   - Install / build   : 360 s

4. **Idempotent prompts** — phrase tasks as "ensure X exists / is set to Y" so a
   retry is always safe, never duplicating work.
"""
import asyncio
import datetime
import os
import sys
from typing import Optional

from mcp.server.fastmcp import FastMCP

# Startup breadcrumb — written before any MCP negotiation so we can tell if
# Claude Code even launched the process.
_log = os.path.join(os.path.dirname(__file__), "_startup.log")
with open(_log, "a", encoding="utf-8") as _f:
    _f.write(f"started pid={os.getpid()} cwd={os.getcwd()} "
             f"python={sys.version.split()[0]}\n")

from agy_core import (
    ask_agy, run_agy_subcommand,
    list_models as _list_models,
    get_quota_summary as _get_quota_summary,
    get_context_stats as _get_context_stats,
    export_conversation as _export_conversation,
    fork_conversation as _fork_conversation,
    format_transcript as _format_transcript,
    list_conversations as _list_conversations,
    rewind_conversation as _rewind_conversation,
    get_config_info as _get_config_info,
    list_hooks as _list_hooks,
    list_skills as _list_skills,
    read_hook_script as _read_hook_script,
    read_keybindings as _read_keybindings,
    read_mcp_config as _read_mcp_config,
    read_settings as _read_settings,
    read_statusline_config as _read_statusline_config,
    write_hook_script as _write_hook_script,
    write_keybindings as _write_keybindings,
    write_mcp_config as _write_mcp_config,
    write_settings as _write_settings,
    write_statusline_config as _write_statusline_config,
    logout as _logout,
    open_path as _open_path,
    show_diff as _show_diff,
    agent_session_state as _agent_session_state,
    list_tasks as _list_tasks,
    ask_btw as _ask_btw,
    run_goal as _run_goal,
    start_grill_me as _start_grill_me,
    start_planning as _start_planning,
    start_schedule as _start_schedule,
    start_teamwork_preview as _start_teamwork_preview,
    toggle_fast_mode as _toggle_fast_mode,
    set_model as _set_model,
)

mcp = FastMCP("agy-mcp")


@mcp.tool()
async def ask_antigravity(
    prompt: str,
    model: Optional[str] = None,
    add_dirs: Optional[list[str]] = None,
    timeout: Optional[int] = None,
    conversation: Optional[str] = None,
    working_dir: Optional[str] = None,
) -> dict:
    """Send a prompt to the Antigravity CLI (Google's Gemini CLI) and get its answer.

    A full headless round-trip: invokes `agy --print` console-less and reads the
    assistant reply back from its trajectory store. No API key — uses the local
    OAuth session.

    For a multi-turn conversation, pass the `conversation_id` returned by a previous
    call back in as `conversation`; the new turn is appended and context carries over.

    IMPORTANT — auto git behaviour: when agy writes or edits code files, it will
    automatically run `git add / commit / push` on its own without being asked.
    If you do NOT want agy to commit or push, explicitly say so in the prompt
    (e.g. "do not commit or push, just write the file").

    Args:
        prompt:       The prompt / question to send.
        model:        Model display name (default "Gemini 3 Pro", which resolves to
                      "Gemini 3.5 Flash (Medium)"). Pass another name to switch.
        add_dirs:     Absolute folder paths to expose to the agent for file/image
                      analysis. Reference the file path inside `prompt` — there is no
                      separate upload mechanism.
        timeout:      Hard cap in seconds before the call is abandoned (default 120).
        conversation: Conversation id to resume; omit to start a fresh conversation.
        working_dir:  Optional working directory path to run the command in.

    Returns:
        {"answer": <model text>, "conversation_id": <id to continue this thread>}
    """
    answer, conv_id = await asyncio.to_thread(
        ask_agy, prompt, model, add_dirs, timeout, conversation, working_dir
    )
    return {"answer": answer, "conversation_id": conv_id}


@mcp.tool()
async def list_conversations(limit: int = 20) -> list[dict]:
    """List recent Antigravity CLI conversations, newest first.

    Args:
        limit: Maximum number of conversations to return (default 20).

    Returns:
        A list of {id, title, user_turns, modified_iso, db_bytes}. `title` is the
        first user prompt; pass `id` to `read_conversation` to see the full transcript.
    """
    items = await asyncio.to_thread(_list_conversations, limit)
    for c in items:
        c["modified_iso"] = datetime.datetime.fromtimestamp(
            c.pop("modified")
        ).isoformat(timespec="seconds")
    return items


@mcp.tool()
async def read_conversation(conversation_id: str) -> str:
    """Return the full transcript of a past Antigravity CLI conversation.

    Args:
        conversation_id: The conversation id (from `list_conversations` or the
                         `conversation_id` returned by `ask_antigravity`).

    Returns:
        A readable transcript with alternating USER / MODEL turns.
    """
    return await asyncio.to_thread(_format_transcript, conversation_id)


@mcp.tool()
async def fork_conversation(conversation_id: str) -> dict:
    """Clone an existing conversation into a new independent copy (/fork).

    The new conversation starts with the same history as the source but gets a
    fresh UUID, so future turns won't pollute the original.  Pass the returned
    `new_conversation_id` to `ask_antigravity` to continue from the branch point.

    Args:
        conversation_id: The id of the conversation to clone.

    Returns:
        {"forked_from": <source_id>, "new_conversation_id": <new_id>}
    """
    return await asyncio.to_thread(_fork_conversation, conversation_id)


@mcp.tool()
async def rewind_conversation(conversation_id: str, turns: int = 1) -> dict:
    """Remove the last N user turns (and their replies) from a conversation (/rewind).

    Useful after a bad prompt: delete the last exchange and try a different
    approach while keeping the earlier context intact.

    Args:
        conversation_id: The id of the conversation to rewind.
        turns:           How many user turns to remove from the end (default 1).

    Returns:
        {"conversation_id", "turns_removed", "steps_deleted", "remaining_turns"}
    Raises an error if the conversation has too few turns to rewind safely.
    """
    return await asyncio.to_thread(_rewind_conversation, conversation_id, turns)


@mcp.tool()
async def export_conversation(
    conversation_id: str,
    output_path: Optional[str] = None,
) -> dict:
    """Save a conversation transcript to a markdown file (/export).

    Writes USER / MODEL turns with ### headers.  If `output_path` is omitted
    the file is saved as <conversation_id>.md in the server's working directory.

    Args:
        conversation_id: The id of the conversation to export.
        output_path:     Absolute path for the output file (optional).

    Returns:
        {"saved_to": <path>, "turns": <count>, "chars": <file_length>}
    """
    return await asyncio.to_thread(_export_conversation, conversation_id, output_path)


@mcp.tool()
async def list_models() -> dict:
    """List all available Antigravity CLI models.

    RECOMMENDED WORKFLOW before delegating tasks to agy:
      1. Call get_quota()       — check remaining quota for Gemini and Claude/GPT groups.
      2. Call list_models()     — see which models are available.
      3. Call get_context_stats() — check how much context the active session has consumed.
      4. Based on quota and context, decide how many / how heavy tasks to delegate.

    Returns:
        {"models": [<name>, ...], "count": <n>}
    """
    models = await asyncio.to_thread(_list_models)
    return {"models": models, "count": len(models)}


@mcp.tool()
async def get_quota() -> dict:
    """Fetch weekly/five-hour group quota from agy's local gRPC server.

    Starts agy headlessly, queries RetrieveUserQuotaSummary, then kills agy.
    Takes ~15-20 s. Returns None values if gRPC is unavailable.

    IMPORTANT: weekly_pct and fiveh_pct are REMAINING quota percentages (not used).
    100% = fully available. 0% = exhausted.

    Two quota groups:
      - "gemini":    Gemini models (Flash, Pro, etc.)
      - "claude_gpt": Claude and GPT models

    If a group's fiveh_pct or weekly_pct is low, avoid heavy tasks on that group
    until fiveh_reset_ts or weekly_reset_ts passes.

    Returns:
        {"quota": <dict> | null}
    """
    result = await asyncio.to_thread(_get_quota_summary)
    return {"quota": result}


@mcp.tool()
async def get_context_stats(conversation_id: Optional[str] = None) -> dict:
    """Return context/token usage stats for the active or most recent agy conversation.

    Use this to gauge how saturated the current agy session is before sending more work.
    pct_used = total_tokens / context_limit * 100. When pct_used is high (>70%),
    consider starting a fresh conversation to avoid context degradation.

    Args:
        conversation_id: Specific conversation ID to inspect; omit for the live/latest session.

    Returns:
        Token counts, step breakdown, and conversation metadata.
    """
    return await asyncio.to_thread(_get_context_stats, conversation_id)


@mcp.tool()
async def debug_model_raw() -> str:
    """Debug: return raw ConPTY output from /model injection (temporary diagnostic)."""
    from agy_core import _debug_model_raw as _dmr
    return await asyncio.to_thread(_dmr)


@mcp.tool()
async def get_changelog() -> str:
    """Show the Antigravity CLI changelog and release notes."""
    return await asyncio.to_thread(run_agy_subcommand, "changelog")


@mcp.tool()
async def list_plugins() -> str:
    """List all installed/imported Antigravity CLI plugins."""
    return await asyncio.to_thread(run_agy_subcommand, "plugin", "list")


@mcp.tool()
async def import_plugins(source: Optional[str] = None) -> str:
    """Import plugins from gemini or claude.

    Args:
        source: 'gemini' or 'claude'. Omit to import from all sources.
    """
    args = ["plugin", "import"]
    if source:
        args.append(source)
    return await asyncio.to_thread(run_agy_subcommand, *args)


@mcp.tool()
async def install_plugin(target: str) -> str:
    """Install an Antigravity CLI plugin.

    Args:
        target: Local path to a plugin directory, or 'plugin@marketplace' format.
    """
    return await asyncio.to_thread(run_agy_subcommand, "plugin", "install", target, timeout=60)


@mcp.tool()
async def uninstall_plugin(name: str) -> str:
    """Uninstall a plugin by name."""
    return await asyncio.to_thread(run_agy_subcommand, "plugin", "uninstall", name)


@mcp.tool()
async def enable_plugin(name: str) -> str:
    """Enable a previously disabled plugin."""
    return await asyncio.to_thread(run_agy_subcommand, "plugin", "enable", name)


@mcp.tool()
async def disable_plugin(name: str) -> str:
    """Disable a plugin without uninstalling it."""
    return await asyncio.to_thread(run_agy_subcommand, "plugin", "disable", name)


# ---- Tier C: configuration file read/write ----


@mcp.tool()
async def read_settings() -> dict:
    """Read the current settings.json (/config, /settings).

    Returns:
        {"allowNonWorkspaceAccess", "enableTelemetry", "model", "permissions",
         "trustedWorkspaces", ...} or {"error": <msg>}
    """
    return await asyncio.to_thread(_read_settings)


@mcp.tool()
async def write_settings(settings: dict) -> dict:
    """Write settings back to settings.json (/config, /settings).

    Replaces the entire settings file. Ensure you pass a complete valid JSON object.

    Args:
        settings: The settings dictionary to write.

    Returns:
        {"saved_to": <path>, "keys": [<updated_keys>]} or error dict.
    """
    return await asyncio.to_thread(_write_settings, settings)


@mcp.tool()
async def read_keybindings() -> dict:
    """Read the current keybindings.json (/keybindings).

    Returns:
        {"action.name": ["key1", "key2", ...], ...} or empty dict.
    """
    return await asyncio.to_thread(_read_keybindings)


@mcp.tool()
async def write_keybindings(keybindings: dict) -> dict:
    """Write keybindings back to keybindings.json (/keybindings).

    Args:
        keybindings: The keybindings dictionary to write.

    Returns:
        {"saved_to": <path>, "actions": <count>} or error dict.
    """
    return await asyncio.to_thread(_write_keybindings, keybindings)


@mcp.tool()
async def list_skills() -> dict:
    """List all available skill files (/skills).

    Recursively finds .md files in ~/.gemini/antigravity-cli.

    Returns:
        {"skills": [<file_paths>], "count": <total>}
    """
    return await asyncio.to_thread(_list_skills)


@mcp.tool()
async def read_mcp_config() -> dict:
    """Read the MCP servers configuration (/mcp).

    Returns the mcp.json content defining external MCP servers and their commands.

    Returns:
        {"mcpServers": {"server_id": {"command": "...", "args": [...]}}}
    """
    return await asyncio.to_thread(_read_mcp_config)


@mcp.tool()
async def write_mcp_config(config: dict) -> dict:
    """Write MCP servers configuration (/mcp).

    Args:
        config: The mcp.json structure with mcpServers definitions.

    Returns:
        {"saved_to": <path>, "servers": [<server_ids>]} or error dict.
    """
    return await asyncio.to_thread(_write_mcp_config, config)


@mcp.tool()
async def read_statusline_config() -> dict:
    """Read the statusline configuration (/statusline).

    Returns the statusline.yaml content defining TUI status bar layout and components.

    Returns:
        {"layout": {"left": [...], "right": [...]}, ...}
    """
    return await asyncio.to_thread(_read_statusline_config)


@mcp.tool()
async def write_statusline_config(config: dict) -> dict:
    """Write statusline configuration (/statusline).

    Args:
        config: The statusline configuration dict.

    Returns:
        {"saved_to": <path>, "keys": [<top_level_keys>]} or error dict.
    """
    return await asyncio.to_thread(_write_statusline_config, config)


@mcp.tool()
async def list_hooks() -> dict:
    """List all hook scripts (/hooks).

    Returns all executable scripts in ~/.gemini/antigravity-cli/hooks/
    (pre-prompt, post-response, etc.).

    Returns:
        {"hooks": [{"name", "executable", "size", "path"}], "count": <total>}
    """
    return await asyncio.to_thread(_list_hooks)


@mcp.tool()
async def read_hook_script(hook_name: str) -> dict:
    """Read a specific hook script (/hooks).

    Args:
        hook_name: Name of the hook (e.g., "pre-prompt").

    Returns:
        {"content": <script>, "path": <path>, "executable": <bool>} or error dict.
    """
    return await asyncio.to_thread(_read_hook_script, hook_name)


@mcp.tool()
async def write_hook_script(hook_name: str, content: str, executable: bool = True) -> dict:
    """Create or update a hook script (/hooks).

    Hook scripts are executed at specific lifecycle events. Common names:
    - pre-prompt: runs before sending a prompt to the model
    - post-response: runs after receiving a response

    Args:
        hook_name: Name of the hook to create/update.
        content: Script content (bash, python, etc.).
        executable: Make the script executable (default True).

    Returns:
        {"saved_to": <path>, "executable": <bool>} or error dict.
    """
    return await asyncio.to_thread(_write_hook_script, hook_name, content, executable)


@mcp.tool()
async def get_config_info() -> dict:
    """Get a summary of current configuration state.

    Useful for debugging or understanding what settings are active.
    Combines info from all configuration files: settings.json, keybindings.json,
    mcp.json, statusline.yaml, hooks, and skills.

    Returns:
        {"settings": {...}, "keybindings": {...}, "mcp": {...}, "statusline": {...},
         "hooks": {...}, "skills": {...}, "agy_home": <path>}
    """
    return await asyncio.to_thread(_get_config_info)


# ---- Tier D: shell subcommands and file operations ----


@mcp.tool()
async def show_diff(path: Optional[str] = None, working_dir: Optional[str] = None) -> str:
    """Show git diff for workspace changes (/diff).

    Displays modified files and their line-by-line diffs.  If `path` is omitted,
    shows all changes; if provided, shows changes for that file or directory tree.

    Args:
        path: Optional file or directory path (relative or absolute).
        working_dir: Directory to run git from (default: current directory).

    Returns:
        git diff output as text, or error message if git fails.
    """
    return await asyncio.to_thread(_show_diff, path, working_dir)


@mcp.tool()
async def open_path(path: str) -> dict:
    """Open a file or directory in the system default application (/open).

    Launches the default editor for text files, image viewer for images, etc.
    On Windows uses os.startfile; on macOS/Linux uses open/xdg-open.

    Args:
        path: Absolute or relative path to file or directory.

    Returns:
        {"opened": <absolute_path>, "status": "success"} or error dict.
    """
    return await asyncio.to_thread(_open_path, path)


@mcp.tool()
async def logout() -> dict:
    """Delete OAuth credentials and logout from Google (/logout).

    Removes the local token files so the next `agy` run will require
    re-authentication. Safe to call multiple times.

    Returns:
        {"deleted": [<files>], "status": "success"} or status dict.
    """
    return await asyncio.to_thread(_logout)


# ---- Tier E: live runtime state of a running agy session (gRPC attach) ----


@mcp.tool()
async def list_tasks(conversation_id: Optional[str] = None) -> dict:
    """List background shell commands in the running Antigravity session (/tasks).

    Attaches to the user's *already-running* interactive `agy` process via its local
    gRPC language server (this state is in-memory and only exists while agy runs —
    there is nothing to read when no session is open).

    Args:
        conversation_id: Inspect a specific conversation; default = the active one.

    Returns:
        {"conversation_id", "tasks": [{command, cwd, summary, action, background,
         wait_ms}]} — or {"status": "no running agy session found"}.
    """
    return await asyncio.to_thread(_list_tasks, conversation_id)


@mcp.tool()
async def agent_session_state(conversation_id: Optional[str] = None) -> dict:
    """Agent / subagent runtime state of the running Antigravity session (/agents).

    Attaches to the running `agy` process (see `list_tasks`) and returns the active
    conversation, every loaded trajectory id (parent + spawned subagents), and the
    tool actions currently in flight.

    Args:
        conversation_id: Inspect a specific conversation; default = the active one.

    Returns:
        {"conversation_id", "conversation_ids", "tool_actions": [{action, summary}],
         "snapshot_bytes"} — or {"status": "no running agy session found"}.
    """
    return await asyncio.to_thread(_agent_session_state, conversation_id)


# ---- Tier F: ConPTY pseudo-terminal injection for interactive-only commands ----


@mcp.tool()
async def set_model(model_name: str) -> dict:
    """Switch the active agy model (/model).

    Launches agy interactively, injects /model <name>, and returns the
    confirmation text. The change persists across sessions (written to
    settings.json by agy itself).

    WARNING: This tool is currently broken — it returns {"output": "", "pid": ...}
    but does NOT write to settings.json and the model does not change.
    Workaround: directly edit the "model" field in
    C:\\Users\\cclie\\.gemini\\antigravity-cli\\settings.json.
    Use `list_models` to get valid model display names.

    Args:
        model_name: Model display name (e.g. "Gemini 3.5 Flash (High)").
                    Use `list_models` to see available names.

    Returns:
        {"output": <confirmation text>, "pid": <agy pid>} or {"error": <msg>}
    """
    return await asyncio.to_thread(_set_model, model_name)


@mcp.tool()
async def toggle_fast_mode() -> dict:
    """Toggle agy's fast/thinking mode inside a fresh ConPTY session (/fast).

    Launches agy interactively, injects /fast, and returns the mode-change
    confirmation text captured from the TUI.

    Returns:
        {"output": <confirmation text>, "pid": <agy pid>} or {"error": <msg>}
    """
    return await asyncio.to_thread(_toggle_fast_mode)


@mcp.tool()
async def run_goal(description: str) -> dict:
    """Set an autonomous execution goal in a fresh agy session (/goal).

    Launches agy interactively, injects /goal <description>, captures the
    initial ~10 s of output, then kills the session.  For a persistent
    long-running goal the user should run /goal directly in their own terminal.

    Args:
        description: The goal to pursue (e.g. "refactor the auth module").

    Returns:
        {"output": <initial response>, "pid": <agy pid>} or {"error": <msg>}
    """
    return await asyncio.to_thread(_run_goal, description)


@mcp.tool()
async def start_planning(description: str = "") -> dict:
    """Start a multi-turn planning session and capture the initial plan (/planning).

    Args:
        description: Optional planning prompt or context.

    Returns:
        {"output": <plan text>, "pid": <agy pid>} or {"error": <msg>}
    """
    return await asyncio.to_thread(_start_planning, description)


@mcp.tool()
async def start_schedule(description: str) -> dict:
    """Set a scheduled/cron task in agy and capture the confirmation (/schedule).

    Args:
        description: Schedule description (e.g. "run tests every day at 9am").

    Returns:
        {"output": <schedule confirmation>, "pid": <agy pid>} or {"error": <msg>}
    """
    return await asyncio.to_thread(_start_schedule, description)


@mcp.tool()
async def start_grill_me() -> dict:
    """Start an interactive Q&A alignment session and capture the first prompt (/grill-me).

    Returns:
        {"output": <first grill-me question>, "pid": <agy pid>} or {"error": <msg>}
    """
    return await asyncio.to_thread(_start_grill_me)


@mcp.tool()
async def start_teamwork_preview() -> dict:
    """Launch multi-agent teamwork preview and capture the initial output (/teamwork-preview).

    Returns:
        {"output": <teamwork preview text>, "pid": <agy pid>} or {"error": <msg>}
    """
    return await asyncio.to_thread(_start_teamwork_preview)


@mcp.tool()
async def ask_btw(
    query: str,
    conversation: Optional[str] = None,
) -> dict:
    """Send a background side-question via agy --print (/btw).

    agy's /btw asks a question in the background without interrupting the current
    session.  We approximate it with a headless --print call: same OAuth auth,
    independent conversation.

    Args:
        query:        The question to ask.
        conversation: Conversation id to resume (optional).

    Returns:
        {"answer": <model text>, "conversation_id": <id>}
    """
    return await asyncio.to_thread(_ask_btw, query, conversation)


if __name__ == "__main__":
    mcp.run()
