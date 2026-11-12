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


DEFAULT_TYPICAL = {
    "_note": "Typical-case sizes, from technical_check_01 where available. Not limits.",
    "target_visible_tokens": 800,
    "target_output_tokens": 2000,
    "client_visible_tokens": 100,
    "client_output_tokens": 800,
    "judge_globals_output_tokens": 6000,
    "judge_behaviors_output_tokens_per_assistant_word": 17,
    "assistant_words_per_reply": 350,
}


def _typical(protocol: Protocol) -> dict[str, Any]:
    return {**DEFAULT_TYPICAL, **(_proposed(protocol).get("typical_tokens") or {})}


def estimate_batch(protocol: Protocol, batch: Batch) -> dict[str, Any]:
    """Worst-case and typical-case cost for a batch.

    Worst case: every earlier reply used its full token limit, so histories grow as fast as
    they can, and every output reaches its limit. Typical case: visible replies and outputs
    of the sizes in config/proposed_settings.json "typical_tokens" (defaults above).
    """
    models, limits, notes = settings_for_estimate(protocol)
    typical = _typical(protocol)
    R, F = batch.max_assistant_replies, batch.max_generated_client_followups
    missing: list[str] = []
    if not all(limits.get(k) for k in ("target", "client", "judge_globals", "judge_behaviors")):
        missing.append("response token limits")

    def rows_for(hist_a: int, hist_u: int, out: dict[str, int]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        def add(role: str, model: str | None, purpose: str, n: int, inp: int, o: int) -> None:
            priced = model is not None and (is_free(model) or model in protocol.pricing)
            if model is None:
                missing.append(f"model for {role}")
            elif not priced:
                missing.append(f"price for {model}")
            rows.append({"role": role, "model": model, "purpose": purpose, "requests": n,
                         "input_tokens_each": inp, "output_tokens_each": o,
                         "cost_usd": attempt_cost(protocol, model, inp, o) * n if priced and model else None})

        for item in batch.items:
            scen = protocol.scenarios[item.scenario_id]
            tm, cm = models.get(item.target_role), models.get(batch.client_role)
            base_t = tokens.estimate([protocol.target_system, scen["opening_message"]], tm or "")
            for k in range(1, R + 1):
                add(item.target_role, tm, f"{item.scenario_id} target reply {k}", 1,
                    base_t + (k - 1) * (hist_a + hist_u + 2 * tokens.PER_MESSAGE_OVERHEAD), out["target"])
            base_c = tokens.estimate([client_system_prompt(protocol, scen), scen["opening_message"]], cm or "")
            for i in range(1, F + 1):
                hist = i * hist_a + (i - 1) * hist_u + (2 * i) * JSON_OVERHEAD_PER_MESSAGE
                add(batch.client_role, cm, f"{item.scenario_id} client follow-up {i}", 1, base_c + hist, out["client"])
        jm = models.get(batch.judge_role)
        dialogue = R * hist_a + R * hist_u + 2 * R * JSON_OVERHEAD_PER_MESSAGE
        for pass_name in ("globals", "behaviors"):
            sys_tokens = tokens.estimate([judge_system(protocol, pass_name),
                                          json.dumps(protocol.schema(pass_name), indent=2)], jm or "")
            add(batch.judge_role, jm, f"judge {pass_name} pass", len(batch.items),
                sys_tokens + dialogue + 200, out[f"judge_{pass_name}"])
        return rows

    def total(rows: list[dict[str, Any]]) -> float | None:
        return sum(r["cost_usd"] for r in rows) if rows and all(r["cost_usd"] is not None for r in rows) else None

    worst_rows: list[dict[str, Any]] = []
    typical_rows: list[dict[str, Any]] = []
    if not missing:
        A, U = int(limits["target"]), int(limits["client"])
        worst_rows = rows_for(A, U, {"target": A, "client": U, "judge_globals": int(limits["judge_globals"]),
                                     "judge_behaviors": int(limits["judge_behaviors"])})
        behaviors_typical = min(int(limits["judge_behaviors"]),
                                int(typical["judge_behaviors_output_tokens_per_assistant_word"]
                                    * typical["assistant_words_per_reply"] * R))
        typical_rows = rows_for(int(typical["target_visible_tokens"]), int(typical["client_visible_tokens"]),
                                {"target": min(A, int(typical["target_output_tokens"])),
                                 "client": min(U, int(typical["client_output_tokens"])),
                                 "judge_globals": min(int(limits["judge_globals"]),
                                                      int(typical["judge_globals_output_tokens"])),
                                 "judge_behaviors": behaviors_typical})

    attempts_per_request = int(protocol.execution["transport_retries_max"]) + 1
    nominal = total(worst_rows)
    n_requests = sum(r["requests"] for r in worst_rows) if worst_rows else len(batch.items) * (R + F + 2)
    return {
        "batch_id": batch.batch_id,
        "tokenizer": tokens.tokenizer_name(),
        "assumptions": [
            "worst case: every earlier reply used its full token limit and every output reaches its limit",
            "typical case: sizes in typical_tokens (" + ", ".join(f"{k}={v}" for k, v in typical.items()
                                                       if not k.startswith("_")) + ")",
            "the full MITI manual is in every judge call",
            "output limits include billed reasoning tokens",
            f"worst case with retries = every request uses all {attempts_per_request} permitted attempts",
        ],
        "settings_notes": notes,
        "missing": sorted(set(missing)),
        "nominal_requests": n_requests,
        "max_attempts_with_one_retry_each": attempts_per_request * n_requests,
        "typical_cost_usd": total(typical_rows),
        "nominal_cost_usd": nominal,
        "worst_case_cost_usd_with_retries": None if nominal is None else attempts_per_request * nominal,
        "rows": worst_rows,
        "typical_rows": typical_rows,
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
        lines.append(f"typical cost: ${est['typical_cost_usd']:.2f}   "
                     f"worst case (every output at its limit): ${est['nominal_cost_usd']:.2f}   "
                     f"worst case with every request retried: ${est['worst_case_cost_usd_with_retries']:.2f}")
    return "\n".join(lines)
