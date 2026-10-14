"""Live-access gate, request cap and dollar cap. No network: refused attempts never reach a provider,
and allowed attempts are recorded but not sent."""

import pytest

from conftest import live_plan
from miti_chat.budget import AccessRefused, Ledger, live_gate_problems

MODELS = {"target_openai": "openai/gpt-x", "target_anthropic": "anthropic/claude-x",
          "client_provisional": "anthropic/claude-x", "judge_provisional": "anthropic/claude-x"}


def _reserve(ledger, model="anthropic/claude-x", inp=1000, out=1000):
    return ledger.reserve(role="target", model=model, purpose="t", request_id="r", attempt_number=1,
                          est_input_tokens=inp, max_output_tokens=out, settings={})


def test_offline_plan_refuses_paid_calls(protocol, tmp_path):
    batch = protocol.batch("technical_check_01")
    assert live_gate_problems(protocol, batch)
    ledger = Ledger(tmp_path / "ledger.json", protocol, batch)
    with pytest.raises(AccessRefused) as ex:
        _reserve(ledger)
    assert ex.value.code == "live_access_refused"
    assert ledger.attempts == []


def test_offline_models_always_allowed(protocol, tmp_path):
    ledger = Ledger(tmp_path / "ledger.json", protocol, protocol.batch("technical_check_01"))
    att = _reserve(ledger, model="mockllm/offline")
    assert att["reserved_cost_usd"] == 0.0 and ledger.paid_attempts() == 0


def test_live_plan_passes_gate(protocol):
    live_plan(protocol, MODELS)
    assert live_gate_problems(protocol, protocol.batch("technical_check_01")) == []


@pytest.mark.parametrize("change, expect", [
    ({"approved_batch_id": "other_batch"}, "does not match"),
    ({"allow_paid_calls": "true"}, "allow_paid_calls"),
    ({"approved_max_cost_usd": float("inf")}, "approved_max_cost_usd"),
    ({"approved_max_total_requests": 36.0}, "approved_max_total_requests"),
])
def test_each_gate_condition(protocol, change, expect):
    live_plan(protocol, MODELS)
    protocol.plan.update(change)
    assert any(expect in p for p in live_gate_problems(protocol, protocol.batch("technical_check_01")))


def test_missing_model_id_and_price(protocol):
    live_plan(protocol, MODELS)
    protocol.plan["model_ids"]["client_provisional"] = None
    protocol.__dict__["pricing"].pop("openai/gpt-x")
    problems = live_gate_problems(protocol, protocol.batch("technical_check_01"))
    assert "model_ids.client_provisional is empty" in problems
    assert any("openai/gpt-x: no price" in p for p in problems)


def test_unverified_provider_refused(protocol):
    live_plan(protocol, {**MODELS, "judge_provisional": "google/gemini-x"})
    assert any("SDK retry" in p for p in live_gate_problems(protocol, protocol.batch("technical_check_01")))


def test_request_cap_counts_every_attempt(protocol, tmp_path):
    live_plan(protocol, MODELS)
    protocol.plan["approved_max_total_requests"] = 2
    ledger = Ledger(tmp_path / "ledger.json", protocol, protocol.batch("technical_check_01"))
    a = _reserve(ledger)
    ledger.finish(a, outcome="error", cost_usd=0.0)   # failed attempts still count
    _reserve(ledger)
    with pytest.raises(AccessRefused) as ex:
        _reserve(ledger)
    assert ex.value.code == "request_limit_reached"
    # the cap persists across commands: a new Ledger on the same file sees both attempts
    assert Ledger(tmp_path / "ledger.json", protocol, protocol.batch("technical_check_01")).paid_attempts() == 2


def test_cost_cap_uses_reservations(protocol, tmp_path):
    live_plan(protocol, MODELS)
    protocol.plan["approved_max_cost_usd"] = 0.05
    ledger = Ledger(tmp_path / "ledger.json", protocol, protocol.batch("technical_check_01"))
    # 1000 in * $3/M + 2000 out * $15/M = $0.033 reserved; an uncertain (timed-out) attempt keeps it
    a = _reserve(ledger, inp=1000, out=2000)
    ledger.finish(a, outcome="error")
    with pytest.raises(AccessRefused) as ex:
        _reserve(ledger, inp=1000, out=2000)
    assert ex.value.code == "cost_limit_reached"
