"""Readable run report and zips for review (`miti report`).

Writes to runs/<batch>/share/:
  <batch>_report_<scoring_run_id>.html   one self-contained page: summary, settings, cost
                                         estimate and actual cost, client checks, dialogues,
                                         judge ratings and codes, counts, every API call
  <batch>_readable.zip                   HOW_TO_VIEW.txt + the report + plain-text transcripts
  <batch>_complete.zip                   the whole runs/<batch> folder, including private/

The report and the complete zip are NOT blinded (they map transcripts to persona
versions and models). Coders get only transcripts/.
"""

from __future__ import annotations

import csv
import html
import json
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from .config import Batch, Protocol
from .estimate import estimate_batch
from .runs import RunPaths

e = html.escape

CSS = """body{font:15px/1.5 -apple-system,Helvetica,Arial,sans-serif;max-width:1040px;margin:32px auto;padding:0 16px;color:#1f2328;background:#fff}
h1{font-size:24px}h2{font-size:20px;margin-top:40px;border-bottom:1px solid #d0d7de;padding-bottom:4px}h3{font-size:16px;margin-top:24px}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}th,td{border:1px solid #d0d7de;padding:5px 7px;text-align:left;vertical-align:top}
th{background:#f6f8fa}td{white-space:pre-wrap}.msg{border:1px solid #d0d7de;border-radius:6px;padding:10px 12px;margin:8px 0;white-space:pre-wrap}
.user{background:#f6f8fa}.who{font-size:12px;font-weight:600;color:#57606a;margin-bottom:4px;white-space:normal}
.ok{color:#1a7f37;font-weight:600}.bad{color:#cf222e;font-weight:600}.warn{color:#9a6700;font-weight:600}
.note{background:#fff8c5;border:1px solid #d4a72c;border-radius:6px;padding:8px 12px;margin:8px 0}
code{background:#f6f8fa;padding:1px 4px;border-radius:4px}.small{font-size:12px;color:#57606a}"""

HOW_TO_VIEW = """{batch}: results ({today})

HOW TO VIEW
1. Unzip this folder (double-click the .zip file).
2. Double-click the .html report. It opens in your web browser (Chrome, Safari or Edge).
   There's nothing to install and no internet connection needed.
3. Scroll down. The report has:
   - Summary: what ran, automated check results, cost against the cap
   - Settings: models, reasoning effort per role (requested and as sent), output caps,
     timeouts, conversation length
   - Cost estimate (typical and worst case) and actual cost by role
   - For each conversation:
     - client checks: indirect cue due/used, whether usual amount, frequency and
       heavier occasions became public (with message IDs), and Martin's sentence counts
     - the full dialogue (USER = simulated client, ASSISTANT = model being tested)
     - the judge's global ratings with rationales and exact evidence
     - every coded thought unit, and the counts and summary scores
   - Appendix: which transcript is which persona version and model, and every API call

The "transcripts" folder has the conversations as plain text files.
The report is not blinded; coders should receive only the transcripts.
"""


def _status(s: str) -> str:
    cls = {"valid": "ok", "completed": "ok", "skipped": "bad", "invalid": "bad", "partial": "bad",
           "needs_review": "warn"}.get(s, "")
    return f'<span class="{cls}">{e(s)}</span>'


def _fmt(v: Any) -> str:
    return "null" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))


def _latest(directory: Path, pattern: str) -> Path | None:
    found = sorted(directory.glob(pattern))
    return found[-1] if found else None


