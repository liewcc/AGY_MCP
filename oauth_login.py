"""Standalone agy OAuth login driver (clipboard bridge) — no TUI.

Why this works: Google blocks fresh credential entry inside automated browsers,
so we don't automate the browser at all. agy opens the user's real, already
logged-in Chrome; the user clicks account + Allow, lands on the code page, and
clicks Copy. This script just watches the clipboard for the auth code and
injects it back into agy via the ConPTY.

Flow:
  1. Spawn agy.exe under a ConPTY, press Enter on the login-method menu.
  2. Scrape the Google OAuth URL and open it in the default browser.
  3. Empty the clipboard, then wait for a "4/..." auth code to be copied.
  4. Inject the code into agy and finish.

Usage:  python oauth_login.py
"""
import os
import re
import time
import ctypes

from agy_core import (
    _conpty_start, _read_pty, _conpty_kill, _ANSI,
    _ensure_console_session, _k32, AGY_BIN,
)

DEBUG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "_agy_login_debug.txt")

# Google OAuth auth codes look like "4/0Adeu5..." — anchor on that shape.
_CODE_RE = re.compile(r"^4/[A-Za-z0-9_\-]{20,}$")


def log(msg: str) -> None:
    print(msg, flush=True)


# ── Clipboard helpers (Win32) ────────────────────────────────────────────────
_CF_UNICODETEXT = 13
_u32 = ctypes.windll.user32
_k32dll = ctypes.windll.kernel32
# Handles/pointers are 64-bit on Win64; without these restypes ctypes defaults
# to a 32-bit c_int and TRUNCATES the value, yielding an invalid pointer.
_u32.GetClipboardData.restype = ctypes.c_void_p
_u32.GetClipboardData.argtypes = [ctypes.c_uint]
_k32dll.GlobalLock.restype = ctypes.c_void_p
_k32dll.GlobalLock.argtypes = [ctypes.c_void_p]
_k32dll.GlobalUnlock.argtypes = [ctypes.c_void_p]


def read_clipboard_text() -> str:
    """Return clipboard text (UTF-16), or '' on any failure."""
    if not _u32.OpenClipboard(0):
        return ""
    try:
        h = _u32.GetClipboardData(_CF_UNICODETEXT)
        if not h:
            return ""
        ptr = _k32dll.GlobalLock(h)
        if not ptr:
            return ""
        try:
            return ctypes.c_wchar_p(ptr).value or ""
        finally:
            _k32dll.GlobalUnlock(h)
    finally:
        _u32.CloseClipboard()


def empty_clipboard() -> None:
    """Clear the clipboard so any later copy is unambiguously the auth code."""
    if _u32.OpenClipboard(0):
        try:
            _u32.EmptyClipboard()
        finally:
            _u32.CloseClipboard()


def grab_oauth_url(chunks, in_write, written) -> str | None:
    """Scrape the Google OAuth URL from agy's ConPTY output (30s budget)."""
    menu_done = False
    deadline = time.time() + 30
    while time.time() < deadline:
        time.sleep(0.5)
        clean = _ANSI.sub("", b"".join(chunks).decode("utf-8", errors="replace"))
        if not menu_done and "Select login method" in clean:
            _k32.WriteFile(in_write, b"\r", 1, ctypes.byref(written), None)
            menu_done = True
        for m in re.finditer(r"https?://\S+", clean):
            url = m.group(0).rstrip(".,)")
            if "google" in url and "auth" in url.lower():
                return url
    return None


def run_login(status=log) -> bool:
    """Run the full agy OAuth clipboard-bridge login.

    `status(msg)` receives progress strings (defaults to printing). Safe to call
    from a background thread — does no console/stdin I/O of its own. Returns True
    if agy received an auth code, False otherwise.
    """
    written = ctypes.c_ulong(0)

    # ── Phase 1: start agy, scrape the OAuth URL ──────────────────────────────
    status("Starting agy …")
    _ensure_console_session()
    res = _conpty_start(f'"{AGY_BIN}"', width=4096)   # wide => URL won't line-wrap
    if res is None:
        status("[red]ConPTY failed to start agy.[/red]")
        return False
    hpc, pi, in_write, out_read = res
    chunks, reader = _read_pty(out_read)
    pid = pi.dwProcessId

    oauth_url = grab_oauth_url(chunks, in_write, written)
    if not oauth_url:
        clean = _ANSI.sub("", b"".join(chunks).decode("utf-8", errors="replace"))
        with open(DEBUG_PATH, "w", encoding="utf-8") as f:
            f.write(f"No OAuth URL found.\n\n{clean}")
        if any(k in clean for k in ("@gmail.com", "? for shortcuts")):
            status("[yellow]agy is already logged in — log out first.[/yellow]")
        else:
            status("[red]No OAuth URL (see _agy_login_debug.txt).[/red]")
        _conpty_kill(pid, hpc, pi, in_write, out_read)
        reader.join(2)
        return False

    status("Opening browser — pick account, Allow, then Copy the code …")
    try:
        os.startfile(oauth_url)   # default browser = user's logged-in Chrome
    except Exception:
        status("[yellow]Could not auto-open browser; open the URL manually.[/yellow]")

    # ── Phase 2: watch the clipboard for the auth code ────────────────────────
    empty_clipboard()
    status("Waiting for the auth code (up to 5 min) …")
    auth_code = None
    deadline = time.time() + 300
    while time.time() < deadline:
        time.sleep(0.4)
        clip = read_clipboard_text().strip()
        if _CODE_RE.match(clip):
            auth_code = clip
            break

    # ── Phase 3: hand the code back to agy ────────────────────────────────────
    ok = False
    if auth_code:
        status("Sending code to agy …")
        payload = (auth_code + "\r").encode("utf-8")
        _k32.WriteFile(in_write, payload, len(payload), ctypes.byref(written), None)
        time.sleep(4)
        status("[green]Login complete![/green]")
        ok = True
    else:
        status("[red]No auth code received (timed out).[/red]")

    _conpty_kill(pid, hpc, pi, in_write, out_read)
    reader.join(2)
    return ok


def main() -> int:
    ok = run_login(log)
    print("", flush=True)
    input("Press Enter to close this window ...")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
