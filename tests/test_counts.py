"""Counting rules against the supplied aggregation cases and offline fixture."""

import pytest

from miti_chat import counts as C
from miti_chat.config import Protocol

CASES = Protocol().json("assets/aggregation_cases.json")["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["case"] for c in CASES])
def test_aggregation_case(case):
    r = C.count_volleys(case["volleys"])
    if "expected_nonzero_counts" in case:
        assert {k: v for k, v in r.counts.items() if v} == case["expected_nonzero_counts"]
    if "expected_summary" in case:
        s = C.behavior_summaries(r.counts)
        for k, v in case["expected_summary"].items():
            assert s[k]["value"] == v
            if v is None:
                assert s[k]["reason"]
    if "finalized_counts_allowed" in case:
        assert r.finalized == case["finalized_counts_allowed"]
        assert r.review_triggers and r.review_triggers[0]["trigger"] == C.MIXED_PERSUASION


def test_fixture_counts(fixture):
    labels = {}
    for u in fixture["behavior_output"]["utterances"]:
        labels.setdefault(u["volley_id"], []).append(u["code"])
    r = C.count_volleys(list(labels.values()))
    assert r.counts == fixture["expected_counts"]
    s = {k: v["value"] for k, v in C.behavior_summaries(r.counts).items()}
    assert s == fixture["expected_summary"]


def test_global_summaries():
    s = C.global_summaries({"cultivating_change_talk": 2, "softening_sustain_talk": 3, "partnership": 4, "empathy": 5})
    assert s["technical_global"]["value"] == 2.5 and s["relational_global"]["value"] == 4.5


def test_unknown_code_rejected():
    with pytest.raises(ValueError):
        C.count_volleys([["Q", "OPEN_QUESTION"]])
