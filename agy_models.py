"""Live model list from the Antigravity CLI.

`agy models` prints the available models ONLY when its stdout is a real console
(it suppresses the list under a pipe/redirect). So we run it attached to a
Windows pseudo-console (ConPTY) and parse the rendered output. No third-party
dependency, and nothing is hardcoded or cached to disk — every call asks `agy`,
which fetches the list live from the backend.
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import threading
from ctypes import wintypes

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

AGY_BIN = os.environ.get(
    "AGY_BIN", os.path.join(os.environ.get("LOCALAPPDATA", ""), "agy", "bin", "agy.exe")
)

_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_CREATE_NO_WINDOW = 0x08000000

# Strip CSI / OSC / other VT escapes, then the "Fetching available models..." spinner.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[\]P].*?(?:\x07|\x1b\\)|\x1b[=>]")
_SPINNER = re.compile(r"[⠀-⣿]\s*Fetching available models\.\.\.")


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


def list_models(deadline_s: float = 12.0) -> list[str]:
    """Return the live list of model labels (e.g. "Gemini 3.5 Flash (Medium)").

    Returns [] on any failure (not logged in, agy missing, timeout). Takes a few
    seconds because `agy` fetches the list from the backend each call. Call it off
    the UI thread.
    """
    if not os.path.isfile(AGY_BIN):
        return []

    in_read, in_write = wintypes.HANDLE(), wintypes.HANDLE()
    out_read, out_write = wintypes.HANDLE(), wintypes.HANDLE()
    _k32.CreatePipe(ctypes.byref(in_read), ctypes.byref(in_write), None, 0)
    _k32.CreatePipe(ctypes.byref(out_read), ctypes.byref(out_write), None, 0)

    hpc = wintypes.HANDLE()
    _k32.CreatePseudoConsole.restype = ctypes.c_long
    if _k32.CreatePseudoConsole(_COORD(200, 50), in_read, out_write, 0, ctypes.byref(hpc)) != 0:
        return []

    attr_size = ctypes.c_size_t(0)
    _k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attr_size))
    attr_list = (ctypes.c_byte * attr_size.value)()
    _k32.InitializeProcThreadAttributeList(attr_list, 1, 0, ctypes.byref(attr_size))
    _k32.UpdateProcThreadAttribute(
        attr_list, 0, ctypes.c_size_t(_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE),
        hpc, ctypes.sizeof(hpc), None, None,
    )

    siex = _STARTUPINFOEXW()
    siex.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
    siex.lpAttributeList = ctypes.cast(attr_list, ctypes.c_void_p)
    pi = _PROCESS_INFORMATION()
    cmdline = ctypes.create_unicode_buffer(f'"{AGY_BIN}" models')
    if not _k32.CreateProcessW(
        None, cmdline, None, None, False,
        _EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT,
        None, None, ctypes.byref(siex.StartupInfo), ctypes.byref(pi),
    ):
        _k32.ClosePseudoConsole(hpc)
        return []

    _k32.CloseHandle(in_read)
    _k32.CloseHandle(out_write)

    chunks: list[bytes] = []

    def _reader():
        buf = (ctypes.c_char * 4096)()
        n = wintypes.DWORD(0)
        while True:
            if not _k32.ReadFile(out_read, buf, 4096, ctypes.byref(n), None) or n.value == 0:
                break
            chunks.append(bytes(buf[: n.value]))

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    # agy's main process prints the list then exits (~2-3s). Wait for it, then
    # hard-kill the whole tree: its background language server keeps the pty's
    # write end open, which would otherwise block the reader forever.
    _k32.WaitForSingleObject(pi.hProcess, int(deadline_s * 1000))
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pi.dwProcessId)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_CREATE_NO_WINDOW,
    )
    t.join(timeout=2.0)

    _k32.ClosePseudoConsole(hpc)
    _k32.CloseHandle(out_read)
    _k32.CloseHandle(in_write)
    _k32.CloseHandle(pi.hThread)
    _k32.CloseHandle(pi.hProcess)

    text = _SPINNER.sub("", _ANSI.sub("", b"".join(chunks).decode("utf-8", "replace")))
    return [
        s for s in (ln.strip() for ln in text.splitlines())
        if s and "Fetching available models" not in s
    ]


if __name__ == "__main__":
    for m in list_models():
        print(m)
    os._exit(0)
