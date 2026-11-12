# Changes for protocol miti-chat-pilot-0.4

This file records how each item in Shaddy's memo of 9 November 2026
(`docs/handoff/04_Changes_after_technical_check_1.docx`) was implemented.

## B. Files used by the program

| Memo item | Done |
|---|---|
| Nine persona fields | Three indirect openings (v4–v6) now say "and she brought up my drinking again", and all six `if_asked_about_drinking` guides are updated, in `assets/first_persona_versions.json`. Nothing else in the scenarios changed. |
| New client prompt | `assets/client_system.txt` is replaced. Paragraph 3 is the disclosure rule; paragraph 4 is the cue rule (a passing reference no longer cancels the cue). |
| README cue rule | `docs/handoff/README_START_HERE.txt`, "Conversation inputs", has the new sentence. |
| Persona template | `reference/Original_persona_template.docx` is replaced with the revised template. |
| Protocol version 0.4 | Updated in the run plan, scenarios, both schemas, `judge_common.txt`, the offline fixture, the package and the README. The offline model and tests now read the version from the run plan instead of hard-coding it. The records in `runs/technical_check_01` stay at 0.3, and `miti score` refuses to re-score transcripts generated under another protocol version. |

## C. The judge call that didn't finish

- **Output caps:** behavior pass 64,000 tokens; globals pass 16,000; targets 16,000; Martin 8,000 (raised from 1,000 because reasoning uses the output allowance). Claude Opus 5.5 allows up to 128,000 output tokens, and Inspect streams large Claude responses automatically.
- **Timeouts:** 900 s per attempt and 1,860 s per request. The request timeout covers both attempts and the 5-second retry wait (900 + 5 + 900). `retry_wait_seconds` is now set in the run plan.
- **One behavior pass per full transcript, as before.** Nothing is split per reply.
- **Rerun one pass:** `miti score --passes behaviors --transcripts <id> --supersedes <earlier scoring run>`. This runs only that pass, with the usual checks and exports.
  - The original transcript and records are not touched.
  - The transcript hash is checked against the earlier record.
  - The new record links to the one it supersedes and stores its settings (`judge_settings`).
  - `counts.json` combines the new pass with the earlier pass by reference.
- **technical_check_01 can't be rescored with this.** Its transcripts were generated under 0.3, so the program refuses (see the protocol-version guard above). Rescoring them would also need a 0.3 judge prompt and schema, plus approval for that batch.

## D. Checks before paid runs

- **Tests:** 57 offline tests pass. They no longer depend on whether the saved run plan is in live mode: the test fixture forces offline mode in memory. One test checks that a live plan names an approved batch whose roles all have explicit reasoning.
- **Reasoning, set and recorded explicitly** (`generation.reasoning_effort`): medium for both targets and for Martin, high for the judge.
  - **What each provider receives:**
    - **OpenAI** gets `reasoning: {effort}` through the Responses API.
    - **Anthropic** gets `thinking: {type: adaptive}` and `output_config: {effort}`.
  - **Before any paid call:** live access is refused if a role has no effort set, or if Inspect would not send that effort to that model. For example, a non-reasoning model is refused rather than silently falling back to the provider default.
  - **After each call:** the reasoning fields of the request actually sent are stored in the ledger (`provider_request_settings`). Inspect is told to keep every request (`log_model_api=True`), not just the first few per model. A call missing the requested effort is treated as failed, and the conversation or pass stops with `reasoning_not_applied`.
  - **Verified offline:** I ran all three roles against a local mock of both APIs. The exact payloads were `reasoning.effort=medium` (OpenAI), `thinking=adaptive` with `output_config.effort=medium` (Claude client and target) and `output_config.effort=high` (Claude judge). No extra requests were sent.
- **Per-conversation checks** (`client_checks` in `private/conversations/<id>.json`, shown in the report). These are rule-based, with no model calls and no regeneration.
  - **Cue:** whether the indirect cue was due (the first assistant reply asked about drinking or addressed it as a concern) and whether it was used (similarity to the guide), with the supporting sentences.
  - **Disclosure:** whether the usual amount, the frequency and the heavier occasions became public, with message IDs and the exact sentences.
  - **Length:** sentence counts for each of Martin's replies.
  - **Review flags:** anything uncertain is flagged for human review. The patterns are in `config/client_checks.json`.
- **technical_check_01 zip:** the complete `runs/technical_check_01` folder, including `private/`, is zipped for Shaddy.

## E. technical_check_02

- **Batch:** `revised_technical_pilot` in the run plan. Martin v2 is paired with `openai/gpt-6.1-sol` and Martin v5 with `anthropic/claude-opus-5-5`. Claude Opus 5.5 also plays Martin and judges.
- **Length and calls:** 8 assistant replies and 7 follow-ups per conversation. That's 34 nominal calls (16 target, 14 client, 4 judge) and at most 68 attempts.
- **Approval:** live mode is set for this batch with a $20 cap and 68 attempts.
- **Model IDs and prices:** checked against the providers' model and pricing pages on 21 October (`config/pricing.json`). Recheck before running if prices may have changed.
- **Estimate** (`miti estimate`): about $4 typical, using output sizes from technical_check_01. It is $22 if every output reached its cap; the $20 cap stops the run before that.
- **Report:** `miti report --batch technical_check_02` writes the HTML report (settings, estimate, actual cost, client checks, dialogues and coding), a readable zip, and a complete zip of the batch folder including `private/`. Settings and the estimate come from the run's own manifests and ledger, not the current run plan. The disclosure table separates details Martin gave in his generated replies from those already in the fixed opening.
- **Unresolved PERSUADE + PWP volley:** if C1–C4 pass and a volley retains both codes in separate thought units, the behavior record is `needs_review` and its counts stay unfinalized. This was already the rule; the record now also shows `counted: null` for every volley.
