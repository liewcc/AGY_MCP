"""Tier F — ConPTY pseudo-terminal injection for interactive-only slash commands.

These slash commands (/goal, /schedule, /grill-me, /planning, /fast,
/teamwork-preview) have no file/DB/gRPC backing — they only exist inside
agy's interactive TUI session loop. We trigger them by launching agy in
interactive mode attached to a Windows ConPTY and injecting the command into
its stdin pipe.

Known gotcha (discovered during /usage experiments): agy's autocomplete
absorbs the first Enter keystroke — the selection fills the input but doesn't
submit. Sending ESC before Enter dismisses the autocomplete dropdown so the
subsequent Enter actually executes the command.

Each public function launches a fresh agy session, fires one command, captures
the initial response output, then kills the process tree.  The captured output
is the raw TUI text with ANSI escape sequences stripped.

/btw is the exception: it has no PTY dependency and is simulated via
agy --print (same auth, independent conversation).
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import threading
import time
from ctypes import wintypes
from typing import Optional

from agy_client import ask_agy

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

AGY_BIN = os.environ.get(
    "AGY_BIN",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "agy", "bin", "agy.exe"),
)

_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_CREATE_NO_WINDOW = 0x08000000

# Strip CSI/OSC/misc VT escapes and bare carriage returns from PTY output.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[\]P].*?(?:\x07|\x1b\\)|\x1b[=>]|\r")


class _COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", _STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
    ]


def _pty_inject_slash(
    slash_cmd: str,
    wait_startup_s: float = 3.5,
    wait_response_s: float = 8.0,
) -> dict:
    """Start agy in interactive ConPTY mode, inject a slash command, return captured output.

    Waits `wait_startup_s` for the TUI to initialize, writes the command followed
    by ESC (dismiss autocomplete) then Enter, collects output for `wait_response_s`,
    kills the process tree, and returns stripped text.

    Returns:
        {"output": <stripped lines>, "pid": <int>} or {"error": <msg>}
    """
    if not os.path.isfile(AGY_BIN):
        return {"error": f"agy not found: {AGY_BIN}"}

    in_read, in_write = wintypes.HANDLE(), wintypes.HANDLE()
    out_read, out_write = wintypes.HANDLE(), wintypes.HANDLE()
    if not _k32.CreatePipe(ctypes.byref(in_read), ctypes.byref(in_write), None, 0):
        return {"error": "CreatePipe stdin failed"}
    if not _k32.CreatePipe(ctypes.byref(out_read), ctypes.byref(out_write), None, 0):
        _k32.CloseHandle(in_read)
        _k32.CloseHandle(in_write)
        return {"error": "CreatePipe stdout failed"}

    hpc = wintypes.HANDLE()
    _k32.CreatePseudoConsole.restype = ctypes.c_long
    rc = _k32.CreatePseudoConsole(_COORD(220, 50), in_read, out_write, 0, ctypes.byref(hpc))
    if rc != 0:
        _k32.CloseHandle(in_read); _k32.CloseHandle(in_write)
        _k32.CloseHandle(out_read); _k32.CloseHandle(out_write)
        return {"error": f"CreatePseudoConsole failed: {rc:#010x}"}

    attr_size = ctypes.c_size_t(0)
    _k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attr_size))
    attr_list = (ctypes.c_byte * attr_size.value)()
    _k32.InitializeProcThreadAttributeList(attr_list, 1, 0, ctypes.byref(attr_size))
    _k32.UpdateProcThreadAttribute(
        attr_list, 0,
        ctypes.c_size_t(_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE),
        hpc, ctypes.sizeof(hpc), None, None,
    )

    siex = _STARTUPINFOEXW()
    siex.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
    siex.lpAttributeList = ctypes.cast(attr_list, ctypes.c_void_p)
    pi = _PROCESS_INFORMATION()

    # Launch agy in interactive mode (no --print, no subcommand)
    cmdline = ctypes.create_unicode_buffer(f'"{AGY_BIN}"')
    if not _k32.CreateProcessW(
        None, cmdline, None, None, False,
        _EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT,
        None, None, ctypes.byref(siex.StartupInfo), ctypes.byref(pi),
    ):
        _k32.ClosePseudoConsole(hpc)
        _k32.CloseHandle(in_read); _k32.CloseHandle(in_write)
        _k32.CloseHandle(out_read); _k32.CloseHandle(out_write)
        return {"error": "CreateProcessW failed"}

    # The PTY owns in_read and out_write; we keep in_write (inject) and out_read (capture)
    _k32.CloseHandle(in_read)
    _k32.CloseHandle(out_write)

    pid = pi.dwProcessId
    chunks: list[bytes] = []

    def _reader() -> None:
        buf = (ctypes.c_char * 4096)()
        n = wintypes.DWORD(0)
        while True:
            if not _k32.ReadFile(out_read, buf, 4096, ctypes.byref(n), None) or n.value == 0:
                break
            chunks.append(bytes(buf[: n.value]))

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    try:
        # Wait for agy TUI to fully render its initial screen
        time.sleep(wait_startup_s)

        # ESC dismisses the autocomplete dropdown; Enter then executes the command
        payload = (slash_cmd + "\x1b\r").encode("utf-8")
        written = wintypes.DWORD(0)
        _k32.WriteFile(in_write, payload, len(payload), ctypes.byref(written), None)

        # Collect response output
        time.sleep(wait_response_s)

    finally:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )
        t.join(timeout=3.0)
        _k32.ClosePseudoConsole(hpc)
        _k32.CloseHandle(out_read)
        _k32.CloseHandle(in_write)
        _k32.CloseHandle(pi.hThread)
        _k32.CloseHandle(pi.hProcess)

    raw = b"".join(chunks).decode("utf-8", "replace")
    cleaned = _ANSI.sub("", raw)
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    return {"output": "\n".join(lines), "pid": pid}


# ---- Public functions, one per Tier F slash command ----

def toggle_fast_mode() -> dict:
    """/fast — toggle agy fast/thinking mode in a fresh ConPTY session.

    Returns the mode-change confirmation text captured from the TUI.
    """
    return _pty_inject_slash("/fast", wait_startup_s=3.5, wait_response_s=5.0)


def run_goal(description: str) -> dict:
    """/goal — set an autonomous execution goal and capture agy's initial response.

    Launches agy in interactive mode, injects /goal <description>, captures the
    first ~10 s of output, then kills the session. For a persistent long-running
    goal the user should type /goal directly in their own agy terminal.
    """
    return _pty_inject_slash(f"/goal {description}", wait_startup_s=3.5, wait_response_s=10.0)


def start_planning(description: str = "") -> dict:
    """/planning — kick off a multi-turn planning session; captures the initial plan output."""
    cmd = f"/planning {description}".rstrip()
    return _pty_inject_slash(cmd, wait_startup_s=3.5, wait_response_s=12.0)


def start_schedule(description: str) -> dict:
    """/schedule — set a scheduled/cron task; captures the schedule confirmation."""
    return _pty_inject_slash(f"/schedule {description}", wait_startup_s=3.5, wait_response_s=8.0)


def start_grill_me() -> dict:
    """/grill-me — start an interactive Q&A alignment session; captures the first prompt."""
    return _pty_inject_slash("/grill-me", wait_startup_s=3.5, wait_response_s=10.0)


def start_teamwork_preview() -> dict:
    """/teamwork-preview — launch multi-agent teamwork preview; captures the initial output."""
    return _pty_inject_slash("/teamwork-preview", wait_startup_s=3.5, wait_response_s=10.0)


def ask_btw(query: str, conversation_id: Optional[str] = None) -> dict:
    """/btw — background side-question, simulated via agy --print.

    agy's /btw sends a background query without interrupting the current session.
    We approximate this with a headless --print call: same model and auth,
    running as an independent conversation.

    Args:
        query:           The question to ask.
        conversation_id: Resume a prior conversation (optional).

    Returns:
        {"answer": <text>, "conversation_id": <id>}
    """
    answer, conv_id = ask_agy(query, conversation=conversation_id)
    return {"answer": answer, "conversation_id": conv_id}
