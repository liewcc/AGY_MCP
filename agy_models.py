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
import time
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

# Quota parsing: percentage value and "Refreshes in Xh Ym" countdown.
_QUOTA_PCT = re.compile(r'(\d+(?:\.\d+)?)%')
_QUOTA_REFRESH = re.compile(r'Refreshes\s+in\s+([^\n\r\x1b]+)')


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


def _parse_quota_proto(data: bytes) -> dict | None:
    """Parse the raw protobuf from RetrieveUserQuotaSummary into a structured dict.

    Returns:
        {
            "gemini":    {"group_name": str, "weekly_pct": float|None,
                          "weekly_reset_ts": int|None, "weekly_msg": str,
                          "weekly_hit": bool, "fiveh_pct": float|None,
                          "fiveh_reset_ts": int|None, "fiveh_msg": str, "fiveh_hit": bool},
            "claude_gpt": { ...same... },
        }
    or None if parsing fails.
    """
    import struct

    def _varint(buf: bytes, pos: int) -> tuple[int, int]:
        val = 0; shift = 0
        while pos < len(buf):
            b = buf[pos]; pos += 1
            val |= (b & 0x7F) << shift; shift += 7
            if not (b & 0x80): break
        return val, pos

    def _fields(buf: bytes) -> list[tuple[int, int, object]]:
        pos = 0; out = []
        while pos < len(buf):
            tag, pos = _varint(buf, pos)
            fn, wt = tag >> 3, tag & 0x7
            if wt == 0:
                v, pos = _varint(buf, pos); out.append((fn, 0, v))
            elif wt == 1:
                if pos + 8 > len(buf): break
                out.append((fn, 1, struct.unpack_from("<Q", buf, pos)[0])); pos += 8
            elif wt == 2:
                n, pos = _varint(buf, pos)
                if pos + n > len(buf): break
                out.append((fn, 2, buf[pos:pos+n])); pos += n
            elif wt == 5:
                if pos + 4 > len(buf): break
                out.append((fn, 5, struct.unpack_from("<f", buf, pos)[0])); pos += 4
            else:
                break
        return out

    def _bucket(buf: bytes) -> dict:
        b: dict = {}
        for fn, wt, v in _fields(buf):
            if fn == 1 and wt == 2:
                try: b["id"] = v.decode()
                except: pass
            elif fn == 2 and wt == 2:
                try: b["name"] = v.decode()
                except: pass
            elif fn == 3 and wt == 2:
                try: b["period"] = v.decode()
                except: pass
            elif fn == 4 and wt == 5:
                b["remaining"] = float(v)
            elif fn == 6 and wt == 2:
                for ifn, iwt, iv in _fields(v):
                    if ifn == 1 and iwt == 0:
                        b["reset_ts"] = int(iv)
            elif fn == 7 and wt == 2:
                try: b["msg"] = v.decode()
                except: pass
            elif fn == 8 and wt == 0:
                b["is_hit"] = bool(v)
        return b

    def _group(buf: bytes) -> dict:
        buckets: list[dict] = []; name = ""
        for fn, wt, v in _fields(buf):
            if fn == 1 and wt == 2:
                bkt = _bucket(v)
                if bkt: buckets.append(bkt)
            elif fn == 2 and wt == 2:
                try: name = v.decode()
                except: pass
        return {"name": name, "buckets": buckets}

    outer = next((v for fn, wt, v in _fields(data) if fn == 1 and wt == 2), None)
    if not outer:
        return None

    groups = [_group(v) for fn, wt, v in _fields(outer) if fn == 2 and wt == 2]
    if not groups:
        return None

    result: dict = {}
    for grp in groups:
        nl = grp["name"].lower()
        key = "gemini" if "gemini" in nl else ("claude_gpt" if ("claude" in nl or "gpt" in nl) else None)
        if key is None:
            continue
        wk = next((b for b in grp["buckets"] if "weekly" in b.get("period", "")), None)
        fh = next((b for b in grp["buckets"] if "5h" in b.get("period", "")), None)
        result[key] = {
            "group_name":      grp["name"],
            "weekly_pct":      (wk.get("remaining", 0.0) * 100) if wk else None,
            "weekly_reset_ts": wk.get("reset_ts") if wk else None,
            "weekly_msg":      wk.get("msg", "") if wk else "",
            "weekly_hit":      wk.get("is_hit", False) if wk else False,
            "fiveh_pct":       (fh.get("remaining", 0.0) * 100) if fh else None,
            "fiveh_reset_ts":  fh.get("reset_ts") if fh else None,
            "fiveh_msg":       fh.get("msg", "") if fh else "",
            "fiveh_hit":       fh.get("is_hit", False) if fh else False,
        }
    return result or None


def get_quota_summary(deadline_s: float = 20.0) -> dict | None:
    """Fetch group quota (Weekly / Five-Hour limits) from agy's local gRPC server.

    Starts agy headlessly, waits for its language server to open two TCP ports,
    extracts the self-signed TLS cert, calls RetrieveUserQuotaSummary on the
    lower port (gRPC), parses the protobuf response, kills agy, and returns the
    structured dict from _parse_quota_proto — or None on any failure.

    Takes ~15 s. Always call off the UI thread.
    """
    try:
        import grpc
        import ssl as _ssl
        import socket as _sock
    except ImportError:
        return None

    if not os.path.isfile(AGY_BIN):
        return None

    def _local_ports() -> set[int]:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 "
             "-ErrorAction SilentlyContinue | Select-Object LocalPort | ConvertTo-Json"],
            capture_output=True, text=True, creationflags=_CREATE_NO_WINDOW,
        )
        return {int(m.group(1)) for m in re.finditer(r'"LocalPort":\s*(\d+)', r.stdout)}

    ports_before = _local_ports()
    proc = subprocess.Popen(
        [AGY_BIN],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=_CREATE_NO_WINDOW,
    )

    try:
        grpc_port: int | None = None
        deadline = time.time() + deadline_s
        while time.time() < deadline:
            time.sleep(0.5)
            new_ports = _local_ports() - ports_before
            if len(new_ports) >= 2:
                grpc_port = min(new_ports)  # lower port = gRPC/TLS
                break

        if grpc_port is None:
            return None

        time.sleep(4.0)  # wait for OAuth auth to complete inside the language server

        ssl_ctx = _ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = _ssl.CERT_NONE
        try:
            with _sock.create_connection(("127.0.0.1", grpc_port), timeout=5) as s:
                with ssl_ctx.wrap_socket(s, server_hostname="localhost") as ts:
                    cert_pem = _ssl.DER_cert_to_PEM_cert(ts.getpeercert(binary_form=True))
        except Exception:
            return None

        creds = grpc.ssl_channel_credentials(root_certificates=cert_pem.encode())
        opts = [
            ("grpc.ssl_target_name_override", "localhost"),
            ("grpc.default_authority", "localhost"),
        ]
        channel = grpc.secure_channel(f"127.0.0.1:{grpc_port}", creds, options=opts)
        try:
            stub = channel.unary_unary(
                "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary",
                request_serializer=bytes,
                response_deserializer=bytes,
            )
            resp: bytes = stub(b"", timeout=10)
        except Exception:
            return None
        finally:
            channel.close()

        return _parse_quota_proto(resp)

    finally:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )


if __name__ == "__main__":
    for m in list_models():
        print(m)
    os._exit(0)
