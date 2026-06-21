# AGY_MCP

AGY_MCP is a plugin that lets AI assistants like Claude talk directly to Google's Antigravity CLI (`agy`), the same tool already used in the terminal. It requires no API key because it reuses your existing CLI OAuth login. Instead of manual copy-pasting between Claude and the terminal, Claude asks `agy` and retrieves the answer automatically. It also includes a desktop control panel (TUI) to monitor quota, switch models, and browse past sessions.

## Table of Contents
1. [Quick Start — Install & First Use](#1-quick-start--install--first-use)
2. [TUI Control Panel](#2-tui-control-panel)
3. [Available Tools (39)](#3-available-tools-39)
4. [Multi-turn & Advanced Usage](#4-multi-turn--advanced-usage)
5. [Configuration](#5-configuration)
6. [Troubleshooting](#6-troubleshooting)

---

## 1. Quick Start — Install & First Use

### 1.1 Prerequisites
* Windows OS
* Python 3.10+ (in PATH — `python --version` must work)
* Google Antigravity CLI (`agy.exe` at `%LOCALAPPDATA%\agy\bin\agy.exe`). `setup.bat` can install it for you if missing.
* A trusted folder configured in `~/.gemini/trustedFolders.json` (created automatically on first `agy` use).

### 1.2 Run setup.bat

Run [setup.bat](file:///D:/AI/AGY_MCP/setup.bat). It performs five steps in order:

| Step | Action | Notes |
|:--:|:--|:--|
| 1/5 | `pip install -r requirements.txt` | Fails the run if pip errors |
| 2/5 | Verify Antigravity CLI present | Warning only — does not block |
| 3/5 | Create `AGY MCP.lnk` desktop shortcut | Targets `run.bat` (launches the TUI) |
| 4/5 | **Register `agy-mcp` with Claude Code** | Interactive (`y/n`), idempotent |
| 5/5 | **Register `agy-mcp` with Antigravity** | Interactive (`y/n`), idempotent |

Steps 4 and 5 always prompt before writing any file. If a registration already exists, it is detected and skipped automatically — re-running `setup.bat` will not duplicate entries.

#### What gets written

| Target | Config file written | Key added |
|:--|:--|:--|
| Claude Code | `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude_desktop_config.json` (Microsoft Store install) **OR** `%APPDATA%\Claude\claude_desktop_config.json` (traditional install) | `mcpServers.agy-mcp` |
| Antigravity | `%USERPROFILE%\.gemini\config\mcp_config.json` (directory auto-created) | `mcpServers.agy-mcp` |

Both entries set `command: "python"` and `args: ["<project_dir>\\server.py"]`. The project directory is captured as an absolute path at setup time — **if you move the project folder, re-run `setup.bat`** so the paths update.

If the Claude config file cannot be found, setup prints a fallback command:
```bat
claude mcp add agy-mcp -- python "<project_dir>\server.py"
```

### 1.3 First-Time OAuth Login

`agy-mcp` has no OAuth of its own — it rides the Antigravity CLI's existing Google login. If `agy` has never been logged in on this machine, **every tool that calls Gemini / Claude / fetches quota will fail** until you do this step.

Do it through the TUI:

| Step | Action | What you should see |
|:--:|:--|:--|
| 1 | Double-click the **AGY MCP** desktop shortcut (or run `run.bat`) | TUI opens with `Home / Chats / Profile Stats` nav rail |
| 2 | On the **Home** page, find the **Credential** card and click **Log In** | Status line shows "Starting …", then "Waiting for browser …" |
| 3 | Your default browser opens to Google's account chooser | Pick the Google account you want `agy` to use |
| 4 | Approve the OAuth consent screen | Lands on a success page with a **Copy** button |
| 5 | Click **Copy** to copy the auth code to your clipboard | TUI detects the `4/...` code automatically — no manual paste |
| 6 | (Optional) close the browser tab | Login completes in the background |
| 7 | Back in the TUI, the **Profile** card shows your email | OAuth done — proceed to step 1.4 |

> The TUI runs the clipboard-bridge login in a background thread ([oauth_login.py](file:///D:/AI/AGY_MCP/oauth_login.py)). It opens the OAuth URL, empties your clipboard, then polls for the `4/...` auth code; once detected, it injects the code back into `agy` and the login finishes.

#### 1.3.1 Switching Between Multiple Accounts

Many users have more than one Google / Antigravity account (e.g. **personal** and **work**), each with its own weekly quota. `agy-mcp` lets you swap accounts at will and remembers the last-seen quota of every account you've signed into.

To switch:

1. On the **Home** page → **Credential** card, click **Log Out** — the current account signs out.
2. Click **Log In** again and complete the OAuth flow (§ 1.3) with the other account.
3. Open the **Profile Stats** page. The new account appears at the top (marked with `*`); previously seen accounts are listed below.

**Profile Stats is your "account fuel-gauge dashboard"** — one row per account, showing each group's Weekly and 5-hour quota with countdown timers:

```
┌────────────────────┬───────────────────────────┬───────────────────────────┐
│                    │       Gemini Group        │    Claude & GPT Group     │
│      Profile       │  Weekly    │  5Hr Limit   │  Weekly    │  5Hr Limit   │
├────────────────────┼────────────┼──────────────┼────────────┼──────────────┤
│ *personal          │ 59.1% (6d) │     —        │ 100.0%     │     —        │
│  work              │ 12.0% (3d) │     —        │ 60.0%      │     —        │
└────────────────────┴────────────┴──────────────┴────────────┴──────────────┘
```

> **Snapshot vs. live:** only the **currently signed-in** account auto-refreshes (default every 30 min, adjustable in the header). Other rows show the snapshot from the last time that account was signed in — re-login to refresh. The cache is persisted to [`data/profile_stats.json`](file:///D:/AI/AGY_MCP/data/profile_stats.json), so it survives TUI restarts.

Typical use: glance at the table → spot which account still has Gemini headroom for bulk Flash work, which still has Claude/GPT headroom for Opus thinking work, and route tasks accordingly.

### 1.4 Restart Host Apps

After `setup.bat` writes the config, the host app must re-read it. **Closing the window is not enough** — both apps run in the system tray.

| App | How to fully quit |
|:--|:--|
| Claude Code (Desktop) | Right-click the tray icon (near clock) → **Quit**, **not** the window's × button |
| Antigravity | Same — quit via the tray icon |

Then relaunch the app. On startup it will spawn `python <project>\server.py` and `mcp__agy-mcp__*` tools become available.

### 1.5 Smoke Test

In your host app (Claude Code or Antigravity), paste this prompt verbatim — it verifies the full chain in one go:

> I just installed agy-mcp. Please verify it step by step:
> 1. List every tool name starting with `mcp__agy-mcp__` so I can confirm the server is connected.
> 2. Call `get_context_stats` and report the active `model` and `pct_used`.
> 3. Call `list_models` and list all available models.
> 4. Call `get_quota` and report the Gemini group and Claude/GPT group weekly remaining percentages.
> 5. If any step errors, paste the full error message.

What each step proves:

| Step | Verifies |
|:--:|:--|
| 1 | Host re-read the config and spawned `server.py` |
| 2 | SQLite trajectory store readable |
| 3 | ConPTY-based agy spawn works |
| 4 | OAuth session valid + local gRPC server reachable |

If step 4 fails with a permission/auth-like error, OAuth has expired or never completed → redo § 1.3.

### 1.6 Manual Registration (Fallback)

Only needed if `setup.bat`'s auto-registration was skipped (config file missing, user answered `n`, or the host app was not installed yet).

**Claude Code** — run from a terminal:
```bat
claude mcp add agy-mcp -- python D:\AI\AGY_MCP\server.py
```

**Antigravity** — edit `%USERPROFILE%\.gemini\config\mcp_config.json` (create the file and folder if absent):
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

**Cursor** — add the same `agy-mcp` block to your Cursor MCP configuration.

> If the config file already contains other `mcpServers` entries, **merge** the `"agy-mcp"` block into the existing `"mcpServers"` object. Do not replace the whole file.

### 1.7 Uninstall / Remove Registration

There is no automated uninstall — removal is a manual JSON edit. This is also the **first thing to try** if `agy-mcp` fails to start and you want to disable it temporarily without uninstalling Python deps.

| Client | File to edit | What to remove |
|:--|:--|:--|
| Claude Code (Microsoft Store) | `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude_desktop_config.json` | The entire `"agy-mcp": { ... }` block inside `"mcpServers"` |
| Claude Code (traditional) | `%APPDATA%\Claude\claude_desktop_config.json` | Same |
| Antigravity | `%USERPROFILE%\.gemini\config\mcp_config.json` | Same |

Save the file, then **fully restart the host app** (§ 1.4). To re-enable, just re-run `setup.bat`.

---

## 2. TUI Control Panel

Run [run.bat](file:///D:/AI/AGY_MCP/run.bat) (or double-click the desktop shortcut) to launch [tui.py](file:///D:/AI/AGY_MCP/tui.py), a terminal-based control panel. Sections:

* **Home** — top-level dashboard.
  * **Credential** card: shows the signed-in account; **Log In** / **Log Out** buttons drive the OAuth clipboard bridge (§ 1.3).
  * **Models** card: lists live models and lets you switch the active one.
  * **Quota** card: live group rate-limit progress bars for the **currently** signed-in account.
* **Chats** — list / open / delete past `agy` conversations.
* **Profile Stats** — multi-account quota table (§ 1.3.1). Snapshot per account is persisted to [`data/profile_stats.json`](file:///D:/AI/AGY_MCP/data/profile_stats.json).

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

For the full per-tool inventory, browse `server.py` — every tool is decorated with `@mcp.tool()` and self-documented.

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

---

## 6. Troubleshooting

### 6.1 Tools are visible but every call fails with auth-like errors
99% of the time this is **expired or missing OAuth**. `agy-mcp` itself has no token — it relies on the Antigravity CLI's Google session. Redo § 1.3, then verify with `get_quota`.

### 6.2 Host app does not show `mcp__agy-mcp__*` tools at all
The host did not spawn `server.py`. Check, in this order:
1. Did you fully quit the host (tray → Quit) before relaunching? See § 1.4.
2. Does the config file actually contain `"agy-mcp"` under `"mcpServers"`? See § 1.7 for the exact file paths.
3. Is `python` on PATH (not the Microsoft Store stub)? Run `python --version` in a fresh `cmd` — must print 3.10+.
4. Inspect the host's MCP logs: Claude Code → `%APPDATA%\Claude\logs\mcp*.log`.

### 6.3 Claude Code config file not found by setup.bat
Setup checks two locations (Microsoft Store and traditional). If neither exists, launch Claude Code at least once so it creates `claude_desktop_config.json`, then re-run `setup.bat`. Or use the manual command in § 1.6.

### 6.4 `agy.exe` not found
The Antigravity registration step (5/5) probes three install locations: `%LOCALAPPDATA%\agy\bin\agy.exe`, `%LOCALAPPDATA%\Programs\Antigravity\Antigravity.exe`, `%LOCALAPPDATA%\Programs\Antigravity IDE\Antigravity IDE.exe`. If none exist, install Antigravity from https://antigravity.google/download then re-run `setup.bat`. Or follow § 1.6.

### 6.5 Existing `mcp_config.json` looks corrupted after setup
If `%USERPROFILE%\.gemini\config\mcp_config.json` contains invalid JSON when setup runs, the script falls back to writing a fresh `{"mcpServers": {"agy-mcp": {...}}}` — **which loses any other MCP servers previously registered in that file.** If you had other servers, restore them manually from a backup, then merge in the `"agy-mcp"` block from § 1.6.

### 6.6 You moved the project folder after setup
Both registrations record an absolute path to `server.py` captured at setup time. Moving the project breaks both — just re-run `setup.bat` from the new location.

### 6.7 Profile Stats numbers for one account never change
Only the **currently signed-in** account auto-refreshes. Other rows are snapshots from the last time that account was signed in. To refresh another account, sign into it (§ 1.3.1).

### 6.8 OAuth login hangs at "Waiting for browser …"
The TUI is polling your clipboard for a `4/...` Google auth code. Make sure you actually clicked **Copy** on the success page. If the browser never opened, check `_agy_login_debug.txt` in the project root for the captured `agy` output.
