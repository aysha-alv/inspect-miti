"""Checks C1-C5 and pipeline status rules."""

import copy
import json

from miti_chat.checks import check_behaviors, check_globals
from miti_chat.coding import evaluate_pass, parse_json
from miti_chat.config import Protocol

VERSION = Protocol().version


def _behaviors(protocol, transcript, output):
    return check_behaviors(output, transcript, protocol.schema("behaviors"))


def test_fixture_passes(protocol, fixture):
    bc = _behaviors(protocol, fixture["transcript"], fixture["behavior_output"])
    assert bc.report.failed == []
    assert [(u["start_char"], u["end_char"]) for u in bc.utterances] == [(0, 23), (24, 51), (0, 29), (30, 59)]


def test_c1_transcript_id_and_schema(protocol, fixture):
    out = copy.deepcopy(fixture["behavior_output"])
    out["transcript_id"] = "other"
    out["utterances"][0]["code"] = "OPEN_Q"
    bc = _behaviors(protocol, fixture["transcript"], out)
    assert "C1" in bc.report.failed
    fails = " ".join(bc.report.results["C1"]["failures"])
    assert "does not match requested" in fails and "OPEN_Q" in fails


def test_c1_duplicate_ids(protocol, fixture):
    out = copy.deepcopy(fixture["behavior_output"])
    out["utterances"][1]["utterance_id"] = "u01"
    assert "C1" in _behaviors(protocol, fixture["transcript"], out).report.failed


def test_c2_assistant_message_missing_from_volleys(protocol, fixture):
    out = copy.deepcopy(fixture["behavior_output"])
    out["volleys"] = out["volleys"][:1]
    out["utterances"] = out["utterances"][:2]
    bc = _behaviors(protocol, fixture["transcript"], out)
    assert "C2" in bc.report.failed
    assert "m004 belongs to 0 volleys" in " ".join(bc.report.results["C2"]["failures"])


def test_c2_facilitative_interjection(protocol, fixture):
    """Two assistant messages may form one volley when the listed user message is between them."""
    out = copy.deepcopy(fixture["behavior_output"])
    out["volleys"] = [{"volley_id": "v01", "assistant_message_ids": ["m002", "m004"],
                       "intervening_facilitative_user_ids": ["m003"], "boundary_note": "test"}]
    for u in out["utterances"]:
        u["volley_id"] = "v01"
    assert _behaviors(protocol, fixture["transcript"], out).report.failed == []
    out["volleys"][0]["intervening_facilitative_user_ids"] = []
    bc = _behaviors(protocol, fixture["transcript"], out)
    assert bc.report.failed == ["C2"]


def test_c3_unmatched_quote_reports_candidate(protocol, fixture):
    out = copy.deepcopy(fixture["behavior_output"])
    out["utterances"][2]["quote"] = "You sometimes have 6 beers."
    bc = _behaviors(protocol, fixture["transcript"], out)
    assert "C3" in bc.report.failed
    diag = bc.report.diagnostics["unmatched_quotes"][0]
    assert diag["message_id"] == "m004"
    cand = diag["closest_candidate"]
    assert cand["diagnostic_only"] and json.loads(cand["text_escaped"]).startswith("You sometimes have")
    # the candidate is never substituted: the utterance stays unresolved
    assert bc.utterances[2]["start_char"] is None


def test_c3_occurrence_index_and_volley_membership(protocol, fixture):
    out = copy.deepcopy(fixture["behavior_output"])
    out["utterances"][0]["occurrence_index"] = 1
    out["utterances"][3]["volley_id"] = "v01"
    fails = " ".join(_behaviors(protocol, fixture["transcript"], out).report.results["C3"]["failures"])
    assert "only 1 exact match" in fails and "not a member of v01" in fails


def test_c4_uncovered_and_overlap(protocol, fixture):
    out = copy.deepcopy(fixture["behavior_output"])
    out["utterances"][1]["quote"] = "do you have?"     # leaves "How many beers" uncovered
    out["utterances"][3]["quote"] = "six beers. What concerns"  # overlaps u03
    bc = _behaviors(protocol, fixture["transcript"], out)
    assert "C4" in bc.report.failed
    unc = bc.report.diagnostics["uncovered"][0]
    assert unc["message_id"] == "m002" and json.loads(unc["text_escaped"]) == "How many beers"
    assert bc.report.diagnostics["overlaps"][0]["utterance_ids"] == ["u03", "u04"]


def test_c4_order(protocol, fixture):
    out = copy.deepcopy(fixture["behavior_output"])
    out["utterances"] = [out["utterances"][1], out["utterances"][0], *out["utterances"][2:]]
    bc = _behaviors(protocol, fixture["transcript"], out)
    assert bc.report.failed == ["C4"]


def test_markdown_kept_in_spans(protocol):
    t = {"transcript_id": "t1", "messages": [
        {"message_id": "m001", "role": "user", "content": "hi"},
        {"message_id": "m002", "role": "assistant", "content": "## Options\n\n- **Talk** to her?\n- Wait"}]}
    out = {"protocol_version": protocol.version, "transcript_id": "t1", "status": "ok",
           "volleys": [{"volley_id": "v1", "assistant_message_ids": ["m002"],
                        "intervening_facilitative_user_ids": [], "boundary_note": ""}],
           "utterances": [
               {"utterance_id": "u1", "volley_id": "v1", "message_id": "m002", "quote": "## Options",
                "occurrence_index": 0, "code": "UNCODED", "rationale": "", "manual_reference": "", "review_note": ""},
               {"utterance_id": "u2", "volley_id": "v1", "message_id": "m002", "quote": "- **Talk** to her?",
                "occurrence_index": 0, "code": "Q", "rationale": "", "manual_reference": "", "review_note": ""}],
           "review_notes": []}
    bc = _behaviors(protocol, t, out)
    assert bc.report.failed == ["C4"]  # "- Wait" not covered
    assert json.loads(bc.report.diagnostics["uncovered"][0]["text_escaped"]) == "- Wait"


