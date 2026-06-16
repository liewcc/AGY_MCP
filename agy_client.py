"""Headless round-trip helper for Google's Antigravity CLI (`agy --print`).

Why this exists: `agy --print` runs the model to completion but writes the answer
ONLY to its SQLite trajectory store, never to stdout. So we invoke it console-less,
then read the assistant reply back from the newest conversation DB.

All configuration can be overridden via environment variables:
    AGY_BIN            path to agy.exe    (default: %LOCALAPPDATA%\\agy\\bin\\agy.exe)
    AGY_CONV_DIR       conversations dir  (default: ~/.gemini/antigravity-cli/conversations)
    AGY_TRUSTED_CWD    trusted working dir(default: first TRUST_FOLDER in
                                            ~/.gemini/trustedFolders.json)
    AGY_DEFAULT_MODEL  model display name (default: "Gemini 3 Pro")
    AGY_TIMEOUT        seconds            (default: 120)
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time

CREATE_NO_WINDOW = 0x08000000

_HOME = os.path.expanduser("~")
AGY_BIN = os.environ.get(
    "AGY_BIN", os.path.join(os.environ.get("LOCALAPPDATA", ""), "agy", "bin", "agy.exe")
)
CONV_DIR = os.environ.get(
    "AGY_CONV_DIR", os.path.join(_HOME, ".gemini", "antigravity-cli", "conversations")
)
DEFAULT_MODEL = os.environ.get("AGY_DEFAULT_MODEL", "Gemini 3 Pro")
DEFAULT_TIMEOUT = int(os.environ.get("AGY_TIMEOUT", "120"))


def _resolve_trusted_cwd() -> str:
    """Pick a folder the Antigravity CLI already trusts, so it won't block on a trust
    prompt. Order: AGY_TRUSTED_CWD env -> first TRUST_FOLDER in trustedFolders.json."""
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
        "No trusted folder found. Set AGY_TRUSTED_CWD to a folder listed in "
        "~/.gemini/trustedFolders.json, or trust a folder in the Antigravity CLI first."
    )


# ---- protobuf wire-format string extraction (no .proto needed) ----
def _varint(b, i):
    shift = val = 0
    while i < len(b):
        c = b[i]
        i += 1
        val |= (c & 0x7F) << shift
        if not (c & 0x80):
            break
        shift += 7
    return val, i


def _strings(b, depth=0, out=None):
    """Walk protobuf wire format, collecting (field_number, text) for every
    wire-type-2 chunk that decodes as mostly-printable UTF-8; recurse into the rest."""
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
            chunk = b[i:i + ln]
            i += ln
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


def _answer_from_db(path: str) -> str:
    """Extract the assistant reply from a conversation DB.

    The reply lives in rows where step_type=15, protobuf field 1. A single run can
    write several step_type=15 rows, some carrying stray tokens — so we take the
    SINGLE LONGEST field-1 string rather than joining them all (joining used to
    prepend junk like a stray `omd3nkj5` token). Falls back to the longest printable
    field of any number if field 1 is absent.
    """
    import sqlite3

    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    field1, anyfield = [], []
    try:
        for (payload,) in con.execute(
            "SELECT step_payload FROM steps WHERE step_type=15 ORDER BY idx"
        ):
            if not payload:
                continue
            for f, s in _strings(payload):
                anyfield.append(s)
                if f == 1:
                    field1.append(s)
    finally:
        con.close()
    pool = field1 or anyfield
    return max(pool, key=len).strip() if pool else ""


def ask_agy(prompt: str, model: str | None = None, add_dirs=None, timeout: int | None = None):
    """Run one headless prompt through the Antigravity CLI and return (answer, db_name).

    Args:
        prompt:   The prompt text.
        model:    Model display name (default AGY_DEFAULT_MODEL). "Gemini 3 Pro"
                  resolves to "Gemini 3.5 Flash (Medium)".
        add_dirs: Folders to expose to the agent for file/image analysis; reference
                  the file path inside the prompt — there is no upload flag.
        timeout:  Hard cap in seconds (default AGY_TIMEOUT).

    Returns:
        (answer_text, conversation_db_filename)
    """
    model = model or DEFAULT_MODEL
    timeout = timeout or DEFAULT_TIMEOUT
    if not os.path.isfile(AGY_BIN):
        raise RuntimeError(f"agy.exe not found at {AGY_BIN!r} (set AGY_BIN to override).")
    cwd = _resolve_trusted_cwd()

    args = [AGY_BIN, "--model", model, "--dangerously-skip-permissions"]
    for d in (add_dirs or []):
        args += ["--add-dir", d]
    args += ["--print", prompt]

    start = time.time()
    subprocess.run(
        args,
        cwd=cwd,
        env={**os.environ, "GEMINI_CLI_TRUST_WORKSPACE": "true"},
        stdin=subprocess.DEVNULL,   # immediate EOF -> the CLI won't hang waiting on stdin
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
        timeout=timeout,
    )

    dbs = [p for p in glob.glob(os.path.join(CONV_DIR, "*.db")) if os.path.getmtime(p) >= start - 1]
    if not dbs:
        raise RuntimeError("agy wrote no conversation DB for this run (auth/quota issue?).")
    newest = max(dbs, key=os.path.getmtime)
    answer = _answer_from_db(newest)
    if not answer:
        raise RuntimeError(f"agy produced an empty answer (db={os.path.basename(newest)}).")
    return answer, os.path.basename(newest)


if __name__ == "__main__":
    p = sys.argv[1] if len(sys.argv) > 1 else "What is 17 * 23? Reply with only the number."
    m = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MODEL
    t0 = time.time()
    ans, db = ask_agy(p, m)
    print(f"PROMPT : {p}")
    print(f"MODEL  : {m}")
    print(f"DB     : {db}   ({time.time() - t0:.1f}s)")
    print(f"ANSWER : {ans!r}")
