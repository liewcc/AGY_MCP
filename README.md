# AGY_MCP

An MCP server that exposes **Google's Antigravity CLI (`agy`)** as a tool, so MCP
clients (Claude Code, Cursor, …) can delegate a prompt to it and get the model's text
answer back — **no API key required**; it rides the CLI's local OAuth login.

This is the standalone sibling of `Gemi_MCP` (which drives the Gemini *web UI* via a
browser engine). AGY_MCP shares no code with that engine: it just spawns `agy.exe` and
reads the answer back from the CLI's local trajectory store.

## How it works

`agy --print "<prompt>"` runs the model to completion but writes the answer **only** to
its SQLite trajectory store, never to stdout. So `agy_client.py`:

1. spawns `agy.exe` console-less with `stdin=DEVNULL` (otherwise it hangs waiting on stdin),
   from a folder the CLI already trusts (otherwise it blocks on a trust prompt);
2. reads the reply back from the newest
   `~/.gemini/antigravity-cli/conversations/<id>.db` (table `steps`, rows where
   `step_type=15`, protobuf field 1).

## Prerequisites

- **Antigravity CLI installed** — `agy.exe` at `%LOCALAPPDATA%\agy\bin\agy.exe`, logged
  in (OAuth, stored in Windows Credential Manager as `gemini:antigravity`).
- **At least one trusted folder** in `~/.gemini/trustedFolders.json`. The server auto-picks
  the first `TRUST_FOLDER` entry; override with `AGY_TRUSTED_CWD` if you prefer a specific one.
- **Python 3.10+**.

## Install

```bat
pip install -r requirements.txt
```

## Smoke test

```bat
python agy_client.py "What is 17 * 23? Reply with only the number."
```

You should see the model's answer printed with the conversation DB name and elapsed time.

## Connect to an MCP client

### Claude Code

```bat
claude mcp add agy-mcp -- python D:\AI\AGY_MCP\server.py
```

### Cursor / generic `mcpServers` config

```json
{
  "mcpServers": {
    "agy-mcp": {
      "command": "python",
      "args": ["D:\\AI\\AGY_MCP\\server.py"]
    }
  }
}
```

Once connected, the client gains three tools: `ask_antigravity`, `list_conversations`,
and `read_conversation`.

## Tool: `ask_antigravity`

Sends one prompt and returns `{"answer": ..., "conversation_id": ...}`.

| Arg            | Default          | Notes |
|----------------|------------------|-------|
| `prompt`       | —                | The question / instruction to send. |
| `model`        | `"Gemini 3 Pro"` | Resolves to `"Gemini 3.5 Flash (Medium)"`. Pass another display name to switch. |
| `add_dirs`     | `[]`             | Folders to expose for file/image analysis. Reference the file path inside `prompt` — there is no upload flag. |
| `timeout`      | `120`            | Hard cap in seconds. |
| `conversation` | `None`           | Pass a previous call's `conversation_id` to continue that thread (context carries over). Omit to start fresh. |

**Multi-turn example:** call once, then pass the returned `conversation_id` back in:

```text
ask_antigravity("Remember the code ZX9-MANGO. Reply OK.")
  -> {"answer": "OK", "conversation_id": "91358578-..."}
ask_antigravity("What was the code?", conversation="91358578-...")
  -> {"answer": "ZX9-MANGO", "conversation_id": "91358578-..."}
```

## Tools: `list_conversations` / `read_conversation`

- **`list_conversations(limit=20)`** → recent sessions, newest first, each
  `{id, title, user_turns, modified_iso, db_bytes}` (`title` = first user prompt).
- **`read_conversation(conversation_id)`** → the full transcript as alternating
  `USER` / `MODEL` turns.

### Where conversations are stored

The CLI keeps each session in its own SQLite file — there is no single combined store:

- `~/.gemini/antigravity-cli/conversations/<id>.db` — **authoritative**; table `steps`
  holds the turn-by-turn trajectory (user = `step_type 14`, model = `step_type 15`).
  Each run also leaves an empty ~48 KB companion shell, which the reader skips.
- `~/.gemini/antigravity-cli/history.jsonl` — a flat append-only log of *interactive*
  prompts. Convenient to skim, but `--print` runs are **not** recorded here, so listing
  is driven off the `.db` files instead.

You can also use the helpers directly from the shell:

```bat
python conversations.py                 REM list recent conversations
python conversations.py <conversation-id>   REM print one transcript
```

## Configuration (environment variables)

| Var                | Default |
|--------------------|---------|
| `AGY_BIN`          | `%LOCALAPPDATA%\agy\bin\agy.exe` |
| `AGY_CONV_DIR`     | `~/.gemini/antigravity-cli/conversations` |
| `AGY_TRUSTED_CWD`  | first `TRUST_FOLDER` in `~/.gemini/trustedFolders.json` |
| `AGY_DEFAULT_MODEL`| `Gemini 3 Pro` |
| `AGY_TIMEOUT`      | `120` |
