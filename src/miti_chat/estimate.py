"""Conservative cost estimate for a batch, computed offline (no API calls).

Worst case for every request: each earlier reply is assumed to have used its
full token limit, so histories grow as fast as they can; every judge call
carries the full manual; every request is retried once. Output limits include
billed reasoning tokens. Inputs are estimated with tokens.py.
"""

from __future__ import annotations

import json
from typing import Any

from . import tokens
from .budget import attempt_cost, is_free
from .coding import judge_system
from .config import Batch, Protocol
from .conversation import client_system_prompt

JSON_OVERHEAD_PER_MESSAGE = 20


def _proposed(protocol: Protocol) -> dict[str, Any]:
    p = protocol.root / "config" / "proposed_settings.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def settings_for_estimate(protocol: Protocol) -> tuple[dict[str, str | None], dict[str, int | None], list[str]]:
    """Model IDs and token limits from the run plan, falling back to config/proposed_settings.json."""
    proposed = _proposed(protocol)
    notes: list[str] = []
    models = dict(protocol.model_ids)
    for role, mid in (proposed.get("model_ids") or {}).items():
        if not models.get(role) and mid:
            models[role] = mid
            notes.append(f"model_ids.{role}: proposed {mid} (not yet in run_plan.json)")
    limits = dict(protocol.generation["response_token_limits"])
    for key, val in (proposed.get("response_token_limits") or {}).items():
        if not limits.get(key) and val:
            limits[key] = val
            notes.append(f"response_token_limits.{key}: proposed {val} (not yet in run_plan.json)")
    return models, limits, notes


def estimate_batch(protocol: Protocol, batch: Batch) -> dict[str, Any]:
    models, limits, notes = settings_for_estimate(protocol)
    R = int(protocol.generation["max_assistant_replies"])
    A, U = limits.get("target"), limits.get("client")
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    if not A or not U or not limits.get("judge_globals") or not limits.get("judge_behaviors"):
        missing.append("response token limits")

    def add(role: str, model: str | None, purpose: str, n: int, inp: int, out: int) -> None:
        priced = model is not None and (is_free(model) or model in protocol.pricing)
        cost = attempt_cost(protocol, model, inp, out) * n if priced and model else None
        if model is None:
            missing.append(f"model for {role}")
        elif not priced:
            missing.append(f"price for {model}")
        rows.append({"role": role, "model": model, "purpose": purpose, "requests": n,
                     "input_tokens_each": inp, "output_tokens_each": out, "cost_usd": cost})

    if A and U:
        for item in batch.items:
            scen = protocol.scenarios[item.scenario_id]
            tm, cm = models.get(item.target_role), models.get(batch.client_role)
            for k in range(1, R + 1):
                base = tokens.estimate([protocol.target_system, scen["opening_message"]], tm or "")
                add(item.target_role, tm, f"{item.scenario_id} target reply {k}", 1,
                    base + (k - 1) * (A + U + 2 * tokens.PER_MESSAGE_OVERHEAD), A)
            csys = client_system_prompt(protocol, scen)
            for i in range(1, R):
                base = tokens.estimate([csys, scen["opening_message"]], cm or "")
                hist = i * A + (i - 1) * U + (2 * i) * JSON_OVERHEAD_PER_MESSAGE
                add(batch.client_role, cm, f"{item.scenario_id} client follow-up {i}", 1, base + hist, U)
        jm = models.get(batch.judge_role)
        dialogue_worst = R * A + (R - 1) * U + 2 * R * JSON_OVERHEAD_PER_MESSAGE
        for pass_name, key in (("globals", "judge_globals"), ("behaviors", "judge_behaviors")):
            sys_tokens = tokens.estimate([judge_system(protocol, pass_name),
                                          json.dumps(protocol.schema(pass_name), indent=2)], jm or "")
            add(batch.judge_role, jm, f"judge {pass_name} pass", len(batch.items),
                sys_tokens + dialogue_worst + 200, limits[key] or 0)

    attempts_per_request = int(protocol.execution["transport_retries_max"]) + 1
    nominal = sum(r["cost_usd"] for r in rows) if rows and all(r["cost_usd"] is not None for r in rows) else None
    return {
        "batch_id": batch.batch_id,
        "tokenizer": tokens.tokenizer_name(),
        "assumptions": [
            "every earlier reply used its full token limit (worst-case history growth)",
            "the full MITI manual is in every judge call",
            "output limits include billed reasoning tokens",
            f"worst case = every request uses all {attempts_per_request} permitted attempts",
        ],
        "settings_notes": notes,
        "missing": sorted(set(missing)),
        "nominal_requests": sum(r["requests"] for r in rows),
        "max_attempts_with_one_retry_each": attempts_per_request * sum(r["requests"] for r in rows),
        "nominal_cost_usd": nominal,
        "worst_case_cost_usd_with_retries": None if nominal is None else attempts_per_request * nominal,
        "rows": rows,
    }


def format_estimate(est: dict[str, Any]) -> str:
    lines = [f"Cost estimate for {est['batch_id']} (tokenizer: {est['tokenizer']})", ""]
    for n in est["settings_notes"]:
        lines.append(f"  note: {n}")
    lines += ["", f"{'role':<20}{'model':<34}{'req':>4}{'in tok':>9}{'out tok':>9}{'USD':>10}  purpose"]
    for r in est["rows"]:
        cost = "-" if r["cost_usd"] is None else f"{r['cost_usd']:.4f}"
        lines.append(f"{r['role']:<20}{str(r['model']):<34}{r['requests']:>4}{r['input_tokens_each']:>9}"
                     f"{r['output_tokens_each']:>9}{cost:>10}  {r['purpose']}")
    lines.append("")
    lines.append(f"nominal requests: {est['nominal_requests']}   "
                 f"max attempts (one retry each): {est['max_attempts_with_one_retry_each']}")
    if est["nominal_cost_usd"] is None:
        lines.append("cost: incomplete, missing " + ", ".join(est["missing"]))
    else:
        lines.append(f"nominal worst-case cost: ${est['nominal_cost_usd']:.2f}   "
                     f"with every request retried once: ${est['worst_case_cost_usd_with_retries']:.2f}")
    return "\n".join(lines)
