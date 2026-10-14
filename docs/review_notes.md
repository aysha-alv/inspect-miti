# Review notes for Shaddy: technical_check_01

Status: code ready for review for the technical test; live calls disabled. Model IDs,
prices and the cost estimate still need to be filled in (table below). Everything else
was checked offline (`miti check`, `miti dry-run`, 45 tests).

## To fill in before approval (Aysha)

| Item | Where | Status |
|---|---|---|
| Model IDs for target_openai, target_anthropic, client_provisional, judge_provisional | `config/proposed_settings.json` → after approval `assets/run_plan.json` | to propose |
| Prices for each model (USD per million tokens, with date checked) | `config/pricing.json` | to fill |
| Per-role response token limits | proposed: target 4096, client 300, judge_globals 4000, judge_behaviors 16000 | proposed |
| Cost estimate | `uv run miti estimate --batch technical_check_01` once models and prices are in | pending |
| Request cap | 36 (18 nominal requests, each retried at most once) | from run plan |

Proposed live commands (after the run plan is updated and committed):

```bash
uv run miti check
uv run miti generate --batch technical_check_01
uv run miti score --batch technical_check_01
```

## How the spec was implemented, where it needed interpreting

1. **Retries and timeouts.** Inspect's own retry is set to 0 and the OpenAI/Anthropic SDK
   retry layer is switched off (`max_retries=0` on the client). The program performs the
   one permitted retry itself, so the live-access gate and the request/dollar limits are
   checked before *every* attempt and each attempt is logged. Only transient errors
   (provider's own classification: rate limit, 5xx, connection; plus Inspect attempt
   timeouts) are retried. For OpenAI models `reasoning_summary` is set explicitly, because
   leaving it unset makes Inspect send an extra probe request outside the ledger.
   `attempt_timeout_seconds` → Inspect `attempt_timeout`; `request_timeout_seconds` →
   Inspect `timeout` and an overall deadline for the request including the retry wait.
2. **Extra live-access conditions.** Besides the run plan's gate, live calls also require
   the four response token limits (needed for the cost bound), a price for each paid model,
   and a provider whose SDK retries the code has verified it can disable (OpenAI and
   Anthropic so far). In live mode `miti score` only accepts the batch's approved judge
   (`model_ids.judge_provisional`). The ledger is one file per batch
   (`runs/<batch>/private/ledger.json`), re-read under a file lock before each attempt, so
   `generate` and `score` share the limits; use the same `--runs-dir` for both.
3. **Cost bound per attempt.** Estimated input tokens (tiktoken o200k_base +10% for OpenAI;
   +30% for Anthropic, which has no offline tokenizer) plus the full output limit, at list
   price. An attempt that errors without a 4xx status keeps its reservation as its cost
   (possible charge); a 4xx rejection is recorded as $0.
4. **Prompt caching** is turned off explicitly (`cache_prompt=False`). Sampling uses provider
   defaults; no seed is requested. Each attempt records requested settings, the served model,
   finish reason and usage.
5. **Empty or truncated responses** end the conversation as partial. The response text is kept
   in the private record (`unappended_response`) but not appended to the public dialogue.
6. **Client request.** The `messages` array in the client's JSON payload is the public message
   array as saved (`message_id`, `role`, `content`). The persona block after
   `client_system.txt` lists readiness, opening type, the six profile fields (including
   writing style) and the three response guides, with "(none supplied for this version)"
   for a null guide. Scenario ID and version label are not sent.
7. **Judge request.** System = `judge_common.txt`, the pass file and the manual text, joined by
   blank lines. User = `TRANSCRIPT_ID`, `STAGE`, `OUTPUT_SCHEMA` and `DIALOGUE` (the message
   array, JSON-encoded) as labeled fields.
8. **JSON parsing.** The reply is parsed as returned. One exception: if the whole reply is a
   single ```` ```json ```` code fence, the fence is removed before parsing and this is recorded
   in `parse_note`. Nothing else is altered and there are no repair calls. Please confirm or
   ask for strict parsing.
9. **occurrence_index** counts overlapping exact occurrences (e.g. "aa" in "aaa" occurs twice).
   `intervening_facilitative_user_ids` must contain exactly the intervening user messages;
   their order in the list is not checked.
10. **Finalized results.** Global ratings and the Technical/Relational summaries are reported
    only when the global record is `valid`; behavior counts and the four count summaries only
    when the behavior record is `valid`. Labels per volley are kept whenever C1–C4 pass; the
    codes each volley contributes (`counted`) are shown only once counts are finalized, so an
    unresolved PERSUADE + PWP volley never appears as counting both.
12. **Batch summary.** `technical_check_complete` is true only if every conversation reached
    the reply limit and every judge pass returned a response. Conversation completion,
    automated check results and unresolved coding decisions are reported separately.
11. **Inspect logs** (`.eval`) are written to `runs/<batch>/private/inspect_logs/` and treated
    as private.

## Known limitations

- Anthropic token counts in the estimate and reservations are approximate (+30% margin).
  The provider's token-count endpoint could replace this once API use is approved.
- tiktoken downloads its encoding file on first use; without network it falls back to
  one token per 3 characters (+30%).
- Later stages (client selection, judge validation with human coding and ICC) are not built yet.
