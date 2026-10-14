import copy

import pytest

from miti_chat.config import Protocol


@pytest.fixture
def protocol() -> Protocol:
    p = Protocol()
    p.plan = copy.deepcopy(p.plan)  # tests may edit the plan in memory; files are never changed
    return p


@pytest.fixture
def fixture(protocol):
    return protocol.json("assets/offline_fixture.json")


def live_plan(protocol: Protocol, models: dict[str, str]) -> Protocol:
    """In-memory copy of the plan with every live-access condition satisfied."""
    plan = protocol.plan
    plan.update(mode="live", allow_paid_calls=True, approved_batch_id="technical_check_01",
                approved_max_cost_usd=5.0, approved_max_total_requests=36)
    plan["model_ids"].update(models)
    plan["generation"]["response_token_limits"].update(target=1000, client=200, judge_globals=2000,
                                                       judge_behaviors=4000)
    protocol.__dict__["pricing"] = {m: {"input_usd_per_mtok": 3.0, "output_usd_per_mtok": 15.0}
                                    for m in models.values()}
    return protocol
