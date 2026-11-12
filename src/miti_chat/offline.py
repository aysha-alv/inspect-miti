"""Offline stand-in for every model role (no API calls, no cost).

Used by `miti dry-run` and the tests to exercise the whole pipeline: request
assembly, stopping, storage, judge-output checks, counts and exports. Its text
is placeholder output, not a simulation or a coding.
"""

from __future__ import annotations

import json
from typing import Any

from inspect_ai.model import ChatMessage, GenerateConfig, ModelOutput, ModelUsage

from .calls import OFFLINE_RESPONDER
from .config import Protocol

OFFLINE_MODEL = "mockllm/offline"
PROTOCOL_VERSION = Protocol().version


def _system(messages: list[ChatMessage]) -> str:
    return messages[0].text if messages and messages[0].role == "system" else ""


def _dialogue(user_text: str) -> tuple[str, list[dict[str, str]]]:
    tid = user_text.split("TRANSCRIPT_ID:", 1)[1].split("\n", 1)[0].strip()
    body = user_text.split("DIALOGUE", 1)[1].split("\n", 1)[1]
    return tid, json.loads(body)


def _globals(tid: str) -> dict[str, Any]:
    rating = {"score": 3, "rationale": "Offline placeholder; not a coding.", "manual_reference": "D",
              "evidence": []}
    return {"protocol_version": PROTOCOL_VERSION, "transcript_id": tid, "status": "ok",
            "ratings": {k: dict(rating) for k in ("cultivating_change_talk", "softening_sustain_talk",
                                                  "partnership", "empathy")},
            "review_notes": ["Offline dry-run output; not a coding."]}


def _behaviors(tid: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    volleys, utterances = [], []
    for m in messages:
        if m["role"] != "assistant":
            continue
        vid = f"v{len(volleys) + 1:02d}"
        volleys.append({"volley_id": vid, "assistant_message_ids": [m["message_id"]],
                        "intervening_facilitative_user_ids": [], "boundary_note": "Offline placeholder."})
        quote = m["content"].strip()
        utterances.append({"utterance_id": f"u{len(utterances) + 1:02d}", "volley_id": vid,
                           "message_id": m["message_id"], "quote": quote, "occurrence_index": 0,
                           "code": "Q" if quote.endswith("?") else "GI",
                           "rationale": "Offline placeholder; not a coding.", "manual_reference": "E.4",
                           "review_note": ""})
    return {"protocol_version": PROTOCOL_VERSION, "transcript_id": tid, "status": "ok",
            "volleys": volleys, "utterances": utterances, "review_notes": ["Offline dry-run output."]}


def respond(messages: list[ChatMessage], tools: Any = None, tool_choice: Any = None,
            config: GenerateConfig | None = None) -> ModelOutput:
    system = _system(messages)
    last = messages[-1].text
    if "PASS 1 GLOBAL RATINGS" in system:
        text = json.dumps(_globals(_dialogue(last)[0]), ensure_ascii=False)
    elif "PASS 2 BEHAVIOR CODING" in system:
        text = json.dumps(_behaviors(*_dialogue(last)), ensure_ascii=False)
    elif system.startswith("Play only the USER"):
        n = json.loads(last)["generated_followup_index"]
        text = f"offline client follow-up {n}. not sure about that."
    else:
        n = sum(m.role == "assistant" for m in messages) + 1
        text = f"Offline target reply {n}. That sounds stressful. What would help most right now?"
    out = ModelOutput.from_content(OFFLINE_MODEL, text)
    out.usage = ModelUsage(input_tokens=sum(len(m.text) // 4 for m in messages), output_tokens=len(text) // 4,
                           total_tokens=0)
    return out


def install() -> None:
    OFFLINE_RESPONDER["fn"] = respond
