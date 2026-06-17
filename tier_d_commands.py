"""Tier-D slash-command equivalents: shell subcommands and file operations.

- /diff: run git diff to show workspace changes
- /open: open a file in the system default editor/application
- /logout: delete OAuth credentials and clear auth state
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys


def show_diff(path: str | None = None, working_dir: str | None = None) -> str:
    """/diff — show git diff for the workspace or a specific path.

    If path is omitted, shows diff for all modified files in the repository.
    If path is a file, shows diff for that file.
    If path is a directory, shows diff for all files in that tree.

    Args:
        path: Optional file or directory path (absolute or relative to working_dir).
        working_dir: Directory to run git from (default: current directory).

    Returns:
        git diff output as a string. Returns error message if git fails or no changes.
    """
    working_dir = working_dir or os.getcwd()
    if path:
        path = os.path.normpath(path)
        if not os.path.exists(path):
            return f"error: path not found: {path!r}"
    try:
        cmd = ["git", "diff"]
        if path:
            cmd.append(path)
        result = subprocess.run(
            cmd,
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return f"git error:\n{result.stderr}"
        if not result.stdout:
            return "(no changes)"
        return result.stdout
    except subprocess.TimeoutExpired:
        return "git diff timed out (>10s)"
    except FileNotFoundError:
        return "error: git not found in PATH"
    except Exception as e:
        return f"error: {e}"


def open_path(path: str) -> dict:
    """/open — open a file or directory in the system default application.

    On Windows: uses os.startfile() which launches the default app.
    On macOS: uses `open` command.
    On Linux: uses `xdg-open` command.

    Args:
        path: Absolute or relative path to file or directory.

    Returns:
        {"opened": <path>, "status": "success"} or error dict.
    """
    path = os.path.normpath(path)
    if not os.path.exists(path):
        return {"error": f"path not found: {path!r}"}

    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.run(["open", path], check=True, timeout=5)
        else:  # linux, etc.
            subprocess.run(["xdg-open", path], check=True, timeout=5)
        return {"opened": os.path.abspath(path), "status": "success"}
    except Exception as e:
        return {"error": str(e)}


def logout() -> dict:
    """/logout — delete OAuth credentials and clear authentication state.

    Removes the local Google OAuth token file so the next `agy` invocation
    will require re-authentication.

    Returns:
        {"deleted": [<files>], "status": "success"} or error dict.
    """
    deleted = []
    errors = []

    # OAuth token file locations (agy uses these paths)
    token_paths = [
        os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli", "auth.json"),
        os.path.join(os.path.expanduser("~"), ".config", "gcloud", "application_default_credentials.json"),
        os.path.join(os.path.expanduser("~"), ".config", "gcloud", "credentials.json"),
    ]

    for path in token_paths:
        if os.path.isfile(path):
            try:
                os.remove(path)
                deleted.append(path)
            except Exception as e:
                errors.append(f"{path}: {e}")
        elif os.path.isdir(path):
            try:
                shutil.rmtree(path)
                deleted.append(path)
            except Exception as e:
                errors.append(f"{path}: {e}")

    if deleted:
        return {"deleted": deleted, "status": "success"}
    elif errors:
        return {"error": "; ".join(errors)}
    else:
        return {"status": "no credentials found to delete"}


if __name__ == "__main__":
    import json
    # Quick test
    print("=== /diff ===")
    print(show_diff(working_dir=r"D:\AI\AGY_MCP")[:200])
    print("\n=== /open (dry run) ===")
    print("skipped (would open in editor)")
    print("\n=== /logout (dry run) ===")
    print("skipped (would delete auth files)")
