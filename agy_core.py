"""AGY_MCP — core library for all agy CLI communication.

Organized by access tier (A–F):
  A  headless --print round-trip + SQLite read-back        (ask_agy, run_agy_subcommand)
  A  ConPTY model list + gRPC quota                        (list_models, get_quota_summary)
  B  SQLite conversation management                        (list/read/fork/rewind/export)
  C  config file read/write                                (settings/keybindings/mcp/statusline/hooks/skills)
  D  shell subcommands / file ops                          (show_diff, open_path, logout)
  E  gRPC attach to a running agy session                  (list_tasks, agent_session_state)
  F  Windows ConPTY injection for interactive-only cmds    (run_goal, toggle_fast_mode, …)

Environment variable overrides:
  AGY_BIN          path to agy.exe  (default: %LOCALAPPDATA%\\agy\\bin\\agy.exe)
  AGY_CONV_DIR     conversations dir (default: ~/.gemini/antigravity-cli/conversations)
  AGY_TRUSTED_CWD  trusted cwd for headless runs
  AGY_DEFAULT_MODEL model display name (default: "Gemini 3 Pro")
  AGY_TIMEOUT      headless call timeout in seconds (default: 120)
"""
from __future__ import annotations

import ctypes
import glob
import json
import os
from pathlib import Path
import re
import shutil
import socket as _sock
import sqlite3
import ssl as _ssl
import stat
import subprocess
import sys
import threading
import time
import uuid
from ctypes import wintypes
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None

try:
    import grpc
except ImportError:
    grpc = None

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---------------------------------------------------------------------------
# Shared constants and paths
# ---------------------------------------------------------------------------

_HOME = os.path.expanduser("~")

AGY_BIN = os.environ.get(
    "AGY_BIN",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "agy", "bin", "agy.exe"),
)
CONV_DIR = os.environ.get(
    "AGY_CONV_DIR",
    os.path.join(_HOME, ".gemini", "antigravity-cli", "conversations"),
)
_AGY_HOME = os.path.join(_HOME, ".gemini", "antigravity-cli")
DEFAULT_MODEL = os.environ.get("AGY_DEFAULT_MODEL", "Gemini 3 Pro")
DEFAULT_TIMEOUT = int(os.environ.get("AGY_TIMEOUT", "120"))

# Windows process flags
_CREATE_NO_WINDOW = 0x08000000
_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400

# ANSI/VT escape stripper (superset: covers both model-list and PTY-injection output)
_ANSI = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[\]P].*?(?:\x07|\x1b\\)|\x1b[=>]|\r"
)
_SPINNER = re.compile(r"[⠀-⣿]\s*Fetching available models\.\.\.")


# ---------------------------------------------------------------------------
# Windows ConPTY structs (shared by list_models and Tier F injection)
# ---------------------------------------------------------------------------

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


def _conpty_start(cmdline_str: str, width: int = 220) -> tuple | None:
    """Allocate a ConPTY and start a process inside it.

    Returns (hpc, pi, in_write, out_read) on success, None on failure.
    Caller is responsible for cleanup via _conpty_kill().
    """
    in_read, in_write = wintypes.HANDLE(), wintypes.HANDLE()
    out_read, out_write = wintypes.HANDLE(), wintypes.HANDLE()
    if not _k32.CreatePipe(ctypes.byref(in_read), ctypes.byref(in_write), None, 0):
        return None
    if not _k32.CreatePipe(ctypes.byref(out_read), ctypes.byref(out_write), None, 0):
        _k32.CloseHandle(in_read); _k32.CloseHandle(in_write)
        return None

    hpc = wintypes.HANDLE()
    _k32.CreatePseudoConsole.restype = ctypes.c_long
    if _k32.CreatePseudoConsole(_COORD(width, 50), in_read, out_write, 0, ctypes.byref(hpc)) != 0:
        _k32.CloseHandle(in_read); _k32.CloseHandle(in_write)
        _k32.CloseHandle(out_read); _k32.CloseHandle(out_write)
        return None

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
    buf = ctypes.create_unicode_buffer(cmdline_str)
    if not _k32.CreateProcessW(
        None, buf, None, None, False,
        _EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT,
        None, None, ctypes.byref(siex.StartupInfo), ctypes.byref(pi),
    ):
        _k32.ClosePseudoConsole(hpc)
        _k32.CloseHandle(in_read); _k32.CloseHandle(in_write)
        _k32.CloseHandle(out_read); _k32.CloseHandle(out_write)
        return None

    # PTY owns in_read and out_write; we keep in_write (inject) and out_read (capture)
    _k32.CloseHandle(in_read)
    _k32.CloseHandle(out_write)
    return hpc, pi, in_write, out_read


def _conpty_kill(pid: int, hpc, pi, in_write, out_read) -> None:
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=_CREATE_NO_WINDOW,
    )
    _k32.ClosePseudoConsole(hpc)
    _k32.CloseHandle(out_read)
    _k32.CloseHandle(in_write)
    _k32.CloseHandle(pi.hThread)
    _k32.CloseHandle(pi.hProcess)


def _read_pty(out_read) -> tuple[list[bytes], threading.Thread]:
    """Start a background thread reading from out_read. Returns (chunks, thread)."""
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
    return chunks, t


# ---------------------------------------------------------------------------
# Tier A — headless --print round-trip + SQLite read-back
# ---------------------------------------------------------------------------

