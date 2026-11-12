# miti-chat

Conversation generation and MITI 4.2.1 coding for the study of how general-purpose
assistants respond to incidental mentions of potentially heavy drinking
(protocol **miti-chat-pilot-0.4**). Built directly on [Inspect AI](https://inspect.aisi.org.uk/).

A simulated client (playing a persona version such as Martin v2) chats with a target
assistant that only has the instruction "You are a helpful assistant." The saved,
blinded transcript is then coded by a judge model that reads the full prepared MITI
manual, in two independent passes: global ratings and behavior codes. The program
checks the judge's answers and calculates the official counts and summaries.

The protocol documents from Shaddy are in [`docs/handoff/`](docs/handoff/).
`README_START_HERE.txt` there is the detailed specification this code implements, and
`04_Changes_after_technical_check_1.docx` is the memo behind protocol 0.4.
Implementation choices are in [`docs/review_notes.md`](docs/review_notes.md) and the 0.4
changes in [`docs/changes_0.4.md`](docs/changes_0.4.md).

**Live access.** Every paid request is refused unless `assets/run_plan.json` is in live
mode for the named, approved batch with model IDs, token limits, reasoning efforts and
request/dollar limits (see [Going live](#going-live)). The plan is currently approved for
`technical_check_02` only.

## Setup

Python 3.12 or newer. With [uv](https://docs.astral.sh/uv/):

```bash
uv sync --extra dev          # installs the pinned versions from uv.lock
uv run miti check            # offline setup check
```

Or with pip: `python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`.

API keys are only needed for live runs. Copy `.env.example` to `.env` and fill in
`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`. `.env` is git-ignored.

## Commands

| Command | What it does | API calls |
|---|---|---|
| `miti check` | Loads the run plan, personas, prompts and schemas; verifies the manual and PDF hashes against `reference/manual_preparation.json`; runs the supplied fixture and aggregation cases; lists unmet live-access conditions | none |
| `miti dry-run` | Full generate → score run with an offline stand-in model, to check message exchange, stopping, storage, checks and exports | none |
| `miti estimate --batch technical_check_02` | Typical and worst-case token and cost estimate | none |
| `miti generate --batch technical_check_02` | Generates the batch's conversations | client, target |
| `miti score --batch technical_check_02` | Codes saved, completed transcripts (two judge calls each) | judge |
| `miti report --batch technical_check_02` | Readable HTML report (settings, cost, client checks, dialogues, coding) plus a readable zip and a complete zip of the batch folder | none |
| `inspect view --log-dir runs/<batch>/private/inspect_logs` | Browse the Inspect logs (private: they contain persona prompts) | none |

`miti score` options: `--transcripts tr_… tr_…` (default: all in the batch),
`--judges provider/model …` (default: the batch's judge role), `--stage`
(default: `technical_test`), `--supersedes <scoring_run_id>` to link corrections to
earlier records, `--passes behaviors` (or `globals`) to rerun just one judge pass. With
`--supersedes`, the pass that wasn't rerun is carried over by reference so `counts.json`
is complete; the transcript hash is checked against the earlier record and the new pass
records its settings. Re-scoring never calls the client or target and never overwrites
earlier scores. Transcripts generated under an older protocol version are not re-scored.

Run `uv run pytest` for the tests (offline; no API calls).

## How a run works

1. **Opening.** The selected version's fixed opening is the first user message, sent unchanged.
2. **Target reply.** The target gets `assets/target_system.txt` as its system message plus
   the public user/assistant history in normal API roles. No MI content, rubric or persona facts.
3. **Client follow-up.** A fresh request with two messages: system = `assets/client_system.txt`
   plus the selected private persona (profile, writing style, readiness, opening type,
   response guides); user = JSON `{generated_followup_index, messages}` with the public
   history. The client's text is appended unchanged as the next user message.
4. **Stop** right after the last assistant reply (`max_assistant_replies`: 4 by default,
   8 for `technical_check_02`; a batch can set its own). Early
   stops (failed request, empty or truncated response, request/cost limit, cancellation)
   keep the partial dialogue and the reason. Every message is saved as it is produced.
   When a conversation ends, rule-based client checks (no model calls) record whether the
   indirect cue was due and used, whether the usual amount, frequency and heavier
   occasions became public (message IDs and sentences), and Martin's sentence counts,
   with anything uncertain flagged for human review (`config/client_checks.json`).
   Every call sets reasoning explicitly per role (`generation.reasoning_effort`); the
   request actually sent is checked and recorded, and a call without the requested
   effort counts as failed.
5. **Code** (separate command). For each completed transcript and judge, two fresh calls:

   ```
   system: assets/judge_common.txt + assets/judge_globals.txt | judge_behaviors.txt
           + reference/MITI_4_2_1_for_judge.txt          (the whole prepared manual)
   user:   TRANSCRIPT_ID, STAGE, OUTPUT_SCHEMA, DIALOGUE  (clearly labeled fields)
   ```

   The manual text is the complete coding content of the 40-page MITI 4.2.1 PDF (pages
   2–40: definitions, anchors, examples, decision rules, FAQs and the coding form) with
   only the repeated running headers, page numbers and draft/citation footer removed and
   tables written out as text. It goes into every judge call, so the judge works from
   the manual the way a human coder does. `miti check` confirms the file is the one
   recorded in `reference/manual_preparation.json`.

6. **Check and count.** Checks C1–C5, pipeline status, counting rules and summary formulas
   follow `docs/handoff/README_START_HERE.txt` exactly (see `src/miti_chat/checks.py`
   and `counts.py`).

## Outputs

```
runs/<batch_id>/
  transcripts/<id>.json, <id>.txt       blinded public transcripts (exactly transcript_id + messages)
  coding/<scoring_run_id>/
    batch_summary.json                  completion / automated checks / unresolved decisions, kept separate
    check_report.json                   failed check IDs, diagnostics, review triggers per record
    <judge>/<id>/globals.json           raw response, parsed answer, judge status, pipeline status, checks
    <judge>/<id>/behaviors.json         same, plus resolved thought-unit spans
    <judge>/<id>/counts.json            labels per volley, finalized counts and the six summaries
    <judge>/<id>/thought_units.tsv, volleys.tsv
  private/                              NOT for blinded coding
    transcript_key.csv                  transcript ID -> scenario, target role, models
    conversations/<id>.json             stop reason, lengths, word counts, usage, attempts per message
    ledger.json                         every provider attempt: settings, timing, usage, cost, raw text
    manifests/                          code commit, library versions, SHA-256 of every protocol file
    inspect_logs/                       Inspect .eval logs
```

Transcript IDs are random (`tr_` + 12 hex characters) and message IDs are sequential
(`m001`, …), so nothing in `transcripts/` reveals persona, version or model. A
transcript file is updated as each message arrives, so `transcripts/` also holds any
partial conversations; `batch_summary.json` lists which are complete. Share completed
transcripts with human coders, never `private/`.

## Going live

Only after Shaddy approves the named batch:

1. Put the approved model IDs (Inspect `provider/model` form) in `assets/run_plan.json`
   `model_ids`, the per-role `response_token_limits` and `reasoning_effort`, and add the
   batch definition (items, conversation length, caps).
2. Add each model's price to `config/pricing.json` (USD per million input/output tokens).
3. Set `mode: "live"`, `allow_paid_calls: true`, `approved_batch_id`,
   `approved_max_cost_usd` and `approved_max_total_requests`.
4. Put the API keys in `.env`. `miti check` should report `live access for batch <id>: ALLOWED`.
5. Commit, then run (currently approved: `technical_check_02`, $20 and 68 attempts):

   ```bash
   uv run miti estimate --batch technical_check_02
   uv run miti generate --batch technical_check_02
   uv run miti score --batch technical_check_02
   uv run miti report --batch technical_check_02
   ```

Before every paid attempt (including retries) the code re-checks the gate and refuses
if the request count or a conservative cost bound would exceed the approved limits.
The ledger (`runs/<batch>/private/ledger.json`) is shared by `generate` and `score`,
so the limits cover the whole batch as long as both use the same runs directory.
Requests run one at a time.

## Repository layout

```
assets/        protocol files from Shaddy: run plan, personas, role prompts, judge prompts, schemas, fixtures
reference/     MITI 4.2.1 PDF, the prepared manual text for the judge, preparation record, persona template
config/        pricing.json (needed for live runs), proposed_settings.json (estimate inputs),
               client_checks.json (patterns for the rule-based client checks)
docs/          handoff/ (Shaddy's documents and memos), review_notes.md, changes_0.4.md
src/miti_chat/ config, budget (gate + ledger), reasoning, calls, conversation, client_checks, transcripts,
               coding, checks, counts, estimate, report, share, provenance, offline, cli
tests/         offline tests: fixture and aggregation cases, checks C1-C5, gate and limits, reasoning,
               client checks, single-pass reruns, end-to-end dry runs of both technical batches
```

The MITI manual is an unpublished draft ("Do not cite without permission"). Keep this
repository private.
