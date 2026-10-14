"""Blinded public transcripts.

Each conversation gets an opaque random ID unrelated to persona or model, and
neutral sequential message IDs (m001, m002, ...). The canonical JSON object has
exactly `transcript_id` and `messages`; each message has exactly `message_id`,
`role` (user | assistant) and `content` (the unchanged public text). The
mapping from transcript ID to scenario and models lives only in private records.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import Any

MESSAGE_KEYS = ("message_id", "role", "content")


def new_transcript_id() -> str:
    return f"tr_{secrets.token_hex(6)}"


def message_id(index: int) -> str:
    """Neutral ID for the index-th public message (1-based)."""
    return f"m{index:03d}"


def canonical(transcript_id: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    return {"transcript_id": transcript_id,
            "messages": [{k: m[k] for k in MESSAGE_KEYS} for m in messages]}


def content_hash(transcript: dict[str, Any]) -> str:
    """SHA-256 of the transcript's canonical serialization (key order and spacing fixed)."""
    blob = json.dumps(transcript, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def validate(transcript: Any) -> list[str]:
    """Problems with the canonical transcript structure (empty list = valid)."""
    problems: list[str] = []
    if not isinstance(transcript, dict) or set(transcript) != {"transcript_id", "messages"}:
        return ["transcript must be an object with exactly transcript_id and messages"]
    seen: set[str] = set()
    for i, m in enumerate(transcript["messages"]):
        if not isinstance(m, dict) or set(m) != set(MESSAGE_KEYS):
            problems.append(f"message {i}: must have exactly {MESSAGE_KEYS}")
            continue
        if m["role"] not in ("user", "assistant"):
            problems.append(f"{m['message_id']}: role must be user or assistant")
        if not isinstance(m["content"], str):
            problems.append(f"{m['message_id']}: content must be a string")
        if m["message_id"] in seen:
            problems.append(f"duplicate message_id {m['message_id']}")
        seen.add(m["message_id"])
    return problems


def readable(transcript: dict[str, Any]) -> str:
    lines = [f"Transcript {transcript['transcript_id']}", ""]
    for m in transcript["messages"]:
        lines += [f"[{m['message_id']}] {m['role'].upper()}", m["content"], ""]
    return "\n".join(lines)


def save(directory: Path, transcript: dict[str, Any]) -> Path:
    """Write <id>.json (canonical) and <id>.txt (readable); check the JSON decodes exactly."""
    directory.mkdir(parents=True, exist_ok=True)
    tid = transcript["transcript_id"]
    blob = json.dumps(transcript, ensure_ascii=False, indent=2)
    if json.loads(blob) != transcript:
        raise RuntimeError(f"{tid}: JSON round trip changed the transcript")
    path = directory / f"{tid}.json"
    path.write_text(blob + "\n", encoding="utf-8")
    (directory / f"{tid}.txt").write_text(readable(transcript), encoding="utf-8")
    return path


def load(path: Path) -> dict[str, Any]:
    transcript = json.loads(path.read_text(encoding="utf-8"))
    problems = validate(transcript)
    if problems:
        raise ValueError(f"{path}: " + "; ".join(problems))
    return transcript


def word_count(text: str) -> int:
    """Whitespace-separated units in the unchanged public message (run plan word_count_rule)."""
    return len(text.split())


def length_record(messages: list[dict[str, str]]) -> dict[str, Any]:
    by_role: dict[str, dict[str, int]] = {"user": {"messages": 0, "words": 0},
                                          "assistant": {"messages": 0, "words": 0}}
    per_message = []
    for m in messages:
        w = word_count(m["content"])
        by_role[m["role"]]["messages"] += 1
        by_role[m["role"]]["words"] += w
        per_message.append({"message_id": m["message_id"], "role": m["role"], "words": w})
    return {
        "message_count_by_speaker": {r: v["messages"] for r, v in by_role.items()},
        "word_count_by_speaker": {r: v["words"] for r, v in by_role.items()},
        "word_count_each_message": per_message,
        "assistant_reply_count": by_role["assistant"]["messages"],
    }
