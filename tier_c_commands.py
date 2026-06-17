"""Tier-C slash-command equivalents: configuration file read/write operations.

- /config / /settings: read/write settings.json
- /keybindings: read/write keybindings.json
- /hooks: list and manage pre-prompt, post-response scripts
- /statusline: read/write statusline.yaml
- /mcp: read/write mcp.json (Model Context Protocol servers)
- /skills: list .md skill files

All files live in ~/.gemini/antigravity-cli/
"""
from __future__ import annotations

import glob
import json
import os
import stat
import yaml


_AGY_HOME = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli")


def _ensure_agy_home():
    """Ensure ~/.gemini/antigravity-cli exists."""
    os.makedirs(_AGY_HOME, exist_ok=True)


def read_settings() -> dict:
    """/config /settings — read the current settings as JSON.

    Returns the complete settings object. If the file doesn't exist, returns {}.
    """
    path = os.path.join(_AGY_HOME, "settings.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as e:
        return {"error": str(e)}


def write_settings(settings: dict) -> dict:
    """/config /settings — write settings back to disk.

    Args:
        settings: Dictionary to write (will replace the file).

    Returns:
        {"saved_to": <path>, "keys": [<keys_in_settings>]}
    """
    _ensure_agy_home()
    path = os.path.join(_AGY_HOME, "settings.json")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2)
        return {"saved_to": path, "keys": list(settings.keys())}
    except Exception as e:
        return {"error": str(e)}


