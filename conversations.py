"""Read-back utilities for Antigravity CLI conversations.

Where sessions live (two complementary stores):
  * ~/.gemini/antigravity-cli/conversations/<id>.db  — per-conversation SQLite holding
    the full turn-by-turn trajectory (table `steps`). AUTHORITATIVE. Each run also
    leaves an empty ~48 KB companion shell (no steps) which we skip.
  * ~/.gemini/antigravity-cli/history.jsonl          — flat append-only index, one JSON
    object per *interactive* prompt: {display, timestamp, workspace, conversationId}.
    Handy as a human-readable log, but `--print` runs are not guaranteed to appear here,
    so listing is driven off the .db files instead.

list_conversations() enumerates the .db files; read_conversation() reconstructs one.
"""
from __future__ import annotations

import glob
import os

from agy_client import CONV_DIR, _rows, _strings


def _first_user_prompt(rows) -> str | None:
    for _idx, st, payload in rows:
        if st == 14 and payload:
            f2 = [s for f, s in _strings(payload) if f == 2]
            if f2:
                return max(f2, key=len).strip()
    return None


def list_conversations(limit: int = 20):
    """List real (non-empty) conversations, newest-first.

    Each item: {id, title, user_turns, modified, db_bytes}. `title` is the first user
    prompt. `modified` is the db mtime (epoch seconds).
    """
    items = []
    for path in glob.glob(os.path.join(CONV_DIR, "*.db")):
        rows = _rows(path)
        if not rows:
            continue  # empty companion shell
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


def read_conversation(conv_id: str):
    """Reconstruct a conversation's transcript as a list of {role, text} turns.

    user  = step_type 14, clean prompt in protobuf field 2.
    model = step_type 15; consecutive 15-rows within a turn are collapsed to their
            longest field-1 string (drops stray tool-call ids like `omd3nkj5`).
    """
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
    """Human-readable transcript string for a conversation."""
    lines = []
    for t in read_conversation(conv_id):
        who = "USER" if t["role"] == "user" else "MODEL"
        lines.append(f"### {who}\n{t['text']}")
    return "\n\n".join(lines) if lines else "(empty conversation)"


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        print(format_transcript(sys.argv[1]))
    else:
        for c in list_conversations():
            print(f"{c['id']}  turns={c['user_turns']:>2}  db={c['db_bytes']:>7}  {c['title']!r}")