def _resolve_trusted_cwd() -> str:
    env = os.environ.get("AGY_TRUSTED_CWD")
    if env:
        return os.path.normpath(env)
    tf = os.path.join(_HOME, ".gemini", "trustedFolders.json")
    try:
        with open(tf, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    for folder, state in data.items():
        norm = os.path.normpath(folder)
        if state == "TRUST_FOLDER" and os.path.isdir(norm):
            return norm
    raise RuntimeError(
        "No trusted folder found. Set AGY_TRUSTED_CWD or trust a folder in agy first."
    )


def _varint(b: bytes, i: int) -> tuple[int, int]:
    shift = val = 0
    while i < len(b):
        c = b[i]; i += 1
        val |= (c & 0x7F) << shift
        if not (c & 0x80):
            break
        shift += 7
    return val, i


def _strings(b: bytes, depth: int = 0, out: list | None = None) -> list:
    """Walk protobuf wire format, collecting (field_number, text) for printable strings."""
    if out is None:
        out = []
    i, n = 0, len(b)
    while i < n:
        try:
            tag, i = _varint(b, i)
        except Exception:
            break
        wt = tag & 7
        if wt == 0:
            _, i = _varint(b, i)
        elif wt == 1:
            i += 8
        elif wt == 2:
            ln, i = _varint(b, i)
            chunk = b[i:i + ln]; i += ln
            if not chunk:
                continue
            nested = []
            if depth < 6:
                try:
                    _strings(chunk, depth + 1, nested)
                except Exception:
                    nested = []
            try:
                s = chunk.decode("utf-8")
                if s and sum(c.isprintable() or c in "\n\r\t" for c in s) / len(s) > 0.85:
                    out.append((tag >> 3, s))
                    continue
            except Exception:
                pass
            out.extend(nested)
        elif wt == 5:
            i += 4
        else:
            break
    return out


def _rows(path: str) -> list:
    """Return (idx, step_type, step_payload) rows from a conversation DB, or []."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return con.execute(
                "SELECT idx, step_type, step_payload FROM steps ORDER BY idx"
            ).fetchall()
        except sqlite3.DatabaseError:
            return []
        finally:
            con.close()
    except Exception:
        return []


def _answer_from_db(path: str) -> str:
    """Extract the assistant reply to the latest turn from a conversation DB."""
    rows = _rows(path)
    last_user = max((idx for idx, st, _ in rows if st == 14), default=-1)
    texts = []
    for idx, st, payload in rows:
        if st == 15 and idx > last_user and payload:
            texts.extend(s for f, s in _strings(payload) if f == 1)
    return max(texts, key=len).strip() if texts else ""


def ask_agy(
    prompt: str,
    model: str | None = None,
    add_dirs=None,
    timeout: int | None = None,
    conversation: str | None = None,
    working_dir: str | None = None,
) -> tuple[str, str]:
    """Run one headless prompt through agy --print; return (answer, conversation_id)."""
    model = model or DEFAULT_MODEL
    timeout = timeout or DEFAULT_TIMEOUT
    if not os.path.isfile(AGY_BIN):
        raise RuntimeError(f"agy.exe not found at {AGY_BIN!r} (set AGY_BIN to override).")
    cwd = os.path.normpath(working_dir) if working_dir else _resolve_trusted_cwd()

    args = [AGY_BIN, "--model", model, "--dangerously-skip-permissions"]
    if conversation:
        args += ["--conversation", conversation]
    for d in (add_dirs or []):
        args += ["--add-dir", d]
    args += ["--print", prompt]

    start = time.time()
    subprocess.run(
        args, cwd=cwd,
        env={**os.environ, "GEMINI_CLI_TRUST_WORKSPACE": "true"},
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=_CREATE_NO_WINDOW, timeout=timeout,
    )

    touched = sorted(
        (p for p in glob.glob(os.path.join(CONV_DIR, "*.db"))
         if os.path.getmtime(p) >= start - 1),
        key=os.path.getmtime, reverse=True,
    )
    if not touched:
        raise RuntimeError("agy wrote no conversation DB (auth/quota issue?).")
    for path in touched:
        answer = _answer_from_db(path)
        if answer:
            return answer, os.path.splitext(os.path.basename(path))[0]
    raise RuntimeError(
        f"agy produced an empty answer (dbs: {[os.path.basename(p) for p in touched]})."
    )


def run_agy_subcommand(*args: str, timeout: int = 30) -> str:
    """Run an agy subcommand and return its stdout (stderr kept separate)."""
    cmd = [AGY_BIN] + list(args)
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb"}
    try:
        res = subprocess.run(
            cmd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", timeout=timeout,
            creationflags=_CREATE_NO_WINDOW, env=env,
        )
        out = res.stdout or ""
        if res.returncode != 0:
            out += f"\nExit code: {res.returncode}"
        return out
    except subprocess.TimeoutExpired:
        return f"Timed out after {timeout}s"


def _ensure_console_session() -> None:
    """Allocate a hidden console so ConPTY works from a headless (no-console) process."""
    if _k32.GetConsoleWindow() == 0:
        _k32.AllocConsole()
        hwnd = _k32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE


# ---------------------------------------------------------------------------
# Tier A — model list (ConPTY agy models) + gRPC quota (used by TUI)
# ---------------------------------------------------------------------------

def _settings_model() -> str | None:
    """Read the currently-selected model label from agy's settings.json."""
    paths = [
        Path(os.path.expanduser("~")) / ".gemini" / "antigravity-cli" / "settings.json",
        Path(os.path.expanduser("~")) / ".gemini" / "settings.json",
    ]
    for p in paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            m = data.get("model")
            if m:
                return m
        except Exception:
            pass
    return None


_MODEL_NAME_RE = re.compile(r'((?:Gemini|Claude|GPT)[A-Za-z0-9 .\-]*\([A-Za-z]+\))')


def list_models(deadline_s: float = 25.0) -> list[str]:
    """Return available model display labels via agy gRPC GetAvailableModels.

    Starts a headless agy instance, waits for its gRPC language server, calls
    GetAvailableModels, extracts display names from the protobuf response, then
    kills the temporary agy process.  Falls back to settings.json current model.
    """
    # No-op debug sink: keeps the inline _dbg.write/flush/close calls harmless
    # without writing a _models_debug.txt file. ponytail: shim instead of
    # surgically removing ~15 scattered debug lines.
    _dbg = type("_NullDbg", (), {"write": lambda *a: None,
                                 "flush": lambda *a: None,
                                 "close": lambda *a: None})()

    current = _settings_model()
    fallback = [current] if current else []
    _dbg.write(f"current={current}\ngrpc={grpc}\nagy_exists={os.path.isfile(AGY_BIN)}\n")

    if grpc is None or not os.path.isfile(AGY_BIN):
        _dbg.write("EARLY RETURN: grpc None or no binary\n"); _dbg.close()
        return fallback

    def _local_ports() -> set[int]:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 "
             "-ErrorAction SilentlyContinue | Select-Object LocalPort | ConvertTo-Json"],
            capture_output=True, text=True, creationflags=_CREATE_NO_WINDOW,
        )
        return {int(m.group(1)) for m in re.finditer(r'"LocalPort":\s*(\d+)', r.stdout)}

    ports_before = _local_ports()
    _dbg.write(f"ports_before={len(ports_before)}\n"); _dbg.flush()
    proc = subprocess.Popen(
        [AGY_BIN], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, creationflags=_CREATE_NO_WINDOW,
    )
    _dbg.write(f"agy spawned pid={proc.pid}\n"); _dbg.flush()
    try:
        grpc_port: int | None = None
        deadline = time.time() + deadline_s
        while time.time() < deadline:
            time.sleep(0.5)
            new_ports = _local_ports() - ports_before
            if len(new_ports) >= 2:
                grpc_port = min(new_ports)
                break
        _dbg.write(f"grpc_port={grpc_port}\n"); _dbg.flush()
        if grpc_port is None:
            _dbg.write("FALLBACK: no grpc port found\n"); _dbg.close()
            return fallback

        time.sleep(4.0)

        ssl_ctx = _ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = _ssl.CERT_NONE
        try:
            with _sock.create_connection(("127.0.0.1", grpc_port), timeout=5) as s:
                with ssl_ctx.wrap_socket(s, server_hostname="localhost") as ts:
                    cert_pem = _ssl.DER_cert_to_PEM_cert(ts.getpeercert(binary_form=True))
            _dbg.write("ssl cert extracted ok\n"); _dbg.flush()
        except Exception as e:
            _dbg.write(f"ssl FAILED: {e}\n"); _dbg.close()
            return fallback

        creds = grpc.ssl_channel_credentials(root_certificates=cert_pem.encode())
        opts = [("grpc.ssl_target_name_override", "localhost"),
                ("grpc.default_authority", "localhost")]
        channel = grpc.secure_channel(f"127.0.0.1:{grpc_port}", creds, options=opts)
        try:
            stub = channel.unary_unary(
                "/exa.language_server_pb.LanguageServerService/GetAvailableModels",
                request_serializer=bytes, response_deserializer=bytes,
            )
            resp: bytes = stub(b"", timeout=10)
            _dbg.write(f"grpc ok, resp_len={len(resp)}\n"); _dbg.flush()
        except Exception as e:
            _dbg.write(f"grpc FAILED: {e}\n"); _dbg.close()
            return fallback
        finally:
            channel.close()

        # Use _strings() to properly parse protobuf rather than regex on raw bytes.
        # Require a trailing (Tier) suffix to match only selectable model entries;
        # this filters out internal base-model names like "Gemini 3 Flash" that
        # appear in other protobuf fields alongside the real display names.
        _MODEL_RE = re.compile(r'^(?:Gemini|Claude|GPT).+\([A-Za-z][A-Za-z0-9 ]*\)\s*$')
        seen: set[str] = set()
        models: list[str] = []
        all_strings = _strings(resp)
        _dbg.write(f"strings extracted: {len(all_strings)}\n")
        for _fn, s in all_strings:
            s = s.strip()
            if s and _MODEL_RE.match(s) and len(s) <= 80 and s not in seen:
                seen.add(s)
                models.append(s)
                _dbg.write(f"  model: {s!r}\n")
        _dbg.write(f"total models found: {len(models)}\n"); _dbg.close()

        _TIER = {"High": 0, "Medium": 1, "Low": 2, "Thinking": 3}
        _VENDOR = {"Gemini": 0, "Claude": 1, "GPT": 2}

        def _sort_key(name: str):
            vendor = next((v for k, v in _VENDOR.items() if name.startswith(k)), 9)
            ver = re.search(r'(\d+\.?\d*)', name)
            tier = re.search(r'\(([^)]+)\)\s*$', name)
            base = re.sub(r'\s*\([^)]+\)\s*$', '', name)
            return (vendor, -(float(ver.group(1)) if ver else 0), base,
                    _TIER.get(tier.group(1) if tier else "", 99))

        models.sort(key=_sort_key)

        if current and current not in seen:
            models.insert(0, current)

        return models if models else fallback
    finally:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )


