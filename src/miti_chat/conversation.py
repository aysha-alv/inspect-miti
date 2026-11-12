"""Conversation generation as an Inspect task (one sample per batch item).

Sequence, from the study plan and README:
  1. The persona version's fixed opening is the first user message (no model call).
  2. The target replies. It sees assets/target_system.txt as a system message
     plus the public user/assistant history in normal API roles; nothing else.
  3. The client writes the next user message in a fresh two-message request:
     system = client instructions + the selected private persona,
     user   = JSON {generated_followup_index, messages} with the public history.
  4. Repeat until the reply limit (default: stop right after the 4th assistant reply).

Every message is saved as it is produced. Early stops (failed request, empty or
truncated response, request/cost limit, cancellation) keep the partial dialogue
and the reason. Only conversations that reach the reply limit are judged.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from inspect_ai import Task
from inspect_ai.dataset import Sample
from inspect_ai.model import (
    ChatMessage,
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageUser,
)
from inspect_ai.solver import Generate, Solver, TaskState, solver

from . import transcripts
from .budget import Ledger, now
from .calls import CallResult, call_model
from .client_checks import client_checks
from .config import Batch, Protocol
from .runs import RunPaths, write_json

READINESS_LABELS = {
    "fine_with_it": "fine with it",
    "ambivalent": "goes back and forth",
    "wants_to_cut_back": "wants to cut back",
}


def client_system_prompt(protocol: Protocol, scenario: dict[str, Any]) -> str:
    """Client instructions plus the selected version's private persona (client only)."""
    profile = "\n".join(f"{k}: {v}" for k, v in scenario["private_profile"].items())
    guides = "\n".join(
        f"{k}: {v if v is not None else '(none supplied for this version)'}"
        for k, v in scenario["response_guides"].items()
    )
    readiness = READINESS_LABELS.get(scenario["readiness"], scenario["readiness"])
    return (
        f"{protocol.client_system}\n\n"
        "SELECTED PERSONA VERSION\n"
        f"Readiness: {readiness}\n"
        f"Opening type: {scenario['opening_type']}\n\n"
        f"PRIVATE PROFILE\n{profile}\n\n"
        f"RESPONSE GUIDES\n{guides}"
    )


def client_user_payload(followup_index: int, messages: list[dict[str, str]]) -> str:
    return json.dumps({"generated_followup_index": followup_index, "messages": messages},
                      ensure_ascii=False, indent=2)


def target_view(protocol: Protocol, messages: list[dict[str, str]]) -> list[ChatMessage]:
    out: list[ChatMessage] = [ChatMessageSystem(content=protocol.target_system)]
    for m in messages:
        cls = ChatMessageUser if m["role"] == "user" else ChatMessageAssistant
        out.append(cls(content=m["content"]))
    return out


def resolve_models(protocol: Protocol, batch: Batch, overrides: dict[str, str] | None = None) -> dict[str, str | None]:
    """Model ID per role: run plan model_ids, optionally overridden (offline dry run only)."""
    models = {role: protocol.model_ids.get(role) for role in batch.roles}
    models.update(overrides or {})
    return models


class Conversation:
    """Private record of one conversation, saved after every message."""

    def __init__(self, paths: RunPaths, protocol: Protocol, batch: Batch, scenario: dict[str, Any],
                 target_role: str, models: dict[str, str | None], generation_run_id: str) -> None:
        self.paths = paths
        self.protocol = protocol
        self.scenario = scenario
        self.transcript_id = transcripts.new_transcript_id()
        self.messages: list[dict[str, str]] = []
        self.record: dict[str, Any] = {
            "transcript_id": self.transcript_id,
            "protocol_version": protocol.version,
            "batch_id": batch.batch_id,
            "generation_run_id": generation_run_id,
            "scenario_id": scenario["scenario_id"],
            "persona_family_id": scenario["persona_family_id"],
            "opening_type": scenario["opening_type"],
            "readiness": scenario["readiness"],
            "target_role": target_role,
            "target_model": models[target_role],
            "client_role": batch.client_role,
            "client_model": models[batch.client_role],
            "max_assistant_replies": batch.max_assistant_replies,
            "max_generated_client_followups": batch.max_generated_client_followups,
            "reasoning_effort": {target_role: protocol.reasoning_effort(target_role),
                                 batch.client_role: protocol.reasoning_effort(batch.client_role)},
            "started_at": now(),
            "finished_at": None,
            "status": "in_progress",
            "stop_reason": None,
            "stop_detail": None,
            "messages": [],          # public messages + provenance (request id, attempts, usage)
            "unappended_response": None,
        }

    def add(self, role: str, content: str, source: dict[str, Any]) -> None:
        mid = transcripts.message_id(len(self.messages) + 1)
        self.messages.append({"message_id": mid, "role": role, "content": content})
        self.record["messages"].append({"message_id": mid, "role": role, "content": content, **source})
        self.save()

    def save(self) -> None:
        write_json(self.paths.conversations / f"{self.transcript_id}.json", self.record)
        if self.messages:
            transcripts.save(self.paths.transcripts, transcripts.canonical(self.transcript_id, self.messages))

    def finish(self, status: str, stop_reason: str, detail: str | None = None,
               unappended: CallResult | None = None) -> None:
        r = self.record
        r.update(status=status, stop_reason=stop_reason, stop_detail=detail, finished_at=now())
        if unappended is not None:
            r["unappended_response"] = {"text": unappended.text, "stop_reason": unappended.stop_reason,
                                        "request_id": unappended.request_id,
                                        "attempt_ids": unappended.attempt_ids}
        canonical = transcripts.canonical(self.transcript_id, self.messages)
        r["transcript_sha256"] = transcripts.content_hash(canonical)
        r["lengths"] = transcripts.length_record(self.messages)
        r["provider_token_usage"] = _usage_by_role(r["messages"])
        # rule-based client checks (no model calls): cue, disclosure, reply length
        try:
            r["client_checks"] = client_checks(self.protocol, self.scenario, r["messages"])
        except Exception as ex:  # a screening aid must never lose a paid conversation
            r["client_checks"] = {"status": "error", "error": f"{type(ex).__name__}: {ex}",
                                  "flags_for_human_review": ["client checks failed to run; review by reading"]}
        self.save()
        self.paths.append_key({**r, "status": status})


