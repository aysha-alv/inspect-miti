"""Command line: miti check | estimate | dry-run | generate | score.

Generation and scoring are separate commands. Paid calls are refused unless the
run plan's live-access gate is satisfied for the named batch.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from inspect_ai import eval as inspect_eval
from jsonschema import Draft202012Validator

from . import counts as C
from . import provenance, transcripts
from .budget import is_free, live_gate_problems
from .checks import check_behaviors
from .coding import RECORD_FILES, coding_task, output_dir, skipped_record
from .config import ROOT, Batch, ConfigError, Protocol
from .conversation import conversation_task, resolve_models
from .estimate import estimate_batch, format_estimate
from .report import write_batch_summary
from .runs import RunPaths, run_id, write_json


# ---- shared steps (also used by the tests) --------------------------------

def _eval(protocol: Protocol, task: Any, paths: RunPaths, display: str) -> None:
    concurrency = int(protocol.execution["concurrency"])
    inspect_eval(task, model=None, log_dir=str(paths.inspect_logs), max_samples=concurrency, max_tasks=1,
                 fail_on_error=False, retry_on_error=0, display=display, log_level="warning")


def _refuse_if_gated(protocol: Protocol, batch: Batch, model_ids: list[str]) -> None:
    paid = [m for m in model_ids if not is_free(m)]
    if paid:
        problems = live_gate_problems(protocol, batch, paid)
        if problems:
            sys.exit("Live model calls are not allowed for this batch:\n  - " + "\n  - ".join(problems))


def run_generation(protocol: Protocol, batch: Batch, paths: RunPaths, overrides: dict[str, str] | None = None,
                   display: str = "plain") -> str:
    models = resolve_models(protocol, batch, overrides)
    missing = [role for role, m in models.items() if role != batch.judge_role and not m]
    if missing:
        sys.exit(f"No model ID for role(s) {missing} in run_plan.json model_ids")
    _refuse_if_gated(protocol, batch, [m for r, m in models.items() if m and r != batch.judge_role])
    gen_id = run_id("gen")
    write_json(paths.manifests / f"{gen_id}.json", {
        "generation_run_id": gen_id, "batch": batch.raw, "models": models,
        "token_limits": protocol.generation["response_token_limits"], "execution": protocol.execution,
        "provenance": provenance.record(protocol), "manual_check": provenance.manual_check(protocol),
    })
    _eval(protocol, conversation_task(protocol, batch, paths, models, gen_id), paths, display)
    return gen_id


def run_scoring(protocol: Protocol, batch: Batch, paths: RunPaths, judges: list[str],
                transcript_ids: list[str] | None = None, stage: str | None = None,
                supersedes_run: str | None = None, display: str = "plain") -> str:
    stage = stage or protocol.judging["transcript_stage_for_first_batch"]
    _refuse_if_gated(protocol, batch, judges)
    approved_judge = protocol.model_ids.get(batch.judge_role)
    unapproved = [j for j in judges if not is_free(j) and j != approved_judge]
    if unapproved:
        sys.exit(f"Judge model(s) {unapproved} are not the batch's approved judge "
                 f"(model_ids.{batch.judge_role} = {approved_judge!r})")
    if supersedes_run and not (paths.coding / supersedes_run).is_dir():
        sys.exit(f"--supersedes: no scoring run {supersedes_run!r} in {paths.coding}")
    records = {r["transcript_id"]: r for r in paths.conversation_records()}
    wanted = transcript_ids or sorted(records)
    score_id = run_id("score")
    eligible, skipped = [], {}
    for tid in wanted:
        rec = records.get(tid)
        tpath = paths.transcripts / f"{tid}.json"
        if rec is None or not tpath.is_file():
            skipped[tid] = "transcript not found in this batch"
        elif rec["status"] != "completed":
            skipped[tid] = f"conversation not completed (stop_reason: {rec['stop_reason']})"
        elif transcripts.content_hash(transcripts.load(tpath)) != rec["transcript_sha256"]:
            skipped[tid] = "transcript content hash does not match the generation record"
        else:
            eligible.append(tid)
    for tid, reason in skipped.items():
        for judge in judges:
            for pass_name in protocol.judging["passes"]:
                write_json(output_dir(paths, score_id, judge, tid) / RECORD_FILES[pass_name],
                           skipped_record(pass_name, tid, judge, stage, score_id, protocol, reason))
    write_json(paths.manifests / f"{score_id}.json", {
        "scoring_run_id": score_id, "batch_id": batch.batch_id, "judges": judges, "stage": stage,
        "transcripts": eligible, "skipped": skipped, "supersedes_run": supersedes_run,
        "output_mode": protocol.judging["structured_output_mode"],
        "token_limits": protocol.generation["response_token_limits"], "execution": protocol.execution,
        "provenance": provenance.record(protocol), "manual_check": provenance.manual_check(protocol),
    })
    if eligible:
        _eval(protocol, coding_task(protocol, batch, paths, eligible, judges, score_id, stage, supersedes_run),
              paths, display)
    write_batch_summary(paths, score_id)
    return score_id


# ---- commands -------------------------------------------------------------

def cmd_check(args: argparse.Namespace) -> int:
    protocol = Protocol()
    ok = True

    def line(good: bool, text: str) -> None:
        nonlocal ok
        ok &= good
        print(f"[{'ok' if good else 'FAIL'}] {text}")

    line(True, f"protocol {protocol.version}; {len(protocol.scenarios)} persona versions loaded")
    for p in ("globals", "behaviors"):
        Draft202012Validator.check_schema(protocol.schema(p))
        line(True, f"{p} schema is a valid JSON Schema; prompt files: {protocol.judge_prompt_files(p)}")
    mc = provenance.manual_check(protocol)
    line(mc["manual_text_matches_record"], f"{mc['manual_text']} matches manual_preparation.json hash")
    line(mc["source_pdf_matches_record"], "source PDF matches manual_preparation.json hash")

    fx = protocol.json("assets/offline_fixture.json")
    bc = check_behaviors(fx["behavior_output"], fx["transcript"], protocol.schema("behaviors"))
    vc = C.count_volleys([v["labels"] for v in bc.volleys])
    got_summary = {k: v["value"] for k, v in C.behavior_summaries(vc.counts).items()} if vc.counts else None
    line(not bc.report.failed and vc.counts == fx["expected_counts"] and got_summary == fx["expected_summary"],
         "offline_fixture.json: checks pass and counts/summaries match")
    for case in protocol.json("assets/aggregation_cases.json")["cases"]:
        r = C.count_volleys(case["volleys"])
        good = True
        if "expected_nonzero_counts" in case:
            good &= r.counts is not None and {k: v for k, v in r.counts.items() if v} == case["expected_nonzero_counts"]
        if "expected_summary" in case:
            s = C.behavior_summaries(r.counts) if r.counts else {}
            good &= all(s[k]["value"] == v for k, v in case["expected_summary"].items())
        if "finalized_counts_allowed" in case:
            good &= r.finalized == case["finalized_counts_allowed"]
        if case.get("expected_status") == "needs_review":
            good &= bool(r.review_triggers)
        line(good, f"aggregation case {case['case']}")

    for bid in protocol.batch_ids():
        batch = protocol.batch(bid)
        problems = live_gate_problems(protocol, batch)
        print(f"\nlive access for batch {bid}: {'ALLOWED' if not problems else 'refused'}")
        for p in problems:
            print(f"  - {p}")
    print("\nversions:", json.dumps(provenance.versions()))
    print("code:", json.dumps(provenance.code_revision(protocol.root)))
    return 0 if ok else 1


def cmd_estimate(args: argparse.Namespace) -> int:
    protocol = Protocol()
    est = estimate_batch(protocol, protocol.batch(args.batch))
    print(format_estimate(est))
    if args.json:
        write_json(Path(args.json), est)
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    load_dotenv(ROOT / ".env")
    protocol = Protocol()
    batch = protocol.batch(args.batch)
    paths = RunPaths.for_batch(batch.batch_id, Path(args.runs_dir) if args.runs_dir else None)
    gen_id = run_generation(protocol, batch, paths, display=args.display)
    _print_conversations(paths, gen_id)
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    load_dotenv(ROOT / ".env")
    protocol = Protocol()
    batch = protocol.batch(args.batch)
    paths = RunPaths.for_batch(batch.batch_id, Path(args.runs_dir) if args.runs_dir else None)
    judges = args.judges or [protocol.model_ids.get(batch.judge_role)]
    if not all(judges):
        sys.exit(f"No judge model: pass --judges or set model_ids.{batch.judge_role}")
    score_id = run_scoring(protocol, batch, paths, judges, args.transcripts, args.stage, args.supersedes,
                           display=args.display)
    _print_summary(paths, score_id)
    return 0


def cmd_dry_run(args: argparse.Namespace) -> int:
    from .offline import OFFLINE_MODEL, install

    install()
    protocol = Protocol()
    batch = protocol.batch(args.batch or protocol.batch_ids()[0])
    runs_dir = Path(args.runs_dir) if args.runs_dir else ROOT / "runs" / run_id("dry_run")
    paths = RunPaths.for_batch(batch.batch_id, runs_dir)
    overrides = {role: OFFLINE_MODEL for role in batch.roles}
    gen_id = run_generation(protocol, batch, paths, overrides, display=args.display)
    _print_conversations(paths, gen_id)
    score_id = run_scoring(protocol, batch, paths, [OFFLINE_MODEL], display=args.display)
    _print_summary(paths, score_id)
    print(f"\nDry-run outputs: {paths.root}")
    return 0


def _print_conversations(paths: RunPaths, gen_id: str) -> None:
    print(f"\nGeneration run {gen_id}")
    for r in paths.conversation_records():
        if r["generation_run_id"] == gen_id:
            n = (r.get("lengths") or {}).get("assistant_reply_count")
            print(f"  {r['transcript_id']}: {r['status']} ({r['stop_reason']}), {n} assistant replies")


def _print_summary(paths: RunPaths, score_id: str) -> None:
    s = json.loads((paths.coding / score_id / "batch_summary.json").read_text(encoding="utf-8"))
    print(f"\nScoring run {score_id}")
    print("  technical check complete:", s["technical_check_complete"])
    print("  conversations:", s["conversation_completion"]["counts"])
    print("  judge-pass records:", s["automated_checks"]["counts"])
    for row in s["automated_checks"]["records"]:
        extra = row["skip_reason"] or ",".join(row["failed_checks"]) or ""
        print(f"    {row['transcript_id']} {row['pass']:<9} {row['pipeline_validation_status']:<12} {extra}")
    print("  unresolved coding decisions:", len(s["unresolved_coding_decisions"]))
    print(f"  outputs: {paths.coding / score_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="miti", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="offline setup check: config, prompts, manual hash, fixtures, live gate")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("estimate", help="conservative cost estimate for a batch (no API calls)")
    p.add_argument("--batch", required=True)
    p.add_argument("--json", help="also write the estimate to this JSON file")
    p.set_defaults(func=cmd_estimate)

    for name, func, helptext in (("generate", cmd_generate, "generate the batch's conversations"),
                                 ("score", cmd_score, "MITI-code saved transcripts"),
                                 ("dry-run", cmd_dry_run, "run generate + score end to end with offline models")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--batch", required=name != "dry-run")
        p.add_argument("--runs-dir", help="parent directory for batch outputs (default: runs/)")
        p.add_argument("--display", default="plain", help="Inspect display: plain, rich, full, none")
        p.set_defaults(func=func)
        if name == "score":
            p.add_argument("--transcripts", nargs="+", help="transcript IDs (default: all in the batch)")
            p.add_argument("--judges", nargs="+", help="judge model IDs (default: the batch's judge role)")
            p.add_argument("--stage", help="stage label for the judge input (default from run plan)")
            p.add_argument("--supersedes", help="earlier scoring_run_id these records correct")

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as ex:
        sys.exit(f"Configuration error: {ex}")


if __name__ == "__main__":
    raise SystemExit(main())