def _parse_quota_proto(data: bytes) -> dict | None:
    """Parse raw protobuf from RetrieveUserQuotaSummary into a structured dict."""
    import struct

    def _vint(buf: bytes, pos: int) -> tuple[int, int]:
        val = 0; shift = 0
        while pos < len(buf):
            b = buf[pos]; pos += 1
            val |= (b & 0x7F) << shift; shift += 7
            if not (b & 0x80): break
        return val, pos

    def _fields(buf: bytes) -> list:
        pos = 0; out = []
        while pos < len(buf):
            tag, pos = _vint(buf, pos)
            fn, wt = tag >> 3, tag & 0x7
            if wt == 0:
                v, pos = _vint(buf, pos); out.append((fn, 0, v))
            elif wt == 1:
                if pos + 8 > len(buf): break
                out.append((fn, 1, struct.unpack_from("<Q", buf, pos)[0])); pos += 8
            elif wt == 2:
                n, pos = _vint(buf, pos)
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
    """Fetch Weekly/Five-Hour group quota from agy's local gRPC server.

    Starts agy headlessly, waits for its language server ports, extracts the
    self-signed TLS cert, calls RetrieveUserQuotaSummary, parses protobuf, kills
    agy. Takes ~15 s; always call off the UI thread.
    """
    if grpc is None or not os.path.isfile(AGY_BIN):
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
        [AGY_BIN], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, creationflags=_CREATE_NO_WINDOW,
    )
    try:
        grpc_port: int | None = None
        deadline = time.time() + deadline_s
        while time.time() < deadline:
            time.sleep(0.5)
            new_ports = _local_ports() - ports_before
            if len(new_ports) >= 2:
                grpc_port = min(new_ports)
                break
        if grpc_port is None:
            return None

        time.sleep(4.0)  # OAuth auth completes async inside agy

        ssl_ctx = _ssl.create_default_context()
        ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = _ssl.CERT_NONE
        try:
            with _sock.create_connection(("127.0.0.1", grpc_port), timeout=5) as s:
                with ssl_ctx.wrap_socket(s, server_hostname="localhost") as ts:
                    cert_pem = _ssl.DER_cert_to_PEM_cert(ts.getpeercert(binary_form=True))
        except Exception:
            return None

        creds = grpc.ssl_channel_credentials(root_certificates=cert_pem.encode())
        opts = [("grpc.ssl_target_name_override", "localhost"),
                ("grpc.default_authority", "localhost")]
        channel = grpc.secure_channel(f"127.0.0.1:{grpc_port}", creds, options=opts)
        try:
            stub = channel.unary_unary(
                "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary",
                request_serializer=bytes, response_deserializer=bytes,
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
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )


# ---------------------------------------------------------------------------
# Context stats — used by TUI Content panel
# ---------------------------------------------------------------------------

_CONTEXT_LIMITS: dict[str, int] = {
    "claude": 200_000,
    "gpt": 128_000,
}


def _model_context_limit(model_name: str) -> int:
    ml = (model_name or "").lower()
    for key, limit in _CONTEXT_LIMITS.items():
        if key in ml:
            return limit
    return 1_048_576  # Gemini default


def get_live_conversation_id() -> str | None:
    """Return the best conversation_id to show in the Content panel.

    Priority:
      1. A gRPC-detected interactive session whose .db has gen_metadata
         (user has already sent at least one prompt)
      2. The most recently modified .db that has gen_metadata
         (covers headless agy-mcp sessions and past interactive sessions)
    A gRPC session with an empty .db is skipped — it's a fresh session
    with no context yet, which is less useful than a session with real data.
    """
    def _has_gen_metadata(cid: str) -> bool:
        path = os.path.join(CONV_DIR, cid + ".db")
        if not os.path.isfile(path):
            return False
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                count = con.execute("SELECT COUNT(*) FROM gen_metadata").fetchone()[0]
                return count > 0
            finally:
                con.close()
        except Exception:
            return False

    # 1. Check gRPC interactive sessions (prefer ones with real data)
    if grpc is not None:
        for port in _listen_ports(_agy_pids()):
            try:
                ch = _grpc_channel(port)
            except Exception:
                continue
            try:
                cid = _active_conversation(ch, None)
                if cid and _has_gen_metadata(cid):
                    return cid
            except Exception:
                pass
            finally:
                try:
                    ch.close()
                except Exception:
                    pass

    # 2. Fall back to most recently modified .db with gen_metadata
    for path in sorted(
        glob.glob(os.path.join(CONV_DIR, "*.db")),
        key=os.path.getmtime, reverse=True,
    ):
        cid = os.path.splitext(os.path.basename(path))[0]
        if _has_gen_metadata(cid):
            return cid

    return None


def _parse_gen_metadata_tokens(data: bytes) -> dict:
    """Extract real token counts from gen_metadata protobuf blob.

    Path: top.f1.f4 → token struct with fields:
      f2 = prompt tokens (non-cached)
      f3 = step token count
      f5 = cached tokens
    Total context used = f2 + f5 (from latest row).
    """
    def get_field_bytes(buf: bytes, target_fn: int) -> bytes | None:
        i = 0
        while i < len(buf):
            try:
                tag, i = _varint(buf, i)
            except Exception:
                break
            fn, wt = tag >> 3, tag & 7
            if wt == 0:
                _, i = _varint(buf, i)
            elif wt == 1:
                i += 8
            elif wt == 5:
                i += 4
            elif wt == 2:
                ln, i2 = _varint(buf, i)
                chunk = buf[i2:i2 + ln]
                i = i2 + ln
                if fn == target_fn:
                    return chunk
            else:
                break
        return None

    def parse_varints(buf: bytes) -> dict:
        result: dict = {}
        i = 0
        while i < len(buf):
            try:
                tag, i = _varint(buf, i)
            except Exception:
                break
            fn, wt = tag >> 3, tag & 7
            if wt == 0:
                v, i = _varint(buf, i)
                result[fn] = v
            elif wt == 1:
                i += 8
            elif wt == 5:
                i += 4
            elif wt == 2:
                ln, i2 = _varint(buf, i)
                i = i2 + ln
            else:
                break
        return result

    f1 = get_field_bytes(data, 1)
    if f1 is None:
        return {}
    f4 = get_field_bytes(f1, 4)
    if f4 is None:
        return {}
    return parse_varints(f4)