def build_report(protocol: Protocol, batch: Batch, paths: RunPaths, scoring_run_id: str | None = None) -> Path:
    priv = paths.private
    if scoring_run_id is None:
        latest = _latest(paths.coding, "score_*")
        if latest is None:
            raise FileNotFoundError(f"no scoring runs in {paths.coding}")
        scoring_run_id = latest.name
    coding = paths.coding / scoring_run_id
    summary = json.loads((coding / "batch_summary.json").read_text(encoding="utf-8"))
    score_manifest = json.loads((paths.manifests / f"{scoring_run_id}.json").read_text(encoding="utf-8"))
    gen_manifests = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(paths.manifests.glob("gen_*.json"))]
    ledger = json.loads(paths.ledger.read_text(encoding="utf-8"))["attempts"] if paths.ledger.is_file() else []
    convs = {c["transcript_id"]: c for c in paths.conversation_records()}
    key = {r["transcript_id"]: r for r in csv.DictReader(paths.key_file.open(encoding="utf-8"))} \
        if paths.key_file.is_file() else {}
    tids = sorted(convs)

    P: list[str] = []
    add = P.append
    billed = sum(a["cost_usd"] for a in ledger if a.get("cost_usd") is not None)
    uncertain = [a for a in ledger if a.get("cost_usd") is None]
    held = sum(a["reserved_cost_usd"] for a in uncertain)
    code = score_manifest["provenance"]["code"]
    add(f"<h1>MITI chat pilot: {e(batch.batch_id)}</h1>")
    add(f"<p class='small'>Protocol {e(protocol.version)} · scoring run {e(scoring_run_id)} · "
        f"code commit <code>{e(str(code.get('git_commit'))[:7])}</code>"
        f"{' (uncommitted changes)' if code.get('uncommitted_changes') else ''} · "
        f"inspect-ai {e(score_manifest['provenance']['versions'].get('inspect-ai') or '')}</p>")

    # ---- summary
    recs = summary["automated_checks"]["records"]
    run_passes = score_manifest.get("passes") or list(protocol.judging["passes"])
    if score_manifest.get("supersedes_run") or len(run_passes) < len(protocol.judging["passes"]):
        add(f"<div class='note'><b>Partial scoring run.</b> This run reran {e(', '.join(run_passes))} for "
            f"{e(', '.join(score_manifest.get('transcripts') or []))}, superseding records in "
            f"{e(str(score_manifest.get('supersedes_run')))}. Other passes and transcripts are in that run's report; "
            f"completeness below refers to this run only.</div>")
    cc = summary["conversation_completion"]["counts"]
    add("<h2>Summary</h2><ul>")
    add(f"<li><b>Conversations:</b> {', '.join(f'{n} {k}' for k, n in cc.items())} "
        f"(limit {batch.max_assistant_replies} assistant replies).</li>")
    rc = Counter(r["pipeline_validation_status"] for r in recs)
    add(f"<li><b>Judge passes:</b> {', '.join(f'{n} {k}' for k, n in rc.items())} of {len(recs)}. "
        f"Failed format checks: {sum(len(r['failed_checks']) for r in recs)}. "
        f"Unresolved coding decisions: {len(summary['unresolved_coding_decisions'])}.</li>")
    add(f"<li><b>Technical check complete:</b> {'yes' if summary['technical_check_complete'] else '<span class=bad>no</span>'}.</li>")
    add(f"<li><b>Cost:</b> ${billed:.2f} billed (provider-reported usage)"
        + (f", plus ${held:.2f} held for {len(uncertain)} attempt(s) with uncertain charges" if uncertain else "")
        + f". {len(ledger)} provider attempts; approved cap ${_fmt(protocol.plan.get('approved_max_cost_usd'))} "
          f"and {protocol.plan.get('approved_max_total_requests')} attempts.</li></ul>")
    problems = [r for r in recs if r["pipeline_validation_status"] != "valid"]
    for r in problems:
        why = r.get("skip_reason") or ", ".join(r["failed_checks"]) or "judge or pipeline review trigger"
        add(f"<div class='note'><b>{e(r['pass'])} pass, <code>{e(r['transcript_id'])}</code>:</b> "
            f"{_status(r['pipeline_validation_status'])}: {e(why)}</div>")
    for c in convs.values():
        if c["status"] != "completed":
            add(f"<div class='note'><b>Conversation <code>{e(c['transcript_id'])}</code> stopped early:</b> "
                f"{e(c['stop_reason'] or '')} {e(c.get('stop_detail') or '')}</div>")

    # ---- settings (from this run's own manifests and ledger, not the current run plan)
    gm = gen_manifests[-1] if gen_manifests else {}
    models = gm.get("models") or {r: protocol.model_ids.get(r) for r in batch.roles}
    efforts = {**(gm.get("reasoning_effort") or {}), **(score_manifest.get("reasoning_effort") or {})}
    caps = score_manifest.get("token_limits") or gm.get("token_limits") or protocol.generation["response_token_limits"]
    ex = gm.get("execution") or score_manifest.get("execution") or protocol.execution
    add("<h2>Settings</h2>")
    add("<p class='small'>As recorded for this run (generation and scoring manifests; reasoning as sent from the ledger).</p>")
    ledger_role = {batch.client_role: "client", batch.judge_role: "judge"}
    sent: dict[tuple[str, str], list[Any]] = {}
    for a in ledger:
        if not str(a.get("model", "")).startswith("mockllm/") and a.get("outcome") == "ok":
            sent.setdefault((a["role"], a["model"]), []).append(a)
    add("<table><tr><th>Role</th><th>Model</th><th>Reasoning requested</th><th>Reasoning as sent</th>"
        "<th>Output cap (tokens)</th></tr>")
    for role in batch.roles:
        model = (score_manifest.get("judges") or [models.get(role)])[0] if role == batch.judge_role else models.get(role)
        model = model or ""
        cap = (f"globals {caps['judge_globals']}, behaviors {caps['judge_behaviors']}" if role == batch.judge_role
               else str(caps["client"] if role == batch.client_role else caps["target"]))
        calls = sent.get((ledger_role.get(role, "target"), model), [])
        variants = Counter(json.dumps({k: v for k, v in (a.get("provider_request_settings") or {}).items()
                                       if k in ("reasoning", "thinking", "output_config")}, sort_keys=True)
                           for a in calls if a.get("provider_request_settings"))
        unverified = sum(1 for a in calls if a.get("reasoning_applied") is None)
        sent_txt = "; ".join(f"{v} ({n} calls)" for v, n in variants.items()) or "(no paid call recorded)"
        if unverified:
            sent_txt += f"; {unverified} call(s) not verified"
        add(f"<tr><td>{e(role)}</td><td>{e(model)}</td><td>{e(str(efforts.get(role)))}</td>"
            f"<td>{e(sent_txt)}</td><td>{e(cap)}</td></tr>")
    add("</table>")
    add(f"<p class='small'>Conversation length: {gm.get('max_assistant_replies', batch.max_assistant_replies)} assistant "
        f"replies, {gm.get('max_generated_client_followups', batch.max_generated_client_followups)} generated "
        f"follow-ups. Timeouts: {ex['attempt_timeout_seconds']} s per "
        f"attempt, {ex['request_timeout_seconds']} s per request (both attempts and the retry wait); at most "
        f"{int(ex['transport_retries_max']) + 1} attempts per request; run one at a time. Judge output: "
        f"{e(protocol.judging['structured_output_mode'])}, validated locally. Sampling: provider defaults; no seed; "
        f"prompt caching off. Reasoning labels are provider-specific and do not imply equal compute.</p>")

    # ---- cost
    est = gm.get("cost_estimate") or estimate_batch(protocol, batch)
    add("<h2>Cost</h2>")
    if est and est.get("nominal_cost_usd") is not None:
        add(f"<p>Estimate before the run: typical about <b>${est['typical_cost_usd']:.2f}</b>; worst case "
            f"${est['nominal_cost_usd']:.2f} if every output hit its cap, ${est['worst_case_cost_usd_with_retries']:.2f} "
            f"if every request also used its retry. {est['nominal_requests']} nominal requests, at most "
            f"{est['max_attempts_with_one_retry_each']} attempts.</p>")
    by_role: dict[str, list[float]] = {}
    for a in ledger:
        v = a["cost_usd"] if a.get("cost_usd") is not None else a["reserved_cost_usd"]
        by_role.setdefault(f"{a['role']} ({a['model']})", []).append(v)
    add("<table><tr><th>Role (model)</th><th>Attempts</th><th>USD (billed or held)</th></tr>" + "".join(
        f"<tr><td>{e(k)}</td><td>{len(v)}</td><td>{sum(v):.4f}</td></tr>" for k, v in by_role.items()) + "</table>")

    # ---- conversations
    for t in tids:
        conv = json.loads((paths.transcripts / f"{t}.json").read_text(encoding="utf-8"))
        c = convs[t]
        add(f"<h2>Transcript <code>{e(t)}</code> · {_status(c['status'])}</h2>")
        lens = c.get("lengths") or {}
        words = lens.get("word_count_by_speaker", {})
        add(f"<p class='small'>Words: user {words.get('user')}, assistant {words.get('assistant')} · "
            f"stop: {e(c.get('stop_reason') or '')}</p>")
        ck = c.get("client_checks") or {}
        if ck.get("status") == "checked":
            add("<h3>Client checks (screening only; confirm by reading)</h3>")
            cue = ck["cue"]
            if cue.get("applies") and cue.get("status") != "not_reached":
                add(f"<p><b>Indirect cue:</b> due = {cue['cue_due']}, used = {e(cue['cue_used'])} "
                    f"(similarity to guide {cue['guide_similarity']}), consistent with rule = {cue['consistent_with_rule']}.")
                ev = cue["evidence_asked_about_drinking"] + cue["evidence_addressed_as_concern"] + cue["evidence_passing_reference"]
                if ev:
                    add("<br><span class='small'>Alcohol in first assistant reply: " + " | ".join(f"“{e(x)}”" for x in ev) + "</span>")
                add("</p>")
            else:
                add(f"<p><b>Indirect cue:</b> {e(cue.get('reason') or cue.get('status') or 'n/a')}</p>")
            add("<table><tr><th>Drinking detail</th><th>In Martin's replies?</th><th>Public at all?</th>"
                "<th>Where (exact sentence)</th></tr>")
            for k, v in ck["disclosure"].items():
                where = "<br>".join(f"<b>{e(h['message_id'])}</b>{' (fixed opening)' if h['source'] == 'fixed_opening' else ''}: "
                                    f"“{e(h['text'])}”" for h in v["evidence"][:4]) or "—"
                yes = lambda b: "yes" if b else "<span class=bad>no</span>"  # noqa: E731
                add(f"<tr><td>{e(k.replace('_', ' '))}</td><td>{yes(v.get('in_client_replies'))}</td>"
                    f"<td>{yes(v['public'])}</td><td>{where}</td></tr>")
            add("</table>")
            add("<p class='small'>Martin's sentences per reply: " + ", ".join(
                f"{r['message_id']}: {r['sentences']}" for r in ck["client_reply_sentences"])
                + f" (style guide: up to {ck['max_sentences_per_reply']}).</p>")
            if ck["flags_for_human_review"]:
                add("<p class='small'><b>For human review:</b></p><ul class='small'>"
                    + "".join(f"<li>{e(f)}</li>" for f in ck["flags_for_human_review"]) + "</ul>")
        add("<h3>Dialogue</h3>")
        for m in conv["messages"]:
            add(f"<div class='msg {m['role']}'><div class='who'>{e(m['message_id'])} · {m['role'].upper()}</div>"
                f"{e(m['content'])}</div>")
        judge_dirs = sorted(coding.glob(f"*/{t}"))
        if not judge_dirs:
            add("<p class='small'>Not scored in this scoring run.</p>")
        for jd in judge_dirs:
            _judge_section(add, jd)

    # ---- appendix
    add("<h2>Appendix: run records (not blinded)</h2>")
    add("<p class='small'>Keep this report and the private/ folder away from coders. Coders get only transcripts/.</p>")
    add("<table><tr><th>Transcript</th><th>Persona version</th><th>Target</th><th>Client</th><th>Status</th></tr>")
    for t in tids:
        k = key.get(t, {})
        add(f"<tr><td><code>{e(t)}</code></td><td>{e(k.get('scenario_id', ''))}</td><td>{e(k.get('target_model', ''))}</td>"
            f"<td>{e(k.get('client_model', ''))}</td><td>{e(k.get('stop_reason', ''))}</td></tr>")
    add("</table>")
    add("<table><tr><th>Role</th><th>Model</th><th>Purpose</th><th>Outcome</th><th>In tok</th><th>Out tok</th>"
        "<th>Reasoning tok</th><th>Stop</th><th>USD</th></tr>")
    for a in ledger:
        u = a.get("usage") or {}
        cost = a.get("cost_usd")
        add(f"<tr><td>{e(a['role'])}</td><td>{e(a['model'])}</td><td>{e(a['purpose'])}</td><td>{e(a['outcome'])}</td>"
            f"<td>{u.get('input_tokens', '')}</td><td>{u.get('output_tokens', '')}</td><td>{u.get('reasoning_tokens', '')}</td>"
            f"<td>{e(str(a.get('stop_reason') or ''))}</td>"
            f"<td>{'%.4f' % cost if cost is not None else '(held %.4f)' % a['reserved_cost_usd']}</td></tr>")
    add("</table>")
    if gen_manifests:
        add(f"<p class='small'>Generation runs: {', '.join(e(g['generation_run_id']) for g in gen_manifests)}.</p>")

    share = paths.root / "share"
    share.mkdir(parents=True, exist_ok=True)
    out = share / f"{batch.batch_id}_report_{scoring_run_id}.html"
    out.write_text(f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' "
                   f"content='width=device-width,initial-scale=1'><title>MITI {e(batch.batch_id)} report</title>"
                   f"<style>{CSS}</style></head><body>{''.join(P)}</body></html>", encoding="utf-8")
    return out


