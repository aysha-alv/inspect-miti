"""Automated output checks C1-C5 (README, "AUTOMATED OUTPUT CHECKS AND EXPORTS").

These check data format and consistency only; they do not say whether a
rating or label is correct.

C1  schema, protocol version, transcript ID; unique IDs within each record type
C2  volleys: every assistant message in exactly one volley, members in order and
    contiguous apart from the listed facilitative user interjections
C3  thought units point to an existing assistant message in their volley; quotes
    (and global evidence quotes) match exactly at occurrence_index
C4  thought-unit spans don't overlap, are in dialogue order, and cover every
    non-whitespace assistant character once
C5  counting rules (applied in coding.py once C1-C4 pass)

Offsets are Unicode code points (Python string indices), end exclusive. For an
unmatched quote the closest candidate substring is reported for diagnosis only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator
from rapidfuzz import fuzz

CHECK_IDS = ("C1", "C2", "C3", "C4", "C5")
CANDIDATE_MIN_SIMILARITY = 60.0


def esc(text: str) -> str:
    """JSON-escaped text so whitespace and unusual characters are visible."""
    return json.dumps(text, ensure_ascii=True)


@dataclass
class CheckReport:
    applicable: tuple[str, ...]
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    diagnostics: dict[str, list[Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for cid in CHECK_IDS:
            self.results[cid] = {"applicable": cid in self.applicable,
                                 "status": "pending" if cid in self.applicable else "not_applicable",
                                 "failures": []}

    def fail(self, cid: str, message: str) -> None:
        self.results[cid]["failures"].append(message)

    def diag(self, key: str, item: Any) -> None:
        self.diagnostics.setdefault(key, []).append(item)

    def close(self, cid: str, *, ran: bool = True) -> None:
        r = self.results[cid]
        if not r["applicable"]:
            return
        r["status"] = ("fail" if r["failures"] else "pass") if ran else "not_run"

    def set_status(self, cid: str, status: str) -> None:
        self.results[cid]["status"] = status

    @property
    def failed(self) -> list[str]:
        return [cid for cid, r in self.results.items() if r["status"] == "fail"]

    def to_dict(self) -> dict[str, Any]:
        return {"failed_checks": self.failed, "checks": self.results, "diagnostics": self.diagnostics}


# ---- quote matching -------------------------------------------------------

def occurrences(content: str, quote: str) -> list[int]:
    """Start offsets of every exact occurrence (overlapping occurrences included)."""
    starts, i = [], content.find(quote)
    while i != -1:
        starts.append(i)
        i = content.find(quote, i + 1)
    return starts


def closest_candidate(content: str, quote: str) -> dict[str, Any] | None:
    if not content or not quote:
        return None
    a = fuzz.partial_ratio_alignment(quote, content)
    if a is None or a.score < CANDIDATE_MIN_SIMILARITY:
        return None
    text = content[a.dest_start:a.dest_end]
    return {"text_escaped": esc(text), "start_char": a.dest_start, "end_char": a.dest_end,
            "similarity": round(a.score, 1), "diagnostic_only": True}


def resolve_quote(report: CheckReport, check_id: str, where: str, message: dict[str, str] | None,
                  message_id: str, quote: str, occurrence_index: int) -> tuple[int, int] | None:
    """(start, end) of the quote's occurrence, or None (recording a failure and diagnostics)."""
    if message is None:
        report.fail(check_id, f"{where}: message_id {message_id!r} not in transcript")
        return None
    starts = occurrences(message["content"], quote)
    if occurrence_index < len(starts):
        start = starts[occurrence_index]
        return start, start + len(quote)
    if starts:
        report.fail(check_id, f"{where}: occurrence_index {occurrence_index} but only {len(starts)} "
                              f"exact match(es) in {message_id}")
    else:
        report.fail(check_id, f"{where}: quote not found exactly in {message_id}")
    report.diag("unmatched_quotes", {
        "where": where, "message_id": message_id, "quote_escaped": esc(quote),
        "occurrence_index": occurrence_index, "exact_matches_found": len(starts),
        "closest_candidate": closest_candidate(message["content"], quote),
    })
    return None


# ---- C1 -------------------------------------------------------------------