def get_context_stats(conv_id: str | None = None) -> dict:
    """Return context usage stats for the active or most recent conversation.

    Priority:
      1. conv_id explicitly provided
      2. Live agy session via gRPC (get_live_conversation_id)
      3. Most recently modified .db file (fallback)

    Token counts are real values from gen_metadata (agy's actual tokenizer).
    Total context = latest row's f2 (prompt tokens) + f5 (cached tokens).
    Breakdown by step type uses each row's f3 (step token count).
    """
    live = False
    if conv_id is None:
        conv_id = get_live_conversation_id()
        if conv_id:
            live = True

    if conv_id:
        path = os.path.join(CONV_DIR, conv_id + ".db")
    else:
        dbs = sorted(
            glob.glob(os.path.join(CONV_DIR, "*.db")),
            key=os.path.getmtime, reverse=True,
        )
        if not dbs:
            return {"error": "no conversations found"}
        path = dbs[0]
        conv_id = os.path.splitext(os.path.basename(path))[0]

    if not os.path.isfile(path):
        return {"error": f"conversation not found: {conv_id}"}

    model = _settings_model() or "Gemini 3.5 Flash"
    context_limit = _model_context_limit(model)

    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            step_types = dict(con.execute(
                "SELECT idx, step_type FROM steps"
            ).fetchall())
            gen_rows = con.execute(
                "SELECT idx, data FROM gen_metadata ORDER BY idx"
            ).fetchall()
        finally:
            con.close()
    except Exception as e:
        return {"error": f"db read failed: {e}"}

    if not gen_rows:
        # Fresh session — no generation yet, matches agy /context showing 0 tokens
        return {
            "conversation_id": conv_id,
            "live": live,
            "model": model,
            "context_limit": context_limit,
            "user_tokens": 0,
            "model_tokens": 0,
            "tool_tokens": 0,
            "total_tokens": 0,
            "pct_used": 0.0,
        }

    # Per-step token counts for breakdown (f3 = step token count)
    user_tokens = model_tokens = tool_tokens = 0
    last_fields: dict = {}
    for idx, data in gen_rows:
        if not data:
            continue
        fields = _parse_gen_metadata_tokens(data)
        if not fields:
            continue
        last_fields = fields
        st = step_types.get(idx)
        f3 = fields.get(3, 0)
        if st == 14:
            user_tokens += f3
        elif st == 15:
            model_tokens += f3
        elif st == 33:
            tool_tokens += f3

    # Total context = latest row's f2 + f5
    total_tokens = last_fields.get(2, 0) + last_fields.get(5, 0)

    # Rescale breakdown proportionally to match total
    raw_sum = user_tokens + model_tokens + tool_tokens
    if raw_sum > 0 and total_tokens > 0:
        scale = total_tokens / raw_sum
        user_tokens = int(user_tokens * scale)
        model_tokens = int(model_tokens * scale)
        tool_tokens = int(tool_tokens * scale)

    return {
        "conversation_id": conv_id,
        "live": live,
        "model": model,
        "context_limit": context_limit,
        "user_tokens": user_tokens,
        "model_tokens": model_tokens,
        "tool_tokens": tool_tokens,
        "total_tokens": total_tokens,
        "pct_used": total_tokens / context_limit * 100 if context_limit else 0.0,
    }


# ---------------------------------------------------------------------------
# Tier B — SQLite conversation management
# ---------------------------------------------------------------------------

def _first_user_prompt(rows) -> str | None:
    for _idx, st, payload in rows:
        if st == 14 and payload:
            f2 = [s for f, s in _strings(payload) if f == 2]
            if f2:
                return max(f2, key=len).strip()
    return None


def list_conversations(limit: int = 20) -> list[dict]:
    """List non-empty conversations newest-first.

    Each item: {id, title, user_turns, modified, db_bytes}.
    """
    items = []
    for path in glob.glob(os.path.join(CONV_DIR, "*.db")):
        rows = _rows(path)
        if not rows:
            continue
        user_turns = sum(1 for _i, st, _p in rows if st == 14)
        title = _first_user_prompt(rows) or "(no prompt)"
        items.append({
            "id": os.path.splitext(os.path.basename(path))[0],
            "title": title[:120],
            "user_turns": user_turns,
            "modified": os.path.getmtime(path),
            "db_bytes": os.path.getsize(path),
        })
    items.sort(key=lambda c: c["modified"], reverse=True)
    return items[:limit]


def read_conversation(conv_id: str) -> list[dict]:
    """Reconstruct a conversation as a list of {role, text} turns."""
    conv_id = conv_id[:-3] if conv_id.endswith(".db") else conv_id
    path = os.path.join(CONV_DIR, conv_id + ".db")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no conversation db for id {conv_id!r}")

    turns = []
    pending = []

    def flush():
        if pending:
            turns.append({"role": "assistant", "text": max(pending, key=len).strip()})
            pending.clear()

    for _idx, st, payload in _rows(path):
        fields = _strings(payload) if payload else []
        if st == 14:
            flush()
            f2 = [s for f, s in fields if f == 2]
            if f2:
                turns.append({"role": "user", "text": max(f2, key=len).strip()})
        elif st == 15:
            f1 = [s for f, s in fields if f == 1]
            if f1:
                pending.append(max(f1, key=len))
    flush()
    return turns


def format_transcript(conv_id: str) -> str:
    """Human-readable transcript string."""
    lines = []
    for t in read_conversation(conv_id):
        who = "USER" if t["role"] == "user" else "MODEL"
        lines.append(f"### {who}\n{t['text']}")
    return "\n\n".join(lines) if lines else "(empty conversation)"


def fork_conversation(source_conv_id: str) -> dict:
    """/fork — clone a conversation to a new independent copy."""
    source_conv_id = source_conv_id[:-3] if source_conv_id.endswith(".db") else source_conv_id
    src = os.path.join(CONV_DIR, source_conv_id + ".db")
    if not os.path.isfile(src):
        raise FileNotFoundError(f"conversation {source_conv_id!r} not found")
    new_id = str(uuid.uuid4())
    dst = os.path.join(CONV_DIR, new_id + ".db")
    shutil.copy2(src, dst)
    con = sqlite3.connect(dst)
    con.execute("UPDATE trajectory_meta SET cascade_id = ?", (new_id,))
    con.commit(); con.close()
    return {"forked_from": source_conv_id, "new_conversation_id": new_id}


def rewind_conversation(conv_id: str, turns: int = 1) -> dict:
    """/rewind — remove the last N user turns and all their assistant steps."""
    conv_id = conv_id[:-3] if conv_id.endswith(".db") else conv_id
    path = os.path.join(CONV_DIR, conv_id + ".db")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"conversation {conv_id!r} not found")

    rows = _rows(path)
    user_idxs = [idx for idx, st, _ in rows if st == 14]
    if not user_idxs:
        raise ValueError("no user turns found in this conversation")
    if turns >= len(user_idxs):
        raise ValueError(
            f"cannot rewind {turns} turn(s) — only {len(user_idxs)} exist; "
            "rewinding all would leave an empty conversation"
        )
    cut_idx = user_idxs[-turns]
    con = sqlite3.connect(path)
    deleted = con.execute("DELETE FROM steps WHERE idx >= ?", (cut_idx,)).rowcount
    for tbl in ("gen_metadata", "executor_metadata", "parent_references", "battle_mode_infos"):
        con.execute(f"DELETE FROM {tbl} WHERE idx >= ?", (cut_idx,))
    con.commit(); con.close()
    return {"conversation_id": conv_id, "turns_removed": turns,
            "steps_deleted": deleted, "remaining_turns": len(user_idxs) - turns}


