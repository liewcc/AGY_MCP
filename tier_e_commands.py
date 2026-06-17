"""Tier-E slash-command equivalents: live runtime state of a RUNNING agy session.

`/tasks` (background shell commands) and `/agents` (subagent state) report
*in-memory* runtime state that belongs to the user's interactive `agy` process —
it does not exist in a freshly spawned headless instance (the sidecar/agent
managers are never initialised). So these tools attach to the already-running
session via its local gRPC language server.

How the attach works (validated by reverse-engineering, see command_access_tiers.md):
  1. Each `agy` process runs its own language server listening on two 127.0.0.1
     ports (lower = TLS gRPC, higher = LSP). We find the running agy PIDs and the
     ports they own (Get-NetTCPConnection -OwningProcess), no hard-coded ports.
  2. The gRPC TLS cert is self-signed; we extract it live from the socket and trust
     it for that one connection. No auth header is required for the local server.
  3. GetAllCascadeTrajectories -> the active conversation id.
  4. StreamAgentStateUpdates(conversation_id) -> a full state snapshot whose tool
     actions embed flat JSON (CommandLine / Cwd / WaitMsBeforeAsync / toolAction /
     toolSummary). The background commands are parsed straight out of that.

Returns a clear "no running agy session" status when nothing is attached, so the
caller can tell "nothing running" apart from "couldn't reach it".
"""
from __future__ import annotations

import json
import re
import socket as _sock
import ssl as _ssl
import subprocess

try:
    import grpc
except ImportError:  # grpc is optional; tools degrade to a clear error
    grpc = None

from agy_models import _CREATE_NO_WINDOW

_SVC = "/exa.language_server_pb.LanguageServerService"
_UUID_RE = re.compile(
    rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
# Flat JSON object containing a CommandLine key (a command tool-call's args).
_CMD_JSON_RE = re.compile(r'\{[^{}]*"CommandLine"[^{}]*\}')


# ---- process / port discovery -------------------------------------------------


def _agy_pids() -> list[int]:
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "Get-Process agy -ErrorAction SilentlyContinue | "
         "Select-Object -ExpandProperty Id"],
        capture_output=True, text=True, creationflags=_CREATE_NO_WINDOW,
    )
    return [int(x) for x in re.findall(r"\d+", r.stdout)]


def _listen_ports(pids: list[int]) -> list[int]:
    """Sorted 127.0.0.1 LISTEN ports owned by the given PIDs (lowest first)."""
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


def _channel(port: int):
    """Build a secure gRPC channel to a local agy port using its live cert."""
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    with _sock.create_connection(("127.0.0.1", port), timeout=5) as s:
        with ctx.wrap_socket(s, server_hostname="localhost") as ts:
            cert_pem = _ssl.DER_cert_to_PEM_cert(ts.getpeercert(binary_form=True))
    creds = grpc.ssl_channel_credentials(root_certificates=cert_pem.encode())
    return grpc.secure_channel(
        f"127.0.0.1:{port}", creds,
        options=[("grpc.ssl_target_name_override", "localhost"),
                 ("grpc.default_authority", "localhost")],
    )


def _attach():
    """Return a connected channel to a running agy gRPC server, or None.

    Probes each candidate port with the global GetUserStatus call to confirm it's
    the gRPC port (the higher LSP port fails the TLS/gRPC handshake).
    """
    for port in _listen_ports(_agy_pids()):
        try:
            ch = _channel(port)
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


# ---- request helpers ----------------------------------------------------------


def _string_field(fn: int, s: str) -> bytes:
    b = s.encode()
    out = bytearray([(fn << 3) | 2])
    n = len(b)
    while True:
        x = n & 0x7F
        n >>= 7
        out.append(x | (0x80 if n else 0))
        if not n:
            break
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
    """First message of StreamAgentStateUpdates — the full state snapshot."""
    call = ch.unary_stream(f"{_SVC}/StreamAgentStateUpdates",
                           request_serializer=bytes,
                           response_deserializer=bytes)(
        _string_field(1, conversation_id), timeout=8)
    try:
        for msg in call:
            return msg
    except Exception:
        return None
    finally:
        call.cancel()
    return None


# ---- public tools -------------------------------------------------------------


def list_tasks(conversation_id: str | None = None) -> dict:
    """/tasks — background shell commands in the live agy session.

    Args:
        conversation_id: Inspect a specific conversation; default = the active one.

    Returns:
        {"conversation_id", "tasks": [{command, cwd, summary, action,
         background, wait_ms}]} — or {"status": "..."} when no session/conversation.
    """
    if grpc is None:
        return {"error": "grpcio not installed"}
    ch = _attach()
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
            tasks.append({
                "command": cmd,
                "cwd": obj.get("Cwd", ""),
                "summary": obj.get("toolSummary", ""),
                "action": obj.get("toolAction", ""),
                "background": bool(wait),  # async-after-wait => runs in background
                "wait_ms": wait,
            })
        return {"conversation_id": cid, "tasks": tasks}
    finally:
        ch.close()


def agent_session_state(conversation_id: str | None = None) -> dict:
    """/agents — agent / subagent runtime state of the live agy session.

    Args:
        conversation_id: Inspect a specific conversation; default = the active one.

    Returns:
        {"conversation_id", "conversation_ids" (all loaded trajectories),
         "tool_actions": [{action, summary}], "snapshot_bytes"} — or {"status": ...}.
    """
    if grpc is None:
        return {"error": "grpcio not installed"}
    ch = _attach()
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
        # All trajectory ids present in the snapshot (parent + spawned subagents).
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
        return {
            "conversation_id": cid,
            "conversation_ids": conv_ids,
            "tool_actions": actions,
            "snapshot_bytes": len(snap),
        }
    finally:
        ch.close()


if __name__ == "__main__":
    print("=== /tasks ===")
    print(json.dumps(list_tasks(), indent=2, ensure_ascii=False))
    print("\n=== /agents ===")
    print(json.dumps(agent_session_state(), indent=2, ensure_ascii=False))
