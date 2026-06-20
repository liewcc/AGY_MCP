# AGY_MCP

AGY_MCP is a plugin that lets AI assistants like Claude talk directly to Google's Antigravity CLI (`agy`), the same tool already used in the terminal. It requires no API key because it reuses your existing CLI OAuth login. Instead of manual copy-pasting between Claude and the terminal, Claude asks `agy` and retrieves the answer automatically. It also includes a desktop control panel (TUI) to monitor quota, switch models, and browse past sessions.

## Table of Contents
1. [Quick Start — Install & First Use](#1-quick-start--install--first-use)
2. [TUI Control Panel](#2-tui-control-panel)
3. [Available Tools (39)](#3-available-tools-39)
4. [Multi-turn & Advanced Usage](#4-multi-turn--advanced-usage)
5. [Configuration](#5-configuration)

---

## 1. Quick Start — Install & First Use

### Prerequisites
* Windows OS
* Python 3.10+
* Google Antigravity CLI (`agy.exe` at `%LOCALAPPDATA%\agy\bin\agy.exe`) installed and logged in (OAuth).
* A trusted folder configured in `~/.gemini/trustedFolders.json`.

### Install Steps
Run the [setup.bat](file:///D:/AI/AGY_MCP/setup.bat) script or manually install the dependencies:
```bat
pip install -r requirements.txt
```

### Connect to Claude Code
To register the MCP server, run:
```bat
claude mcp add agy-mcp -- python D:\AI\AGY_MCP\server.py
```

### Connect to Cursor
Add the following JSON snippet to your Cursor `mcpServers` configuration:
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

### Smoke Test Command
Verify the installation by running the smoke test helper script [agy_client.py](file:///D:/AI/AGY_MCP/agy_client.py):
```bat
python agy_client.py "What is 17 * 23? Reply with only the number."
```

---

## 2. TUI Control Panel

Run the [run.bat](file:///D:/AI/AGY_MCP/run.bat) batch script to launch [tui.py](file:///D:/AI/AGY_MCP/tui.py), a terminal-based desktop control panel. It includes:
* **Credential**: Log in or log out of your Google account.
* **Models**: Switch the active Gemini model.
* **Quota**: View and monitor group rate limits.
* **Profile Stats**: Monitor multi-account quotas inside a table with auto-refresh.

---

## 3. Available Tools (39)

AGY_MCP exposes 39 tools to your AI client, categorized into Tiers A–F based on their integration and communication mechanism with the `agy` CLI.

| Tier | Mechanism | Example Tools |
| :--- | :--- | :--- |
| A | Headless `agy --print` execution | [ask_antigravity](file:///D:/AI/AGY_MCP/server.py#L67), `ask_btw` |
| B | SQLite database direct read/write | [list_conversations](file:///D:/AI/AGY_MCP/server.py#L110), `read_conversation`, `fork_conversation` |
| C | Configuration file read/write (JSON/YAML) | `read_settings`, `write_settings`, `list_skills`, `get_config_info` |
| D | Shell subcommands & system calls | `show_diff`, `open_path`, `logout`, `list_plugins` |
| E | gRPC connection to running `agy` processes | `list_tasks`, `agent_session_state` |
| F | Windows ConPTY terminal injection | `run_goal`, `start_planning`, `toggle_fast_mode`, `list_models` |

For the full detailed list of all 39 tools and their exact execution flow, see [agy_knowledge/architecture.md](file:///D:/AI/AGY_MCP/agy_knowledge/architecture.md).

---

## 4. Multi-turn & Advanced Usage

### Multi-turn Conversations
To maintain context across multiple turns, retrieve the `conversation_id` from the initial [ask_antigravity](file:///D:/AI/AGY_MCP/server.py#L67) call and pass it in subsequent requests:

```text
ask_antigravity(prompt="Remember code ZX9-MANGO.")
  -> {"answer": "OK", "conversation_id": "91358578-f3ab-41c2-9e90-c637efeead9c"}

ask_antigravity(prompt="What was the code?", conversation="91358578-f3ab-41c2-9e90-c637efeead9c")
  -> {"answer": "ZX9-MANGO", "conversation_id": "91358578-f3ab-41c2-9e90-c637efeead9c"}
```

### Exposing Directories
Pass absolute folder paths using the `add_dirs` parameter to share files and context with the agent:
```text
ask_antigravity(prompt="Review the code in main.py", add_dirs=["D:/AI/AGY_MCP"])
```

### Pre-flight Best Practices
Before dispatching heavy tasks to the agent, perform these checks:
1. Check the active model and context limits using [get_context_stats](file:///D:/AI/AGY_MCP/server.py#L239).
2. Check remaining quota percentages using [get_quota](file:///D:/AI/AGY_MCP/server.py#L215) to verify sufficient resources.

---

## 5. Configuration

Adjust AGY_MCP behavior using the following environment variables:

| Environment Variable | Default Value | Description |
| :--- | :--- | :--- |
| `AGY_BIN` | `%LOCALAPPDATA%\agy\bin\agy.exe` | Absolute path to the `agy` executable file. See [AGY_BIN](file:///D:/AI/AGY_MCP/agy_core.py#L58). |
| `AGY_CONV_DIR` | `~/.gemini/antigravity-cli/conversations` | Path to the directory where SQLite conversation files are stored. See [AGY_CONV_DIR](file:///D:/AI/AGY_MCP/agy_core.py#L62). |
| `AGY_TRUSTED_CWD` | First `TRUST_FOLDER` in `trustedFolders.json` | Working directory to execute headless CLI tasks. See [AGY_TRUSTED_CWD](file:///D:/AI/AGY_MCP/agy_core.py#L203). |
| `AGY_DEFAULT_MODEL` | `Gemini 3 Pro` | Model display name used for requests. See [AGY_DEFAULT_MODEL](file:///D:/AI/AGY_MCP/agy_core.py#L67). |
| `AGY_TIMEOUT` | `120` | Headless execution timeout limit in seconds. See [AGY_TIMEOUT](file:///D:/AI/AGY_MCP/agy_core.py#L68). |