def export_conversation(conv_id: str, output_path: str | None = None) -> dict:
    """/export — write a conversation transcript to a markdown file."""
    conv_id = conv_id[:-3] if conv_id.endswith(".db") else conv_id
    transcript = format_transcript(conv_id)
    turns = len(read_conversation(conv_id))
    if output_path is None:
        output_path = os.path.join(os.getcwd(), conv_id + ".md")
    parent = os.path.dirname(output_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    content = f"# Conversation {conv_id}\n\n{transcript}\n"
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return {"saved_to": output_path, "turns": turns, "chars": len(content)}


# ---------------------------------------------------------------------------
# Tier C — configuration file read/write
# ---------------------------------------------------------------------------

def _ensure_agy_home() -> None:
    os.makedirs(_AGY_HOME, exist_ok=True)


def read_settings() -> dict:
    """/config — read settings.json."""
    path = os.path.join(_AGY_HOME, "settings.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as e:
        return {"error": str(e)}


def write_settings(settings: dict) -> dict:
    """/config — write settings.json."""
    _ensure_agy_home()
    path = os.path.join(_AGY_HOME, "settings.json")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2)
        return {"saved_to": path, "keys": list(settings.keys())}
    except Exception as e:
        return {"error": str(e)}


def read_keybindings() -> dict:
    """/keybindings — read keybindings.json."""
    path = os.path.join(_AGY_HOME, "keybindings.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as e:
        return {"error": str(e)}


def write_keybindings(keybindings: dict) -> dict:
    """/keybindings — write keybindings.json."""
    _ensure_agy_home()
    path = os.path.join(_AGY_HOME, "keybindings.json")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(keybindings, fh, indent=4)
        return {"saved_to": path, "actions": len(keybindings)}
    except Exception as e:
        return {"error": str(e)}


def list_skills() -> dict:
    """/skills — list .md skill files under agy home."""
    try:
        skills = sorted(
            os.path.relpath(p, _AGY_HOME)
            for p in glob.glob(os.path.join(_AGY_HOME, "**", "*.md"), recursive=True)
        )
        return {"skills": skills, "count": len(skills)}
    except Exception as e:
        return {"error": str(e)}


def read_mcp_config() -> dict:
    """/mcp — read mcp.json."""
    path = os.path.join(_AGY_HOME, "mcp.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as e:
        return {"error": str(e)}


def write_mcp_config(config: dict) -> dict:
    """/mcp — write mcp.json."""
    _ensure_agy_home()
    path = os.path.join(_AGY_HOME, "mcp.json")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2)
        return {"saved_to": path, "servers": list(config.get("mcpServers", {}).keys())}
    except Exception as e:
        return {"error": str(e)}


def read_statusline_config() -> dict:
    """/statusline — read statusline.yaml."""
    if yaml is None:
        return {"error": "pyyaml not installed"}
    path = os.path.join(_AGY_HOME, "statusline.yaml")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as e:
        return {"error": str(e)}


def write_statusline_config(config: dict) -> dict:
    """/statusline — write statusline.yaml."""
    if yaml is None:
        return {"error": "pyyaml not installed"}
    _ensure_agy_home()
    path = os.path.join(_AGY_HOME, "statusline.yaml")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            yaml.dump(config, fh, default_flow_style=False, allow_unicode=True)
        return {"saved_to": path, "keys": list(config.keys())}
    except Exception as e:
        return {"error": str(e)}


def list_hooks() -> dict:
    """/hooks — list hook scripts in agy home."""
    hooks_dir = os.path.join(_AGY_HOME, "hooks")
    hooks_list = []
    if os.path.isdir(hooks_dir):
        for fname in os.listdir(hooks_dir):
            path = os.path.join(hooks_dir, fname)
            if os.path.isfile(path):
                hooks_list.append({
                    "name": fname,
                    "executable": os.access(path, os.X_OK),
                    "size": os.path.getsize(path),
                    "path": path,
                })
    return {"hooks": sorted(hooks_list, key=lambda h: h["name"]), "count": len(hooks_list)}


def read_hook_script(hook_name: str) -> dict:
    """/hooks — read a specific hook script."""
    path = os.path.join(_AGY_HOME, "hooks", hook_name)
    if not os.path.isfile(path):
        return {"error": f"hook not found: {hook_name}"}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return {"content": fh.read(), "path": path, "executable": os.access(path, os.X_OK)}
    except Exception as e:
        return {"error": str(e)}


def write_hook_script(hook_name: str, content: str, executable: bool = True) -> dict:
    """/hooks — write or update a hook script."""
    _ensure_agy_home()
    hooks_dir = os.path.join(_AGY_HOME, "hooks")
    os.makedirs(hooks_dir, exist_ok=True)
    path = os.path.join(hooks_dir, hook_name)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        if executable and hasattr(stat, "S_IXUSR"):
            st = os.stat(path)
            os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return {"saved_to": path, "executable": os.access(path, os.X_OK)}
    except Exception as e:
        return {"error": str(e)}


def get_config_info() -> dict:
    """Unified summary of all configuration state."""
    settings = read_settings()
    keybindings = read_keybindings()
    mcp = read_mcp_config()
    statusline = read_statusline_config()
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
        "hooks": list_hooks(),
        "skills": list_skills(),
        "agy_home": _AGY_HOME,
    }


# ---------------------------------------------------------------------------
# Tier D — shell subcommands and file operations
# ---------------------------------------------------------------------------

def show_diff(path: str | None = None, working_dir: str | None = None) -> str:
    """/diff — show git diff for the workspace or a specific path."""
    working_dir = working_dir or os.getcwd()
    if path:
        path = os.path.normpath(path)
        if not os.path.exists(path):
            return f"error: path not found: {path!r}"
    try:
        cmd = ["git", "diff"]
        if path:
            cmd.append(path)
        result = subprocess.run(cmd, cwd=working_dir, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return f"git error:\n{result.stderr}"
        return result.stdout or "(no changes)"
    except subprocess.TimeoutExpired:
        return "git diff timed out (>10s)"
    except FileNotFoundError:
        return "error: git not found in PATH"
    except Exception as e:
        return f"error: {e}"


def open_path(path: str) -> dict:
    """/open — open a file or directory in the system default application."""
    path = os.path.normpath(path)
    if not os.path.exists(path):
        return {"error": f"path not found: {path!r}"}
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.run(["open", path], check=True, timeout=5)
        else:
            subprocess.run(["xdg-open", path], check=True, timeout=5)
        return {"opened": os.path.abspath(path), "status": "success"}
    except Exception as e:
        return {"error": str(e)}


def logout() -> dict:
    """/logout — delete OAuth credentials and clear auth state."""
    deleted = []; errors = []
    token_paths = [
        os.path.join(_AGY_HOME, "auth.json"),
        os.path.join(_HOME, ".config", "gcloud", "application_default_credentials.json"),
        os.path.join(_HOME, ".config", "gcloud", "credentials.json"),
    ]
    for p in token_paths:
        if os.path.isfile(p):
            try: os.remove(p); deleted.append(p)
            except Exception as e: errors.append(f"{p}: {e}")
        elif os.path.isdir(p):
            try: shutil.rmtree(p); deleted.append(p)
            except Exception as e: errors.append(f"{p}: {e}")
    if deleted:
        return {"deleted": deleted, "status": "success"}
    elif errors:
        return {"error": "; ".join(errors)}
    return {"status": "no credentials found to delete"}


# ---------------------------------------------------------------------------
# Tier E — gRPC attach to a running agy session
# ---------------------------------------------------------------------------

_SVC = "/exa.language_server_pb.LanguageServerService"
_UUID_RE = re.compile(rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_CMD_JSON_RE = re.compile(r'\{[^{}]*"CommandLine"[^{}]*\}')


def _agy_pids() -> list[int]:
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "Get-Process agy -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id"],
        capture_output=True, text=True, creationflags=_CREATE_NO_WINDOW,
    )
    return [int(x) for x in re.findall(r"\d+", r.stdout)]


def _listen_ports(pids: list[int]) -> list[int]:
    if not pids:
        return []
    pidset = ",".join(str(p) for p in pids)
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         f"Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 "
         f"-ErrorAction SilentlyContinue | Where-Object {{ @({pidset}) -contains "
         f"$_.OwningProcess }} | Select-Object -ExpandProperty LocalPort"],
        capture_output=True, text=True, creationflags=_CREATE_NO_WINDOW,
    )
    return sorted(int(m) for m in re.findall(r"\d+", r.stdout))


