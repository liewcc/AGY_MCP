"""MCP server exposing Google's Antigravity CLI (`agy`) as delegatable tools.

Lets MCP clients (Claude Code, Cursor, ...) send a prompt to the Antigravity CLI and
get the model's text answer back — no API key, it uses the local OAuth login — plus
resume multi-turn conversations and read past sessions back.

Run standalone for a smoke test:  python server.py
Normally launched over stdio by the MCP client (see README).
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

from agy_client import ask_agy, run_agy_subcommand
from agy_models import list_models as _list_models
from conversations import export_conversation as _export_conversation
from conversations import fork_conversation as _fork_conversation
from conversations import format_transcript as _format_transcript
from conversations import list_conversations as _list_conversations
from conversations import rewind_conversation as _rewind_conversation
from tier_c_commands import get_config_info as _get_config_info
from tier_c_commands import list_hooks as _list_hooks
from tier_c_commands import list_skills as _list_skills
from tier_c_commands import read_hook_script as _read_hook_script
from tier_c_commands import read_keybindings as _read_keybindings
from tier_c_commands import read_mcp_config as _read_mcp_config
from tier_c_commands import read_settings as _read_settings
from tier_c_commands import read_statusline_config as _read_statusline_config
from tier_c_commands import write_hook_script as _write_hook_script
from tier_c_commands import write_keybindings as _write_keybindings
from tier_c_commands import write_mcp_config as _write_mcp_config
from tier_c_commands import write_settings as _write_settings
from tier_c_commands import write_statusline_config as _write_statusline_config
from tier_d_commands import logout as _logout
from tier_d_commands import open_path as _open_path
from tier_d_commands import show_diff as _show_diff

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
async def list_models() -> list[str]:
    """List all available Antigravity CLI models."""
    return await asyncio.to_thread(_list_models)


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


if __name__ == "__main__":
    mcp.run()
