"""MCP server exposing Google's Antigravity CLI (`agy`) as delegatable tools.

Lets MCP clients (Claude Code, Cursor, ...) send a prompt to the Antigravity CLI and
get the model's text answer back — no API key, it uses the local OAuth login — plus
resume multi-turn conversations and read past sessions back.

Run standalone for a smoke test:  python server.py
Normally launched over stdio by the MCP client (see README).
"""
import asyncio
import datetime
from typing import Optional

from mcp.server.fastmcp import FastMCP

from agy_client import ask_agy
from conversations import format_transcript as _format_transcript
from conversations import list_conversations as _list_conversations

mcp = FastMCP("agy-mcp")


@mcp.tool()
async def ask_antigravity(
    prompt: str,
    model: Optional[str] = None,
    add_dirs: Optional[list[str]] = None,
    timeout: Optional[int] = None,
    conversation: Optional[str] = None,
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

    Returns:
        {"answer": <model text>, "conversation_id": <id to continue this thread>}
    """
    answer, conv_id = await asyncio.to_thread(
        ask_agy, prompt, model, add_dirs, timeout, conversation
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


if __name__ == "__main__":
    mcp.run()
