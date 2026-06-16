"""MCP server exposing Google's Antigravity CLI (`agy`) as a delegatable tool.

Lets MCP clients (Claude Code, Cursor, ...) send a prompt to the Antigravity CLI and
get the model's text answer back — no API key, it uses the local OAuth login.

Run standalone for a smoke test:  python server.py
Normally launched over stdio by the MCP client (see README).
"""
import asyncio
from typing import Optional

from mcp.server.fastmcp import FastMCP

from agy_client import DEFAULT_MODEL, ask_agy

mcp = FastMCP("agy-mcp")


@mcp.tool()
async def ask_antigravity(
    prompt: str,
    model: Optional[str] = None,
    add_dirs: Optional[list[str]] = None,
    timeout: Optional[int] = None,
) -> str:
    """Send a prompt to the Antigravity CLI (Google's Gemini CLI) and return its text answer.

    A full headless round-trip: invokes `agy --print` console-less and reads the
    assistant reply back from its trajectory store. No API key — uses the local
    OAuth session.

    Args:
        prompt:   The prompt / question to send.
        model:    Model display name (default "Gemini 3 Pro", which resolves to
                  "Gemini 3.5 Flash (Medium)"). Pass another name to switch.
        add_dirs: Absolute folder paths to expose to the agent for file/image
                  analysis. Reference the file path inside `prompt` — there is no
                  separate upload mechanism.
        timeout:  Hard cap in seconds before the call is abandoned (default 120).

    Returns:
        The model's text answer.
    """
    answer, _db = await asyncio.to_thread(ask_agy, prompt, model, add_dirs, timeout)
    return answer


if __name__ == "__main__":
    mcp.run()
