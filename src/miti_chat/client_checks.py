"""Rule-based checks of the simulated client, run when a conversation finishes (no model calls).

For each conversation this records:
- cue: for an indirect opening, whether the scheduled cue was due (the first assistant
  reply did not ask about drinking or address it as a concern) and whether follow-up 1
  used the if_drinking_not_mentioned guide;
- disclosure: whether the usual amount, frequency and heavier occasions became public,
  with message IDs and the supporting sentences;
- reply length: sentence counts for each generated client reply.

Patterns live in config/client_checks.json. Results are screening aids: anything
uncertain is listed under flags_for_human_review. Nothing is regenerated to pass.
"""

from __future__ import annotations

import json
import re
from typing import Any

from rapidfuzz import fuzz

from .config import Protocol

CUE_USED, CUE_UNCERTAIN = 70.0, 45.0
METHOD = ("Keyword and pattern matching on the public messages (config/client_checks.json); "
          "guide similarity with rapidfuzz token_set_ratio. Screening only: confirm by reading.")


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p.strip() for p in parts if re.search(r"\w", p)]


def _has(text: str, terms: list[str]) -> bool:
    low = text.lower()
    return any(re.search(r"\b" + re.escape(t), low) for t in terms)


def _config(protocol: Protocol) -> dict[str, Any]:
    p = protocol.root / "config" / "client_checks.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def cue_check(cfg: dict[str, Any], scenario: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    guide = scenario["response_guides"].get("if_drinking_not_mentioned")
    if scenario["opening_type"] != "indirect" or not guide:
        return {"applies": False, "reason": "direct opening or no if_drinking_not_mentioned guide"}
    first = next((m for m in messages if m["role"] == "assistant"), None)
    followup = next((m for m in messages if m.get("generated_followup_index") == 1), None)
    if first is None or followup is None:
        return {"applies": True, "status": "not_reached", "reason": "conversation ended before follow-up 1"}
    alcohol, concern = cfg["alcohol_terms"], cfg["concern_terms"]
    sents = sentences(first["content"])
    asked = [s for s in sents if s.endswith("?") and _has(s, alcohol)]
    addressed = [s for s in sents if _has(s, alcohol) and _has(s, concern) and s not in asked]
    passing = [s for s in sents if _has(s, alcohol) and s not in asked and s not in addressed]
    due = not (asked or addressed)
    score = round(fuzz.token_set_ratio(guide.lower(), followup["content"].lower()), 1)
    used = "yes" if score >= CUE_USED else ("uncertain" if score >= CUE_UNCERTAIN else "no")
    uncertain_reasons = []
    if asked or addressed or passing:
        uncertain_reasons.append("first assistant reply mentions alcohol; whether it asked about or "
                                 "addressed drinking as a concern is a judgment")
    if used == "uncertain":
        uncertain_reasons.append(f"follow-up 1 is only partly similar to the guide (score {score})")
    adherent = (due and used == "yes") or (not due and used == "no")
    return {
        "applies": True,
        "first_assistant_message_id": first["message_id"],
        "followup_1_message_id": followup["message_id"],
        "cue_due": due,
        "evidence_asked_about_drinking": asked,
        "evidence_addressed_as_concern": addressed,
        "evidence_passing_reference": passing,
        "cue_used": used,
        "guide_similarity": score,
        "guide": guide,
        "followup_1_text": followup["content"],
        "consistent_with_rule": adherent if used != "uncertain" else None,
        "uncertain": uncertain_reasons,
    }


def disclosure_check(patterns: dict[str, list[str]], messages: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for element, regexes in patterns.items():
        hits = []
        for m in messages:
            if m["role"] != "user":
                continue
            for s in sentences(m["content"]):
                if any(re.search(rx, s, flags=re.IGNORECASE) for rx in regexes):
                    hits.append({"message_id": m["message_id"], "text": s,
                                 "source": m.get("generated_by", "client")})
        generated = [h for h in hits if h["source"] == "client"]
        out[element] = {"public": bool(hits), "first_message_id": hits[0]["message_id"] if hits else None,
                        "in_client_replies": bool(generated),
                        "first_client_reply_id": generated[0]["message_id"] if generated else None,
                        "evidence": hits}
    return out


def client_checks(protocol: Protocol, scenario: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    cfg = _config(protocol)
    persona = (cfg.get("personas") or {}).get(scenario["persona_family_id"])
    if not cfg or persona is None:
        return {"method": METHOD, "status": "not_configured",
                "flags_for_human_review": [f"no client_checks entry for {scenario['persona_family_id']!r}"]}
    flags: list[str] = []

    cue = cue_check(cfg, scenario, messages)
    if cue.get("applies") and cue.get("status") != "not_reached":
        flags += [f"cue: {u}" for u in cue["uncertain"]]
        if cue["consistent_with_rule"] is False:
            flags.append(f"cue: due={cue['cue_due']} but used={cue['cue_used']}")

    disclosure = disclosure_check(persona["disclosure"], messages)
    for element, res in disclosure.items():
        if res["in_client_replies"]:
            hit = next(h for h in res["evidence"] if h["source"] == "client")
            flags.append(f"disclosure: confirm {element} in Martin's reply {hit['message_id']} (\"{hit['text'][:80]}\")")
        elif res["public"]:
            flags.append(f"disclosure: {element} appears only in the fixed opening "
                         f"({res['first_message_id']}), not in Martin's generated replies")
        else:
            flags.append(f"disclosure: {element} not found in client messages; check by reading")

    limit = persona.get("max_sentences_per_reply")
    lengths = [{"message_id": m["message_id"], "sentences": len(sentences(m["content"])),
                "words": len(m["content"].split())}
               for m in messages if m["role"] == "user" and m.get("generated_by") == "client"]
    for row in lengths:
        if limit and row["sentences"] > limit:
            flags.append(f"length: {row['message_id']} has {row['sentences']} sentences (style guide: up to {limit})")

    return {"method": METHOD, "status": "checked", "cue": cue, "disclosure": disclosure,
            "client_reply_sentences": lengths, "max_sentences_per_reply": limit,
            "flags_for_human_review": flags}
