import copy

import pytest

from miti_chat.config import Protocol

# Real model IDs so the reasoning-support check can run (it makes no network call).
LIVE_MODELS = {"target_openai": "openai/gpt-6.1-sol", "target_anthropic": "anthropic/claude-opus-5-5",
               "client_provisional": "anthropic/claude-opus-5-5", "judge_provisional": "anthropic/claude-opus-5-5"}


@pytest.fixture
def protocol() -> Protocol:
    """The repository protocol with an in-memory plan forced to offline mode.

    Tests never depend on whether assets/run_plan.json is currently set up for a live
    batch, and they never change the file.
    """
    p = Protocol()
    p.plan = copy.deepcopy(p.plan)
    p.plan.update(mode="offline", allow_paid_calls=False, approved_batch_id=None,
                  approved_max_cost_usd=None, approved_max_total_requests=None)
    p.plan["execution"]["retry_wait_seconds"] = 0
    return p


@pytest.fixture
def fixture(protocol):
    return protocol.json("assets/offline_fixture.json")


@pytest.fixture(autouse=True)
def dummy_api_keys(monkeypatch):
    """Provider clients need a key to be constructed; nothing in the tests sends a request."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")


def live_plan(protocol: Protocol, models: dict[str, str] | None = None,
              batch_id: str = "technical_check_01") -> Protocol:
    """In-memory plan with every live-access condition satisfied for one batch."""
    models = models or LIVE_MODELS
    plan = protocol.plan
    plan.update(mode="live", allow_paid_calls=True, approved_batch_id=batch_id,
                approved_max_cost_usd=5.0, approved_max_total_requests=36)
    plan["model_ids"].update(models)
    plan["generation"]["response_token_limits"].update(target=1000, client=200, judge_globals=2000,
                                                       judge_behaviors=4000)
    plan["generation"]["reasoning_effort"] = {"target_openai": "medium", "target_anthropic": "medium",
                                              "client_provisional": "medium", "judge_provisional": "high"}
    protocol.__dict__["pricing"] = {m: {"input_usd_per_mtok": 3.0, "output_usd_per_mtok": 15.0}
                                    for m in models.values()}
    return protocol