def _grpc_channel(port: int):
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False; ctx.verify_mode = _ssl.CERT_NONE
    with _sock.create_connection(("127.0.0.1", port), timeout=5) as s:
        with ctx.wrap_socket(s, server_hostname="localhost") as ts:
            cert_pem = _ssl.DER_cert_to_PEM_cert(ts.getpeercert(binary_form=True))
    creds = grpc.ssl_channel_credentials(root_certificates=cert_pem.encode())
    return grpc.secure_channel(
        f"127.0.0.1:{port}", creds,
        options=[("grpc.ssl_target_name_override", "localhost"),
                 ("grpc.default_authority", "localhost")],
    )


def _grpc_attach():
    """Return a connected gRPC channel to the running agy session, or None."""
    for port in _listen_ports(_agy_pids()):
        try:
            ch = _grpc_channel(port)
        except Exception:
            continue
        try:
            ch.unary_unary(f"{_SVC}/GetUserStatus",
                           request_serializer=bytes,
                           response_deserializer=bytes)(b"", timeout=8)
            return ch
        except Exception:
            ch.close()
    return None


def _pb_string_field(fn: int, s: str) -> bytes:
    b = s.encode()
    out = bytearray([(fn << 3) | 2])
    n = len(b)
    while True:
        x = n & 0x7F; n >>= 7
        out.append(x | (0x80 if n else 0))
        if not n: break
    return bytes(out) + b


def _active_conversation(ch, conversation_id: str | None) -> str | None:
    if conversation_id:
        return conversation_id
    try:
        resp = ch.unary_unary(f"{_SVC}/GetAllCascadeTrajectories",
                              request_serializer=bytes,
                              response_deserializer=bytes)(b"", timeout=8)
    except Exception:
        return None
    ids = list(dict.fromkeys(m.decode() for m in _UUID_RE.findall(resp)))
    return ids[0] if ids else None


def _state_snapshot(ch, conversation_id: str) -> bytes | None:
    call = ch.unary_stream(f"{_SVC}/StreamAgentStateUpdates",
                           request_serializer=bytes,
                           response_deserializer=bytes)(
        _pb_string_field(1, conversation_id), timeout=8)
    try:
        for msg in call:
            return msg
    except Exception:
        return None
    finally:
        call.cancel()
    return None


def list_tasks(conversation_id: str | None = None) -> dict:
    """/tasks — background shell commands in the live agy session."""
    if grpc is None:
        return {"error": "grpcio not installed"}
    ch = _grpc_attach()
    if ch is None:
        return {"status": "no running agy session found"}
    try:
        cid = _active_conversation(ch, conversation_id)
        if cid is None:
            return {"status": "no active conversation in the running agy session"}
        snap = _state_snapshot(ch, cid)
        if not snap:
            return {"conversation_id": cid, "tasks": []}

        text = snap.decode("latin-1")
        tasks, seen = [], set()
        for m in _CMD_JSON_RE.finditer(text):
            try:
                obj = json.loads(m.group(0))
            except json.JSONDecodeError:
                continue
            cmd = obj.get("CommandLine", "")
            key = (cmd, obj.get("Cwd", ""))
            if key in seen:
                continue
            seen.add(key)
            wait = obj.get("WaitMsBeforeAsync")
            tasks.append({"command": cmd, "cwd": obj.get("Cwd", ""),
                          "summary": obj.get("toolSummary", ""),
                          "action": obj.get("toolAction", ""),
                          "background": bool(wait), "wait_ms": wait})
        return {"conversation_id": cid, "tasks": tasks}
    finally:
        ch.close()