def _judge_section(add: Any, jd: Path) -> None:
    c = json.loads((jd / "counts.json").read_text(encoding="utf-8")) if (jd / "counts.json").is_file() else None
    carried = (c or {}).get("passes_carried_from") or {}

    def load(pass_name: str, name: str) -> dict[str, Any] | None:
        p = jd / name
        if not p.is_file() and pass_name in carried:  # pass carried over from a superseded run
            p = jd.parents[2] / carried[pass_name] / jd.parent.name / jd.name / name
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    g = load("globals", "globals.json")
    b = load("behaviors", "behaviors.json")
    if g:
        add(f"<h3>Global ratings · judge {e(g['judge_model'])} · {_status(g['pipeline_validation_status'])}</h3>")
        go = g.get("parsed_output") or {}
        if g["pipeline_validation_status"] == "skipped":
            add(f"<p>{e(g.get('skip_reason', ''))}</p>")
        elif go.get("ratings"):
            add("<table><tr><th>Global</th><th>Score</th><th>Rationale</th><th>Manual ref.</th><th>Evidence (exact quotes)</th></tr>")
            for k, v in go["ratings"].items():
                ev = "<br>".join(f"<b>{e(x['message_id'])}</b>: “{e(x['quote'])}”" for x in v["evidence"]) or "<i>none (absence)</i>"
                add(f"<tr><td>{e(k.replace('_', ' ').title())}</td><td><b>{v['score']}</b></td><td>{e(v['rationale'])}</td>"
                    f"<td>{e(v['manual_reference'])}</td><td>{ev}</td></tr>")
            add("</table>")
            if go.get("review_notes"):
                add("<p class='small'><b>Judge notes:</b></p><ul class='small'>"
                    + "".join(f"<li>{e(n)}</li>" for n in go["review_notes"]) + "</ul>")
    if b:
        add(f"<h3>Behavior codes · {_status(b['pipeline_validation_status'])}</h3>")
        bo = b.get("parsed_output") or {}
        if b["pipeline_validation_status"] == "skipped":
            add(f"<p>{e(b.get('skip_reason', ''))}</p>")
        elif bo.get("utterances"):
            failed = (b.get("check_report") or {}).get("failed_checks") or []
            if failed:
                add(f"<p class='bad'>Failed checks: {e(', '.join(failed))} (see check_report.json)</p>")
            add("<table><tr><th>Volley</th><th>Msg</th><th>Code</th><th>Thought unit (exact)</th><th>Rationale</th>"
                "<th>Manual ref.</th><th>Judge note</th></tr>")
            for u in bo["utterances"]:
                add(f"<tr><td>{e(u['volley_id'])}</td><td>{e(u['message_id'])}</td><td><b>{e(u['code'])}</b></td>"
                    f"<td>{e(u['quote'])}</td><td>{e(u['rationale'])}</td><td>{e(u['manual_reference'])}</td>"
                    f"<td>{e(u['review_note'])}</td></tr>")
            add("</table>")
            if bo.get("review_notes"):
                add("<p class='small'><b>Judge notes:</b></p><ul class='small'>"
                    + "".join(f"<li>{e(n)}</li>" for n in bo["review_notes"]) + "</ul>")
        elif b.get("raw_response"):
            add(f"<p class='bad'>Response could not be parsed ({e(b.get('parse_note', ''))}); raw text is in behaviors.json.</p>")
    if c:
        add("<h3>Counts and summaries</h3>")
        if c.get("passes_carried_from"):
            add(f"<p class='small'>Passes carried over from earlier scoring runs: {e(json.dumps(c['passes_carried_from']))}</p>")
        if c.get("behavior_counts"):
            add("<table><tr>" + "".join(f"<th>{k}</th>" for k in c["behavior_counts"]) + "</tr><tr>"
                + "".join(f"<td>{v}</td>" for v in c["behavior_counts"].values()) + "</tr></table>")
        elif c.get("counts_not_finalized_reason"):
            add(f"<p class='small'>{e(c['counts_not_finalized_reason'])}</p>")
        sums = {**c["global_summaries"], **c["behavior_summaries"]}
        add("<table><tr><th>Summary</th><th>Value</th><th>Note</th></tr>" + "".join(
            f"<tr><td>{e(k.replace('_', ' '))}</td><td>{_fmt(v['value'])}</td><td>{e(v['reason'] or '')}</td></tr>"
            for k, v in sums.items()) + "</table>")


def write_zips(batch: Batch, paths: RunPaths, report: Path) -> tuple[Path, Path]:
    """Readable zip (report + plain-text transcripts + note) and complete zip of the whole batch folder."""
    share = paths.root / "share"
    readable = share / f"{batch.batch_id}_readable.zip"
    complete = share / f"{batch.batch_id}_complete.zip"
    base = f"{batch.batch_id}_results"
    with zipfile.ZipFile(readable, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{base}/HOW_TO_VIEW.txt", HOW_TO_VIEW.format(batch=batch.batch_id, today=date.today().isoformat()))
        z.write(report, f"{base}/{report.name}")
        for p in sorted(paths.transcripts.glob("*.txt")):
            z.write(p, f"{base}/transcripts/{p.name}")
    with zipfile.ZipFile(complete, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(paths.root.rglob("*")):
            if p.is_file() and share not in p.parents and p.suffix != ".lock" and p.name != ".DS_Store":
                z.write(p, f"{batch.batch_id}/{p.relative_to(paths.root).as_posix()}")
    return readable, complete