def _check_c1(report: CheckReport, output: Any, schema: dict[str, Any], transcript_id: str) -> bool:
    """Schema, protocol version (a schema const) and transcript ID. True if the structure is usable."""
    errors = sorted(Draft202012Validator(schema).iter_errors(output), key=lambda e: list(e.absolute_path))
    for e in errors:
        path = "/".join(str(p) for p in e.absolute_path) or "(root)"
        report.fail("C1", f"schema: {path}: {e.message}")
    if isinstance(output, dict) and output.get("transcript_id") != transcript_id:
        report.fail("C1", f"transcript_id {output.get('transcript_id')!r} does not match requested {transcript_id!r}")
    return not errors


def _unique(report: CheckReport, items: list[dict[str, Any]], key: str) -> None:
    seen: set[str] = set()
    for it in items:
        if it[key] in seen:
            report.fail("C1", f"duplicate {key} {it[key]!r}")
        seen.add(it[key])


# ---- global pass ----------------------------------------------------------

@dataclass
class GlobalCheck:
    report: CheckReport
    evidence: list[dict[str, Any]]      # resolved evidence with offsets
    scores: dict[str, int] | None


def check_globals(output: Any, transcript: dict[str, Any], schema: dict[str, Any]) -> GlobalCheck:
    report = CheckReport(applicable=("C1", "C3"))
    by_id = {m["message_id"]: m for m in transcript["messages"]}
    evidence: list[dict[str, Any]] = []
    if output is None:
        report.fail("C1", "no parseable JSON object in the response")
        report.close("C1")
        report.close("C3", ran=False)
        return GlobalCheck(report, evidence, None)
    usable = _check_c1(report, output, schema, transcript["transcript_id"])
    report.close("C1")
    if not usable:
        report.close("C3", ran=False)
        return GlobalCheck(report, evidence, None)
    for name, rating in output["ratings"].items():
        if not rating["evidence"]:
            report.diag("empty_evidence_ratings", {"rating": name, "rationale": rating["rationale"]})
        for i, ev in enumerate(rating["evidence"]):
            where = f"ratings.{name}.evidence[{i}]"
            span = resolve_quote(report, "C3", where, by_id.get(ev["message_id"]), ev["message_id"],
                                 ev["quote"], ev["occurrence_index"])
            evidence.append({"rating": name, "index": i, **ev,
                             "start_char": span[0] if span else None, "end_char": span[1] if span else None})
    report.close("C3")
    scores = {name: r["score"] for name, r in output["ratings"].items()}
    return GlobalCheck(report, evidence, scores)


# ---- behavior pass --------------------------------------------------------

@dataclass
class BehaviorCheck:
    report: CheckReport
    utterances: list[dict[str, Any]]    # utterances with start_char/end_char (None if unresolved)
    volleys: list[dict[str, Any]]       # volleys in output order, each with its utterance labels


def _check_c2(report: CheckReport, output: dict[str, Any], transcript: dict[str, Any]) -> None:
    msgs = transcript["messages"]
    index = {m["message_id"]: i for i, m in enumerate(msgs)}
    membership: dict[str, list[str]] = {m["message_id"]: [] for m in msgs if m["role"] == "assistant"}
    for v in output["volleys"]:
        vid, members = v["volley_id"], v["assistant_message_ids"]
        ok = True
        for mid in members:
            if mid not in index:
                report.fail("C2", f"{vid}: {mid!r} is not in the transcript")
                ok = False
            elif msgs[index[mid]]["role"] != "assistant":
                report.fail("C2", f"{vid}: {mid} is a user message, not an assistant message")
                ok = False
            elif vid not in membership[mid]:
                membership[mid].append(vid)
        if not ok:
            continue
        positions = [index[mid] for mid in members]
        if positions != sorted(set(positions)):
            report.fail("C2", f"{vid}: assistant_message_ids are not in transcript order (or repeat)")
            continue
        between: list[str] = []
        for a, b in zip(positions, positions[1:]):
            gap = msgs[a + 1:b]
            if any(m["role"] == "assistant" for m in gap):
                report.fail("C2", f"{vid}: members {msgs[a]['message_id']} and {msgs[b]['message_id']} "
                                  "are not contiguous (another assistant message lies between them)")
            between += [m["message_id"] for m in gap if m["role"] == "user"]
        listed = v["intervening_facilitative_user_ids"]
        if sorted(listed) != sorted(between):
            report.fail("C2", f"{vid}: intervening_facilitative_user_ids {listed} must list exactly the "
                              f"intervening user messages {between}")
    for mid, vids in membership.items():
        if len(vids) != 1:
            report.fail("C2", f"assistant message {mid} belongs to {len(vids)} volleys {vids} (must be exactly 1)")


