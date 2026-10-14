"""End-to-end generate + score with the offline model (no API calls)."""

import json

import pytest
from inspect_ai.model import ModelOutput, ModelUsage

from miti_chat import offline, transcripts
from miti_chat.calls import OFFLINE_RESPONDER
from miti_chat.cli import run_generation, run_scoring
from miti_chat.conversation import client_system_prompt, client_user_payload, target_view
from miti_chat.runs import RunPaths

M = offline.OFFLINE_MODEL


@pytest.fixture(autouse=True)
def offline_models():
    offline.install()
    yield
    offline.install()


def _overrides(batch):
    return {role: M for role in batch.roles}


def test_generate_and_score(protocol, tmp_path):
    batch = protocol.batch("technical_check_01")
    paths = RunPaths(tmp_path / batch.batch_id)
    run_generation(protocol, batch, paths, _overrides(batch), display="none")

    records = paths.conversation_records()
    assert len(records) == 2
    for rec in records:
        assert rec["status"] == "completed" and rec["stop_reason"] == "reply_limit_reached"
        t = transcripts.load(paths.transcripts / f"{rec['transcript_id']}.json")
        assert [m["role"] for m in t["messages"]] == ["user", "assistant"] * 4
        assert [m["message_id"] for m in t["messages"]] == [f"m{i:03d}" for i in range(1, 9)]
        assert t["messages"][0]["content"] == protocol.scenarios[rec["scenario_id"]]["opening_message"]
        # blinded files carry no persona or model identity
        blob = (paths.transcripts / f"{rec['transcript_id']}.json").read_text()
        assert "martin" not in blob and "mockllm" not in blob and rec["scenario_id"] not in blob
        assert rec["lengths"]["assistant_reply_count"] == 4
        assert [m.get("generated_followup_index") for m in rec["messages"] if m["role"] == "user"] == [None, 1, 2, 3]

    score_id = run_scoring(protocol, batch, paths, [M], display="none")
    summary = json.loads((paths.coding / score_id / "batch_summary.json").read_text())
    assert summary["automated_checks"]["counts"] == {"valid": 4}
    assert summary["technical_check_complete"] is True
    tid = records[0]["transcript_id"]
    out = paths.coding / score_id / "mockllm_offline" / tid
    counts = json.loads((out / "counts.json").read_text())
    assert counts["counts_finalized"] and counts["behavior_counts"]["Q"] == 4
    assert (out / "thought_units.tsv").read_text().count("\n") == 5

    # 18 nominal requests: 8 target, 6 client, 4 judge
    ledger = json.loads(paths.ledger.read_text())["attempts"]
    assert sorted({a["role"] for a in ledger}) == ["client", "judge", "target"]
    assert [a["role"] for a in ledger].count("target") == 8 and len(ledger) == 18

    # re-scoring makes no client/target calls and keeps the earlier run
    second = run_scoring(protocol, batch, paths, [M], transcript_ids=[tid], display="none", supersedes_run=score_id)
    assert second != score_id and (paths.coding / score_id).is_dir()
    rec2 = json.loads((paths.coding / second / "mockllm_offline" / tid / "globals.json").read_text())
    rec1 = json.loads((out / "globals.json").read_text())
    assert rec2["supersedes"] == rec1["record_id"]
    ledger2 = json.loads(paths.ledger.read_text())["attempts"]
    assert [a["role"] for a in ledger2[18:]] == ["judge", "judge"]


def test_early_stop_on_empty_client_reply(protocol, tmp_path):
    def respond(messages, *args):
        if messages[0].text.startswith("Play only the USER"):
            out = ModelOutput.from_content(M, "")
            out.usage = ModelUsage(input_tokens=10, output_tokens=0, total_tokens=10)
            return out
        return offline.respond(messages, *args)

    OFFLINE_RESPONDER["fn"] = respond
    batch = protocol.batch("technical_check_01")
    paths = RunPaths(tmp_path / batch.batch_id)
    run_generation(protocol, batch, paths, _overrides(batch), display="none")
    for rec in paths.conversation_records():
        assert rec["status"] == "partial" and rec["stop_reason"] == "client_empty_response"
        assert len(rec["messages"]) == 2 and rec["unappended_response"]["text"] == ""
    score_id = run_scoring(protocol, batch, paths, [M], display="none")
    summary = json.loads((paths.coding / score_id / "batch_summary.json").read_text())
    assert summary["automated_checks"]["counts"] == {"skipped": 4}
    assert summary["technical_check_complete"] is False
    assert len(json.loads(paths.ledger.read_text())["attempts"]) == 4  # no judge calls


def test_paid_models_refused_offline(protocol, tmp_path):
    batch = protocol.batch("technical_check_01")
    paths = RunPaths(tmp_path / batch.batch_id)
    with pytest.raises(SystemExit) as ex:
        run_generation(protocol, batch, paths, {r: "openai/gpt-x" for r in batch.roles}, display="none")
    assert "not allowed" in str(ex.value)
    assert not paths.ledger.exists()


def test_role_inputs(protocol):
    """Target sees only its system instruction + public dialogue; client gets the private persona."""
    scen = protocol.scenarios["martin_v5"]
    public = [{"message_id": "m001", "role": "user", "content": scen["opening_message"]},
              {"message_id": "m002", "role": "assistant", "content": "Hello"}]
    tv = target_view(protocol, public)
    assert [m.role for m in tv] == ["system", "user", "assistant"]
    assert tv[0].text == "You are a helpful assistant."
    assert all("Drinking pattern" not in m.text for m in tv)
    cs = client_system_prompt(protocol, scen)
    assert cs.startswith(protocol.client_system) and "Drinking pattern" in cs
    assert "if_drinking_not_mentioned: yeah that" in cs and "martin_v5" not in cs
    payload = json.loads(client_user_payload(1, public))
    assert payload == {"generated_followup_index": 1, "messages": public}
    direct = client_system_prompt(protocol, protocol.scenarios["martin_v2"])
    assert "if_drinking_not_mentioned: (none supplied for this version)" in direct


def test_unapproved_judge_refused(protocol, tmp_path):
    from conftest import live_plan
    live_plan(protocol, {"target_openai": "openai/a", "target_anthropic": "anthropic/b",
                         "client_provisional": "anthropic/b", "judge_provisional": "anthropic/b"})
    protocol.__dict__["pricing"]["anthropic/other"] = {"input_usd_per_mtok": 1, "output_usd_per_mtok": 1}
    batch = protocol.batch("technical_check_01")
    with pytest.raises(SystemExit) as ex:
        run_scoring(protocol, batch, RunPaths(tmp_path / "b"), ["anthropic/other"], display="none")
    assert "not the batch's approved judge" in str(ex.value)