def _usage_by_role(messages: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for m in messages:
        usage = m.get("usage") or {}
        role = m.get("generated_by")
        if role not in ("target", "client"):
            continue
        agg = out.setdefault(role, {})
        for k, v in usage.items():
            if isinstance(v, int):
                agg[k] = agg.get(k, 0) + v
    return out


def _source(result: CallResult, generated_by: str) -> dict[str, Any]:
    return {"generated_by": generated_by, "request_id": result.request_id, "attempt_ids": result.attempt_ids,
            "served_model": result.served_model, "stop_reason": result.stop_reason, "usage": result.usage}


def _check(conv: Conversation, result: CallResult, who: str) -> bool:
    """True if the response can be appended; otherwise finish the conversation as partial."""
    if not result.ok:
        conv.finish("partial", f"{who}_{result.failure}", result.error,
                    unappended=result if result.text else None)
    elif result.empty:
        conv.finish("partial", f"{who}_empty_response", unappended=result)
    elif result.truncated:
        conv.finish("partial", f"{who}_truncated_response", f"stop_reason={result.stop_reason}",
                    unappended=result)
    else:
        return True
    return False


@solver
def conversation_solver(protocol: Protocol, batch: Batch, paths: RunPaths, models: dict[str, str | None],
                        generation_run_id: str) -> Solver:
    max_replies = batch.max_assistant_replies
    max_followups = batch.max_generated_client_followups
    ledger = Ledger(paths.ledger, protocol, batch)

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        scenario = protocol.scenarios[state.metadata["scenario_id"]]
        target_role = state.metadata["target_role"]
        target_model, client_model = models[target_role], models[batch.client_role]
        assert target_model and client_model
        client_system = client_system_prompt(protocol, scenario)
        conv = Conversation(paths, protocol, batch, scenario, target_role, models, generation_run_id)
        state.metadata["transcript_id"] = conv.transcript_id
        conv.add("user", scenario["opening_message"], {"generated_by": "fixed_opening"})

        try:
            while True:
                result = await call_model(ledger=ledger, role="target", model_id=target_model,
                                          messages=target_view(protocol, conv.messages),
                                          max_tokens=protocol.token_limit("target"), purpose="target_reply",
                                          reasoning_effort=protocol.reasoning_effort(target_role))
                if not _check(conv, result, "target"):
                    break
                conv.add("assistant", result.text, _source(result, "target"))
                replies = sum(m["role"] == "assistant" for m in conv.messages)
                if replies >= max_replies:
                    conv.finish("completed", "reply_limit_reached")
                    break
                followup_index = sum(m["role"] == "user" for m in conv.messages)  # opening is user #1
                if followup_index > max_followups:
                    conv.finish("partial", "followup_limit_reached")
                    break
                result = await call_model(
                    ledger=ledger, role="client", model_id=client_model,
                    messages=[ChatMessageSystem(content=client_system),
                              ChatMessageUser(content=client_user_payload(followup_index, conv.messages))],
                    max_tokens=protocol.token_limit("client"), purpose=f"client_followup_{followup_index}",
                    reasoning_effort=protocol.reasoning_effort(batch.client_role))
                if not _check(conv, result, "client"):
                    break
                conv.add("user", result.text, {**_source(result, "client"), "generated_followup_index": followup_index})
        except (asyncio.CancelledError, KeyboardInterrupt):
            conv.finish("partial", "operator_cancelled")
            raise
        except Exception as ex:
            conv.finish("partial", "pipeline_error", f"{type(ex).__name__}: {ex}")
            raise

        state.messages = target_view(protocol, conv.messages)
        state.metadata.update(status=conv.record["status"], stop_reason=conv.record["stop_reason"])
        return state

    return solve


def conversation_task(protocol: Protocol, batch: Batch, paths: RunPaths, models: dict[str, str | None],
                      generation_run_id: str) -> Task:
    samples = [
        Sample(id=f"{n:02d}_{item.scenario_id}_{item.target_role}",
               input=protocol.scenarios[item.scenario_id]["opening_message"],
               metadata={"scenario_id": item.scenario_id, "target_role": item.target_role,
                         "target_model": models[item.target_role], "client_model": models[batch.client_role]})
        for n, item in enumerate(batch.items, start=1)
    ]
    return Task(
        name="miti_conversations",
        dataset=samples,
        solver=conversation_solver(protocol, batch, paths, models, generation_run_id),
        metadata={"batch_id": batch.batch_id, "generation_run_id": generation_run_id,
                  "protocol_version": protocol.version},
    )
