"""Batch summary for a scoring run: conversation completion, automated check
results and unresolved coding decisions, reported separately."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from .runs import RunPaths, write_json


def _records(paths: RunPaths, scoring_run_id: str) -> list[dict[str, Any]]:
    root = paths.coding / scoring_run_id
    out = []
    for p in sorted(root.glob("*/*/*.json")):
        if p.name in ("globals.json", "behaviors.json"):
            out.append(json.loads(p.read_text(encoding="utf-8")))
    return out


def write_batch_summary(paths: RunPaths, scoring_run_id: str) -> dict[str, Any]:
    conversations = [
        {"transcript_id": r["transcript_id"], "status": r["status"], "stop_reason": r["stop_reason"],
         "assistant_replies": (r.get("lengths") or {}).get("assistant_reply_count")}
        for r in paths.conversation_records()
    ]
    records = _records(paths, scoring_run_id)
    check_rows, unresolved = [], []
    for r in records:
        report = r.get("check_report") or {}
        check_rows.append({
            "transcript_id": r["transcript_id"], "judge_model": r["judge_model"], "pass": r["record_type"],
            "record_id": r["record_id"], "pipeline_validation_status": r["pipeline_validation_status"],
            "judge_status": r.get("judge_status"), "failed_checks": report.get("failed_checks", []),
            "skip_reason": r.get("skip_reason"),
        })
        if r["pipeline_validation_status"] == "needs_review":
            notes = (r.get("parsed_output") or {}).get("review_notes", [])
            unresolved.append({"transcript_id": r["transcript_id"], "judge_model": r["judge_model"],
                               "pass": r["record_type"], "record_id": r["record_id"],
                               "review_triggers": r.get("review_triggers", []), "judge_review_notes": notes})
    completed = {c["transcript_id"] for c in conversations if c["status"] == "completed"}
    obtained = {(r["transcript_id"], r["judge_model"], r["record_type"]) for r in records
                if r["pipeline_validation_status"] != "skipped"}
    judges = {r["judge_model"] for r in records}
    all_judged = bool(judges) and all((t, j, p) in obtained for t in completed for j in judges
                                      for p in ("globals", "behaviors"))
    summary = {
        "scoring_run_id": scoring_run_id,
        # complete = every conversation reached the reply limit and every judge pass returned a response
        "technical_check_complete": bool(conversations) and len(completed) == len(conversations) and all_judged,
        "conversation_completion": {
            "counts": dict(Counter(c["status"] for c in conversations)),
            "conversations": conversations,
            "all_conversations_completed": bool(conversations) and all(c["status"] == "completed"
                                                                         for c in conversations),
        },
        "automated_checks": {
            "counts": dict(Counter(row["pipeline_validation_status"] for row in check_rows)),
            "records": check_rows,
        },
        "unresolved_coding_decisions": unresolved,
    }
    out = paths.coding / scoring_run_id
    write_json(out / "batch_summary.json", summary)
    write_json(out / "check_report.json", [
        {**row, "checks": (r.get("check_report") or {}).get("checks"),
         "diagnostics": (r.get("check_report") or {}).get("diagnostics"),
         "review_triggers": r.get("review_triggers", [])}
        for row, r in zip(check_rows, records)
    ])
    return summary
