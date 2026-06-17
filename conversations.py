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

Tier-B slash-command equivalents (SQLite-based):
  fork_conversation()   — /fork   : clone a conversation to a new id
  rewind_conversation() — /rewind : remove the last N user turns
  export_conversation() — /export : write a markdown transcript to disk
"""
from __future__ import annotations

import glob
import os
import shutil
import sqlite3
import uuid

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


# ---------------------------------------------------------------------------
# Tier-B slash-command equivalents
# ---------------------------------------------------------------------------

def fork_conversation(source_conv_id: str) -> dict:
    """/fork — clone a conversation into a new independent copy.

    The new conversation starts with the same history as the source but gets a
    fresh UUID so future turns don't pollute the original.  cascade_id in the
    new DB is updated to match the new filename so agy can resume it via
    --conversation <new_id>.

    Returns: {forked_from, new_conversation_id}
    """
    source_conv_id = source_conv_id[:-3] if source_conv_id.endswith(".db") else source_conv_id
    src = os.path.join(CONV_DIR, source_conv_id + ".db")
    if not os.path.isfile(src):
        raise FileNotFoundError(f"conversation {source_conv_id!r} not found")

    new_id = str(uuid.uuid4())
    dst = os.path.join(CONV_DIR, new_id + ".db")
    shutil.copy2(src, dst)

    # cascade_id must match the filename so agy can look it up
    con = sqlite3.connect(dst)
    con.execute("UPDATE trajectory_meta SET cascade_id = ?", (new_id,))
    con.commit()
    con.close()

    return {"forked_from": source_conv_id, "new_conversation_id": new_id}


def rewind_conversation(conv_id: str, turns: int = 1) -> dict:
    """/rewind — remove the last N user turns and all their assistant steps.

    Finds the idx of the cut point (the N-th user turn from the end), then
    deletes every row at or after that idx from `steps` plus companion tables
    (gen_metadata, executor_metadata, parent_references, battle_mode_infos).

    Returns: {conversation_id, turns_removed, steps_deleted, remaining_turns}
    Raises ValueError if there aren't enough turns to rewind.
    """
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
            f"cannot rewind {turns} turn(s) — only {len(user_idxs)} turn(s) exist; "
            "rewinding all turns would leave an empty conversation"
        )

    cut_idx = user_idxs[-turns]  # first idx to delete

    con = sqlite3.connect(path)
    deleted = con.execute("DELETE FROM steps WHERE idx >= ?", (cut_idx,)).rowcount
    for tbl in ("gen_metadata", "executor_metadata", "parent_references", "battle_mode_infos"):
        con.execute(f"DELETE FROM {tbl} WHERE idx >= ?", (cut_idx,))
    con.commit()
    con.close()

    return {
        "conversation_id": conv_id,
        "turns_removed": turns,
        "steps_deleted": deleted,
        "remaining_turns": len(user_idxs) - turns,
    }


def export_conversation(conv_id: str, output_path: str | None = None) -> dict:
    """/export — write a conversation transcript to a markdown file.

    If output_path is omitted, saves to the current working directory as
    <conv_id>.md.  The file is UTF-8 and uses ### USER / ### MODEL headers.

    Returns: {saved_to, turns, chars}
    """
    conv_id = conv_id[:-3] if conv_id.endswith(".db") else conv_id
    transcript = format_transcript(conv_id)
    turns = len([t for t in read_conversation(conv_id)])

    if output_path is None:
        output_path = os.path.join(os.getcwd(), conv_id + ".md")

    parent = os.path.dirname(output_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    content = f"# Conversation {conv_id}\n\n{transcript}\n"
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(content)

    return {"saved_to": output_path, "turns": turns, "chars": len(content)}


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        print(format_transcript(sys.argv[1]))
    else:
        for c in list_conversations():
            print(f"{c['id']}  turns={c['user_turns']:>2}  db={c['db_bytes']:>7}  {c['title']!r}")
