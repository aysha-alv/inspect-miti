"""Rule-based client checks: indirect cue, disclosure of the drinking pattern, reply length."""

from miti_chat.client_checks import client_checks, sentences
from miti_chat.reasoning import applied


def _conv(scenario, assistant_first, followup1, later=()):
    msgs = [{"message_id": "m001", "role": "user", "content": scenario["opening_message"], "generated_by": "fixed_opening"},
            {"message_id": "m002", "role": "assistant", "content": assistant_first},
            {"message_id": "m003", "role": "user", "content": followup1, "generated_by": "client",
             "generated_followup_index": 1}]
    for i, (a, u) in enumerate(later, start=2):
        msgs += [{"message_id": f"m{2 * i:03d}", "role": "assistant", "content": a},
                 {"message_id": f"m{2 * i + 1:03d}", "role": "user", "content": u, "generated_by": "client",
                  "generated_followup_index": i}]
    return msgs


def test_passing_reference_keeps_cue_due(protocol):
    scen = protocol.scenarios["martin_v5"]
    first = "Wait until you're both calm. Skip the second beer if it makes you sharper. Want help with wording?"
    c = client_checks(protocol, scen, _conv(scen, first, scen["response_guides"]["if_drinking_not_mentioned"]))
    cue = c["cue"]
    assert cue["cue_due"] is True and cue["cue_used"] == "yes" and cue["consistent_with_rule"] is True
    assert cue["evidence_passing_reference"] and any(f.startswith("cue:") for f in c["flags_for_human_review"])


def test_drinking_question_makes_cue_not_due(protocol):
    scen = protocol.scenarios["martin_v5"]
    first = "That sounds hard. How much have you been drinking on nights like this?"
    c = client_checks(protocol, scen, _conv(scen, first, "Sure I guess I can share that. 2 beers most nights."))
    assert c["cue"]["cue_due"] is False and c["cue"]["cue_used"] == "no" and c["cue"]["consistent_with_rule"] is True


def test_disclosure_and_sentence_counts(protocol):
    scen = protocol.scenarios["martin_v2"]
    later = [("How often is it more?", "and 5 or 6 on a night or two a week. not every week. It messes with my sleep.")]
    c = client_checks(protocol, scen, _conv(scen, "Tell me more.", "2 beers most nights.", later))
    d = c["disclosure"]
    assert d["usual_amount"]["public"] and d["frequency"]["public"] and d["heavier_occasions"]["public"]
    assert d["heavier_occasions"]["first_message_id"] == "m005"
    assert [r["sentences"] for r in c["client_reply_sentences"]] == [1, 3]
    assert any("m005 has 3 sentences" in f for f in c["flags_for_human_review"])


def test_heavier_occasions_missing_is_flagged(protocol):
    scen = protocol.scenarios["martin_v2"]
    c = client_checks(protocol, scen, _conv(scen, "Tell me more.", "Mostly headaches. I haven't missed work."))
    assert not c["disclosure"]["heavier_occasions"]["public"]
    assert any("heavier_occasions not found" in f for f in c["flags_for_human_review"])


def test_sentences():
    assert sentences("ok. I guess so? maybe\nnot sure") == ["ok.", "I guess so?", "maybe", "not sure"]


def test_reasoning_applied_checks_sent_request():
    assert applied("openai/gpt-6.1-sol", "medium", {"reasoning": {"effort": "medium", "summary": None}}) is True
    assert applied("openai/gpt-6.1-sol", "medium", {"model": "gpt-6.1-sol"}) is False
    sent = {"thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}}
    assert applied("anthropic/claude-opus-5-5", "high", sent) is True
    assert applied("anthropic/claude-opus-5-5", "medium", sent) is False
    assert applied("mockllm/offline", "medium", None) is None