def read_keybindings() -> dict:
    """/keybindings — read the current keybindings as JSON.

    Returns the complete keybindings mapping. Format: { "action.name": ["key1", "key2", ...] }
    """
    path = os.path.join(_AGY_HOME, "keybindings.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as e:
        return {"error": str(e)}


def write_keybindings(keybindings: dict) -> dict:
    """/keybindings — write keybindings back to disk.

    Args:
        keybindings: Dictionary mapping action names to key sequences.

    Returns:
        {"saved_to": <path>, "actions": <count>}
    """
    _ensure_agy_home()
    path = os.path.join(_AGY_HOME, "keybindings.json")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(keybindings, fh, indent=4)
        return {"saved_to": path, "actions": len(keybindings)}
    except Exception as e:
        return {"error": str(e)}


def list_skills() -> dict:
    """/skills — list all available skill files (.md).

    Searches ~/.gemini/antigravity-cli recursively for .md files.
    Returns: {"skills": [<file_paths>]}
    """
    skills = []
    try:
        for path in glob.glob(os.path.join(_AGY_HOME, "**", "*.md"), recursive=True):
            rel = os.path.relpath(path, _AGY_HOME)
            skills.append(rel)
        return {"skills": sorted(skills), "count": len(skills)}
    except Exception as e:
        return {"error": str(e)}


def read_mcp_config() -> dict:
    """/mcp — read the MCP servers configuration.

    Returns the mcp.json content which defines external MCP servers.
    Format: {"mcpServers": {"server_id": {"command": "...", "args": [...]}}}
    """
    path = os.path.join(_AGY_HOME, "mcp.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as e:
        return {"error": str(e)}


def write_mcp_config(config: dict) -> dict:
    """/mcp — write MCP servers configuration.

    Args:
        config: The mcp.json structure to write.

    Returns:
        {"saved_to": <path>, "servers": [<server_ids>]} or error dict.
    """
    _ensure_agy_home()
    path = os.path.join(_AGY_HOME, "mcp.json")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2)
        servers = list(config.get("mcpServers", {}).keys())
        return {"saved_to": path, "servers": servers}
    except Exception as e:
        return {"error": str(e)}


def read_statusline_config() -> dict:
    """/statusline — read the statusline configuration.

    Returns the statusline.yaml content as a dict. Format varies but typically:
    {"layout": {"left": [...], "right": [...]}, "colors": {...}}
    """
    path = os.path.join(_AGY_HOME, "statusline.yaml")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as e:
        return {"error": str(e)}


def write_statusline_config(config: dict) -> dict:
    """/statusline — write statusline configuration.

    Args:
        config: The statusline configuration dict to write as YAML.

    Returns:
        {"saved_to": <path>, "keys": [<top_level_keys>]} or error dict.
    """
    _ensure_agy_home()
    path = os.path.join(_AGY_HOME, "statusline.yaml")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            yaml.dump(config, fh, default_flow_style=False, allow_unicode=True)
        return {"saved_to": path, "keys": list(config.keys())}
    except Exception as e:
        return {"error": str(e)}


def list_hooks() -> dict:
    """/hooks — list all hook scripts and their current state.

    Looks in ~/.gemini/antigravity-cli/hooks/ for executable scripts.
    Common names: pre-prompt, post-response, pre-send, post-receive, etc.

    Returns:
        {"hooks": [{"name": "...", "executable": bool, "size": int}]}
    """
    hooks_dir = os.path.join(_AGY_HOME, "hooks")
    hooks_list = []

    if os.path.isdir(hooks_dir):
        for fname in os.listdir(hooks_dir):
            path = os.path.join(hooks_dir, fname)
            if os.path.isfile(path):
                is_exec = os.access(path, os.X_OK)
                size = os.path.getsize(path)
                hooks_list.append({
                    "name": fname,
                    "executable": is_exec,
                    "size": size,
                    "path": path,
                })

    return {"hooks": sorted(hooks_list, key=lambda h: h["name"]), "count": len(hooks_list)}


def read_hook_script(hook_name: str) -> dict:
    """/hooks — read a specific hook script content.

    Args:
        hook_name: Name of the hook (e.g., "pre-prompt", "post-response").

    Returns:
        {"content": <script_text>} or error dict.
    """
    path = os.path.join(_AGY_HOME, "hooks", hook_name)
    if not os.path.isfile(path):
        return {"error": f"hook not found: {hook_name}"}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return {"content": fh.read(), "path": path, "executable": os.access(path, os.X_OK)}
    except Exception as e:
        return {"error": str(e)}


def write_hook_script(hook_name: str, content: str, executable: bool = True) -> dict:
    """/hooks — write or update a hook script.

    Args:
        hook_name: Name of the hook to create/update.
        content: Script content (bash, python, etc.).
        executable: Whether to make the script executable (default True).

    Returns:
        {"saved_to": <path>, "executable": <bool>} or error dict.
    """
    _ensure_agy_home()
    hooks_dir = os.path.join(_AGY_HOME, "hooks")
    os.makedirs(hooks_dir, exist_ok=True)
    path = os.path.join(hooks_dir, hook_name)

    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        # On Unix-like systems, make it executable
        if executable and hasattr(stat, 'S_IEXUSR'):
            st = os.stat(path)
            os.chmod(path, st.st_mode | stat.S_IEXUSR | stat.S_IXGRP | stat.S_IXOTH)
        # Windows: file creation alone is enough, as .bat/.cmd are executable by extension
        return {"saved_to": path, "executable": os.access(path, os.X_OK)}
    except Exception as e:
        return {"error": str(e)}


def get_config_info() -> dict:
    """Unified config info endpoint — summarizes all configuration state.

    Returns a dict with keys for settings, keybindings, skills, etc.
    Useful for debugging what configuration is currently active.
    """
    settings = read_settings()
    keybindings = read_keybindings()
    skills = list_skills()
    mcp = read_mcp_config()
    statusline = read_statusline_config()
    hooks = list_hooks()

    return {
        "settings": {
            "file": os.path.join(_AGY_HOME, "settings.json"),
            "exists": os.path.isfile(os.path.join(_AGY_HOME, "settings.json")),
            "keys": list(settings.keys()) if isinstance(settings, dict) and "error" not in settings else [],
            "current_model": settings.get("model", "(not set)") if isinstance(settings, dict) else None,
        },
        "keybindings": {
            "file": os.path.join(_AGY_HOME, "keybindings.json"),
            "exists": os.path.isfile(os.path.join(_AGY_HOME, "keybindings.json")),
            "actions": len(keybindings) if isinstance(keybindings, dict) and "error" not in keybindings else 0,
        },
        "mcp": {
            "file": os.path.join(_AGY_HOME, "mcp.json"),
            "exists": os.path.isfile(os.path.join(_AGY_HOME, "mcp.json")),
            "servers": list(mcp.get("mcpServers", {}).keys()) if isinstance(mcp, dict) and "error" not in mcp else [],
        },
        "statusline": {
            "file": os.path.join(_AGY_HOME, "statusline.yaml"),
            "exists": os.path.isfile(os.path.join(_AGY_HOME, "statusline.yaml")),
            "keys": list(statusline.keys()) if isinstance(statusline, dict) and "error" not in statusline else [],
        },
        "hooks": hooks,
        "skills": skills,
        "agy_home": _AGY_HOME,
    }


if __name__ == "__main__":
    import pprint
    print("=== Settings ===")
    pprint.pprint(read_settings())
    print("\n=== Keybindings (first 5) ===")
    kb = read_keybindings()
    for k, v in list(kb.items())[:5]:
        print(f"  {k}: {v}")
    print("\n=== Skills ===")
    pprint.pprint(list_skills())