def check_behaviors(output: Any, transcript: dict[str, Any], schema: dict[str, Any]) -> BehaviorCheck:
    report = CheckReport(applicable=("C1", "C2", "C3", "C4", "C5"))
    msgs = transcript["messages"]
    by_id = {m["message_id"]: m for m in msgs}
    order = {m["message_id"]: i for i, m in enumerate(msgs)}
    if output is None:
        report.fail("C1", "no parseable JSON object in the response")
    usable = output is not None and _check_c1(report, output, schema, transcript["transcript_id"])
    if usable:
        _unique(report, output["volleys"], "volley_id")
        _unique(report, output["utterances"], "utterance_id")
    report.close("C1")
    if not usable:
        for cid in ("C2", "C3", "C4", "C5"):
            report.close(cid, ran=False)
        return BehaviorCheck(report, [], [])

    # C2 volleys
    _check_c2(report, output, transcript)
    report.close("C2")

    # C3 utterance references and exact quotes
    volleys = {v["volley_id"]: v for v in output["volleys"]}
    utterances: list[dict[str, Any]] = []
    for u in output["utterances"]:
        where = f"utterance {u['utterance_id']}"
        msg = by_id.get(u["message_id"])
        if msg is not None and msg["role"] != "assistant":
            report.fail("C3", f"{where}: {u['message_id']} is a user message; only assistant text is coded")
        vol = volleys.get(u["volley_id"])
        if vol is None:
            report.fail("C3", f"{where}: volley_id {u['volley_id']!r} does not exist")
        elif u["message_id"] not in vol["assistant_message_ids"]:
            report.fail("C3", f"{where}: {u['message_id']} is not a member of {u['volley_id']}")
        span = resolve_quote(report, "C3", where, msg, u["message_id"], u["quote"], u["occurrence_index"])
        utterances.append({**u, "start_char": span[0] if span else None, "end_char": span[1] if span else None})
    report.close("C3")

    # C4 spans: no overlap, dialogue order, full coverage of non-whitespace assistant text
    resolved = [u for u in utterances if u["start_char"] is not None
                and by_id.get(u["message_id"], {}).get("role") == "assistant"]
    keys = [(order[u["message_id"]], u["start_char"]) for u in resolved]
    for prev, cur, u in zip(keys, keys[1:], resolved[1:]):
        if cur < prev:
            report.fail("C4", f"utterance {u['utterance_id']} is out of dialogue order")
    for m in msgs:
        if m["role"] != "assistant":
            continue
        spans = sorted((u["start_char"], u["end_char"], u["utterance_id"]) for u in resolved
                       if u["message_id"] == m["message_id"])
        for k, (s2, e2, id2) in enumerate(spans):
            for s1, e1, id1 in spans[:k]:
                if s2 < e1:  # spans are sorted by start, so this is an overlap
                    end = min(e1, e2)
                    report.fail("C4", f"{id1} and {id2} overlap in {m['message_id']} [{s2}, {end})")
                    report.diag("overlaps", {"message_id": m["message_id"], "utterance_ids": [id1, id2],
                                             "start_char": s2, "end_char": end,
                                             "text_escaped": esc(m["content"][s2:end])})
        covered = [False] * len(m["content"])
        for s, e, _ in spans:
            for i in range(s, e):
                covered[i] = True
        i, text = 0, m["content"]
        while i < len(text):
            if covered[i] or text[i].isspace():
                i += 1
                continue
            j = i
            while j < len(text) and not covered[j]:
                j += 1
            while text[j - 1].isspace():
                j -= 1
            report.fail("C4", f"uncovered text in {m['message_id']} [{i}, {j})")
            report.diag("uncovered", {"message_id": m["message_id"], "start_char": i, "end_char": j,
                                      "text_escaped": esc(text[i:j])})
            i = j
    report.close("C4")

    # labels per volley, in the order volleys are listed
    volley_rows = []
    for v in output["volleys"]:
        labels = [u["code"] for u in utterances if u["volley_id"] == v["volley_id"]]
        volley_rows.append({**v, "labels": labels})
    return BehaviorCheck(report, utterances, volley_rows)