def _globals_output(fixture, evidence):
    rating = {"score": 3, "rationale": "r", "manual_reference": "D", "evidence": evidence}
    return {"protocol_version": VERSION, "transcript_id": fixture["transcript"]["transcript_id"],
            "status": "ok", "review_notes": [],
            "ratings": {k: copy.deepcopy(rating) for k in
                        ("cultivating_change_talk", "softening_sustain_talk", "partnership", "empathy")}}


def test_globals_evidence(protocol, fixture):
    ok = _globals_output(fixture, [{"message_id": "m004", "quote": "What concerns you", "occurrence_index": 0}])
    gc = check_globals(ok, fixture["transcript"], protocol.schema("globals"))
    assert gc.report.failed == [] and gc.evidence[0]["start_char"] == 30
    bad = _globals_output(fixture, [{"message_id": "m004", "quote": "What worries you", "occurrence_index": 0}])
    assert check_globals(bad, fixture["transcript"], protocol.schema("globals")).report.failed == ["C3"]
    empty = _globals_output(fixture, [])
    gc = check_globals(empty, fixture["transcript"], protocol.schema("globals"))
    assert gc.report.failed == [] and len(gc.report.diagnostics["empty_evidence_ratings"]) == 4
    out_of_range = _globals_output(fixture, [])
    out_of_range["ratings"]["empathy"]["score"] = 7
    assert check_globals(out_of_range, fixture["transcript"], protocol.schema("globals")).report.failed == ["C1"]


def test_parse_json():
    assert parse_json('{"a": 1}')[0] == {"a": 1}
    obj, note = parse_json('```json\n{"a": 1}\n```')
    assert obj == {"a": 1} and "fence" in note
    assert parse_json('Here you go: {"a": 1}')[0] is None


def test_pipeline_status(protocol, fixture):
    t = fixture["transcript"]
    out = copy.deepcopy(fixture["behavior_output"])
    fields, _ = evaluate_pass("behaviors", json.dumps(out), t, protocol)
    assert fields["pipeline_validation_status"] == "valid"

    out["status"] = "needs_review"
    fields, _ = evaluate_pass("behaviors", json.dumps(out), t, protocol)
    assert fields["pipeline_validation_status"] == "needs_review" and fields["judge_status"] == "needs_review"

    out = copy.deepcopy(fixture["behavior_output"])
    out["utterances"][0]["code"], out["utterances"][1]["code"] = "PERSUADE", "PWP"
    fields, _ = evaluate_pass("behaviors", json.dumps(out), t, protocol)
    assert fields["pipeline_validation_status"] == "needs_review"
    assert fields["check_report"]["checks"]["C5"]["status"] == "counts_not_finalized"

    out["transcript_id"] = "wrong"  # a failed check wins over review triggers and the judge's "ok"
    fields, _ = evaluate_pass("behaviors", json.dumps(out), t, protocol)
    assert fields["pipeline_validation_status"] == "invalid"

    fields, _ = evaluate_pass("behaviors", "not json", t, protocol)
    assert fields["pipeline_validation_status"] == "invalid" and fields["raw_response"] == "not json"


def test_overlap_reported_for_non_adjacent_spans(protocol):
    t = {"transcript_id": "t1", "messages": [
        {"message_id": "m001", "role": "user", "content": "hi"},
        {"message_id": "m002", "role": "assistant", "content": "abcdefghij"}]}
    def u(i, q):
        return {"utterance_id": f"u{i}", "volley_id": "v1", "message_id": "m002", "quote": q,
                "occurrence_index": 0, "code": "GI", "rationale": "", "manual_reference": "", "review_note": ""}
    out = {"protocol_version": protocol.version, "transcript_id": "t1", "status": "ok",
           "volleys": [{"volley_id": "v1", "assistant_message_ids": ["m002"],
                        "intervening_facilitative_user_ids": [], "boundary_note": ""}],
           "utterances": [u(1, "abcdefghij"), u(2, "cd"), u(3, "fgh")], "review_notes": []}
    bc = _behaviors(protocol, t, out)
    pairs = [d["utterance_ids"] for d in bc.report.diagnostics["overlaps"]]
    assert ["u1", "u2"] in pairs and ["u1", "u3"] in pairs


def test_unresolved_persuasion_not_exported_as_counted(protocol, fixture):
    from miti_chat.coding import counts_record
    out = copy.deepcopy(fixture["behavior_output"])
    out["utterances"][0]["code"], out["utterances"][1]["code"] = "PERSUADE", "PWP"
    fields, check = evaluate_pass("behaviors", json.dumps(out), fixture["transcript"], protocol)
    b = {"record_id": "b1", **fields}
    g = {"record_id": "g1", "pipeline_validation_status": "skipped"}
    rec = counts_record("t", "j", g, b, check)
    assert rec["labels_by_volley"][0] == {"volley_id": "v01", "labels": ["PERSUADE", "PWP"], "counted": None}
    assert rec["behavior_counts"] is None and rec["counts_finalized"] is False
    assert rec["behavior_summaries"]["total_mi_nonadherent"]["value"] is None
