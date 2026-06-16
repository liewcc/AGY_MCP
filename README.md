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

Once connected, the client gains one tool: **`ask_antigravity(prompt, model?, add_dirs?, timeout?)`**.

## Tool: `ask_antigravity`

| Arg        | Default            | Notes |
|------------|--------------------|-------|
| `prompt`   | —                  | The question / instruction to send. |
| `model`    | `"Gemini 3 Pro"`   | Resolves to `"Gemini 3.5 Flash (Medium)"`. Pass another display name to switch. |
| `add_dirs` | `[]`               | Folders to expose for file/image analysis. Reference the file path inside `prompt` — there is no upload flag. |
| `timeout`  | `120`              | Hard cap in seconds. |

## Configuration (environment variables)

| Var                | Default |
|--------------------|---------|
| `AGY_BIN`          | `%LOCALAPPDATA%\agy\bin\agy.exe` |
| `AGY_CONV_DIR`     | `~/.gemini/antigravity-cli/conversations` |
| `AGY_TRUSTED_CWD`  | first `TRUST_FOLDER` in `~/.gemini/trustedFolders.json` |
| `AGY_DEFAULT_MODEL`| `Gemini 3 Pro` |
| `AGY_TIMEOUT`      | `120` |