def agent_session_state(conversation_id: str | None = None) -> dict:
    """/agents — agent/subagent runtime state of the live agy session."""
    if grpc is None:
        return {"error": "grpcio not installed"}
    ch = _grpc_attach()
    if ch is None:
        return {"status": "no running agy session found"}
    try:
        cid = _active_conversation(ch, conversation_id)
        if cid is None:
            return {"status": "no active conversation in the running agy session"}
        snap = _state_snapshot(ch, cid)
        if not snap:
            return {"conversation_id": cid, "tool_actions": []}

        text = snap.decode("latin-1")
        conv_ids = list(dict.fromkeys(m.decode() for m in _UUID_RE.findall(snap)))
        actions, seen = [], set()
        for m in _CMD_JSON_RE.finditer(text):
            try:
                obj = json.loads(m.group(0))
            except json.JSONDecodeError:
                continue
            pair = (obj.get("toolAction", ""), obj.get("toolSummary", ""))
            if pair == ("", "") or pair in seen:
                continue
            seen.add(pair)
            actions.append({"action": pair[0], "summary": pair[1]})
        return {"conversation_id": cid, "conversation_ids": conv_ids,
                "tool_actions": actions, "snapshot_bytes": len(snap)}
    finally:
        ch.close()


# ---------------------------------------------------------------------------
# Tier F — Windows ConPTY injection for interactive-only slash commands
# ---------------------------------------------------------------------------

def _pty_inject_slash(
    slash_cmd: str,
    wait_startup_s: float = 3.5,
    wait_response_s: float = 8.0,
) -> dict:
    """Start agy in interactive ConPTY mode, inject a slash command, return output."""
    if not os.path.isfile(AGY_BIN):
        return {"error": f"agy not found: {AGY_BIN}"}
    _ensure_console_session()

    res = _conpty_start(f'"{AGY_BIN}"')
    if res is None:
        return {"error": "ConPTY creation failed"}
    hpc, pi, in_write, out_read = res
    chunks, t = _read_pty(out_read)
    pid = pi.dwProcessId

    try:
        time.sleep(wait_startup_s)
        # ESC dismisses autocomplete; Enter then executes the command
        payload = (slash_cmd + "\x1b\r").encode("utf-8")
        written = wintypes.DWORD(0)
        _k32.WriteFile(in_write, payload, len(payload), ctypes.byref(written), None)
        time.sleep(wait_response_s)
    finally:
        _conpty_kill(pid, hpc, pi, in_write, out_read)
        t.join(timeout=3.0)

    raw = b"".join(chunks).decode("utf-8", "replace")
    lines = [ln.strip() for ln in _ANSI.sub("", raw).splitlines() if ln.strip()]
    return {"output": "\n".join(lines), "pid": pid}


def toggle_fast_mode() -> dict:
    """/fast — toggle agy fast/thinking mode."""
    return _pty_inject_slash("/fast", wait_startup_s=3.5, wait_response_s=5.0)


def _debug_model_raw() -> str:
    """Return raw cleaned ConPTY output from /model injection for debugging."""
    if not os.path.isfile(AGY_BIN):
        return f"agy not found: {AGY_BIN}"
    res = _conpty_start(f'"{AGY_BIN}"')
    if not res:
        return "ConPTY creation failed"
    hpc, pi, in_write, out_read = res
    chunks, t = _read_pty(out_read)
    pid = pi.dwProcessId
    written = ctypes.c_ulong(0)
    try:
        time.sleep(4.0)
        _k32.WriteFile(in_write, b"/model\r\n", 8, ctypes.byref(written), None)
        time.sleep(3.0)
        _k32.WriteFile(in_write, b"\x1b", 1, ctypes.byref(written), None)
        time.sleep(0.5)
    finally:
        _conpty_kill(pid, hpc, pi, in_write, out_read)
        t.join(timeout=3.0)
    raw = b"".join(chunks).decode("utf-8", "replace")
    clean = _ANSI.sub("", raw)
    clean = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', clean)
    return clean[-3000:] if len(clean) > 3000 else clean


def set_model(model_name: str) -> dict:
    """/model — switch the active model (persists across sessions)."""
    return _pty_inject_slash(f"/model {model_name}", wait_startup_s=3.5, wait_response_s=6.0)


def run_goal(description: str) -> dict:
    """/goal — set an autonomous execution goal."""
    return _pty_inject_slash(f"/goal {description}", wait_startup_s=3.5, wait_response_s=10.0)


def start_planning(description: str = "") -> dict:
    """/planning — kick off a multi-turn planning session."""
    return _pty_inject_slash(f"/planning {description}".rstrip(), wait_startup_s=3.5, wait_response_s=12.0)


def start_schedule(description: str) -> dict:
    """/schedule — set a scheduled/cron task."""
    return _pty_inject_slash(f"/schedule {description}", wait_startup_s=3.5, wait_response_s=8.0)


def start_grill_me() -> dict:
    """/grill-me — start an interactive Q&A alignment session."""
    return _pty_inject_slash("/grill-me", wait_startup_s=3.5, wait_response_s=10.0)


def start_teamwork_preview() -> dict:
    """/teamwork-preview — launch multi-agent teamwork preview."""
    return _pty_inject_slash("/teamwork-preview", wait_startup_s=3.5, wait_response_s=10.0)


def ask_btw(query: str, conversation_id: Optional[str] = None) -> dict:
    """/btw — background side-question, simulated via agy --print."""
    answer, conv_id = ask_agy(query, conversation=conversation_id)
    return {"answer": answer, "conversation_id": conv_id}
