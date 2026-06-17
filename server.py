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


if __name__ == "__main__":
    mcp.run()
