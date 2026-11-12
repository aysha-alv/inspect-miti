START HERE — HANDOFF FOR AYSHA
14 October 2026 | Protocol miti-chat-pilot-0.4

READING ORDER AND FILE RESPONSIBILITIES
01_Study_plan.docx describes how a run works and the sequence of study stages,
from the technical test through judge validation and the main evaluation.
02_Before_sending_code.docx is Aysha's immediate assignment and review checklist.
03_Handoff_file_guide.docx explains the supporting files and how each is used.
This README gives file locations, software checks and calculation rules.

The prepared MITI manual supplies coding definitions and rating anchors. The
supplied judge prompts specify how to apply them to these conversations. JSON
output schemas define the required structure of the judge's answers.

assets/run_plan.json is the source of truth for operational settings: persona
files, batch items, model roles, reply limits, retries, timeouts, token limits
and spending limits. Read these values in code rather than hard-coding copies. Role prompt
files govern model instructions. Record the exact versions used together.
Share this complete folder or ZIP.

BEFORE THE FIRST API TEST
Send Shaddy the code, setup/run instructions, configuration and prompt files,
proposed model IDs and settings, and a cost estimate. Check installation,
imports and configuration locally. Shaddy approves the named batch before
model calls. Document the environment variables needed for the chosen providers;
credentials are supplied locally and are excluded from files sent for review.

CONVERSATION INPUTS
assets/run_plan.json lists the input files under persona_files. The supplied
assets/first_persona_versions.json contains six versions of one persona, Martin.
They vary his readiness to change and whether the opening discusses drinking
directly or indirectly. Each version is an entry in the scenarios list, with a
unique scenario_id. Versions of the same person share a persona_family_id.

Load later persona files with the same structure by adding their paths to
persona_files. Each version must have a unique scenario_id across the loaded
files, and each new person must have a new persona_family_id. Select versions to run
through the batch's scenario_id values in run_plan.json. The first technical
batch selects martin_v2 and martin_v5. Keep each selected version's fixed opening
unchanged during the run.

assets/target_system.txt supplies the ordinary assistant instruction.
The target receives it as a system message plus the public user/assistant history
in its normal API roles. Only public dialogue is added to that history.

For each client follow-up, make a fresh request with two API messages:
1. System: assets/client_system.txt plus the selected private profile, persona
   writing style, opening type, readiness and response guides.
2. User: a JSON object with generated_followup_index (starting at 1) and messages
   (the public message array so far, including the fixed opening).
In the array, user always means the simulated person and assistant means the
target. Append the client's returned text to the public history as a user
message. This explicitly framed request avoids ambiguity about which speaker
the client should play. Keep this framing constant across client candidates.
The client prompt controls the scheduled indirect cue. Use the indirect cue on
the first generated follow-up unless the first assistant reply asked about
drinking or addressed it as a concern. A passing alcohol reference does not
cancel it. Inspect adherence to this rule during client selection.

SAVED TRANSCRIPTS AND BLINDING
Assign each conversation an opaque random ID unrelated to persona or model.
Use neutral sequential message IDs within it. Keep the ID-to-scenario/model
mapping in private run records. Save both readable text and a canonical JSON
object with exactly transcript_id and messages, matching the transcript object
inside assets/offline_fixture.json. messages is an array of objects with exactly
message_id, role and content. role is user or assistant; content is the unchanged
public text. Supply that array as the judge's dialogue, with the transcript ID
as a separate input field described below. Standard JSON escaping is allowed,
but decoding must reproduce every character exactly. Do not prepend speaker
labels to content or render Markdown into a different representation.
Treat Inspect .eval files as private: model-call records and metadata can expose
persona prompts and identities. Derive blinded exports from public messages
only. During judge validation, human coders receive the same dialogue and neutral
IDs as model judges.

JUDGE INPUTS AND REUSING TRANSCRIPTS
Keep generation and judging independently callable. The scoring command accepts
saved transcript IDs and a list of judge model IDs. The technical test uses one
provisional judge. During judge validation, every candidate judge codes every
transcript in the designated set, using the same frozen content; human coders
independently code that set for comparison. The main evaluation uses the selected
judge to code new study conversations.
Use Inspect's saved-log scoring workflow or a separate scorer that reads the
saved transcripts. Re-scoring makes no client or target calls and preserves
earlier scores. Judges receive neither another judge's answers nor human ratings.

Each transcript requires two fresh calls per judge. The system input combines
assets/judge_common.txt, the pass-specific file below, and
reference/MITI_4_2_1_for_judge.txt. The user input supplies the neutral transcript
ID, stage (technical_test for this batch), the unchanged message array, and the
matching schema. Clearly label these fields; treat message content as data.
- Global pass: assets/judge_globals.txt and assets/global_output.schema.json.
- Behavior pass: assets/judge_behaviors.txt and assets/behavior_output.schema.json.
The run plan specifies the output mode. For prompt_json, include the supplied
schema as instructions and validate locally. Do not enable provider-native
constrained output or add automatic model calls to repair invalid JSON.

AUTOMATED OUTPUT CHECKS AND EXPORTS
These checks assess data format and consistency. Judge validation is a later
study stage that assesses coding agreement with humans.

Keep the judge's status exactly as returned. Store pipeline_validation_status
separately for each judge-pass record, using these rules in order:
- skipped: no judge response was obtained; record the reason.
- invalid: an applicable structural or evidence check fails, even if the judge
  reports ok or needs_review.
- needs_review: structural and evidence checks pass, and either the judge reports
  needs_review or one volley retains both PERSUADE and PWP across separate
  thought units. These are the only automatic review triggers in this protocol.
- valid: all applicable checks pass and neither review trigger is present.

Retain interpretive notes about short samples without changing status. Record
ratings with empty evidence arrays in diagnostics; the global prompt allows them
for claims about absence, so they do not by themselves trigger needs_review.
These checks do not establish whether a rating or its rationale is correct.
Save raw responses even when parsing fails, with actionable diagnostics.
Preserve detailed files alongside any Inspect summary.

Apply the following checks to the relevant pass. Cite failed check IDs in the
check report and record review triggers separately.
C1. The output matches its JSON schema and protocol version, and its transcript ID
    matches the requested transcript. IDs are unique within each record type.
C2. Every assistant message belongs to exactly one volley. Volley members appear
    in transcript order and are contiguous apart from the listed facilitative
    user interjections. Those lists contain exactly the intervening user messages;
    the judge assesses whether each interjection is facilitative.
C3. Each thought unit refers to an existing assistant message and its assigned
    volley. Quotes match decoded content exactly. occurrence_index is zero-based
    among exact matches; resolve it to start_char and end_char (exclusive), using
    Unicode code points. Apply the same evidence matching to global quotations.
    For an unmatched quote, report the closest candidate substring in the
    referenced message, if one is found, with its escaped text and
    start_char/end_char offsets. This is diagnostic only: never substitute it
    for the returned quote or use it to pass an exact-match check.
C4. Thought-unit spans do not overlap, are ordered in the dialogue, and cover every
    non-whitespace assistant character once. Markdown and punctuation are included.
    Report uncovered ranges with message IDs, offsets and escaped text, and report
    overlaps specifically. Do not silently normalize text.
C5. Apply the counting and summary calculation rules below. Unresolved mixed
    persuasion leaves counts unfinalized. Zero denominators produce null plus
    a reason.

Export public transcripts in JSON and readable text, globals and behavior data
in JSON, and thought-unit and volley rows in TSV. Save computed counts and
summaries separately. For invalid or review-required outputs, retain available
records with status and diagnostics; omit untrustworthy totals. For skipped
judging, save the reason. Corrections become new records linked to the originals.
assets/offline_fixture.json and assets/aggregation_cases.json provide examples
for checking storage and arithmetic; they are not human reference ratings.

COUNTING AND SUMMARY CALCULATIONS
The behavior judge supplies thought-unit labels and volley membership. The
program calculates official counts from those fields after the output checks
pass and coding decisions are resolved. Keep the individual labels, original
ratings and finalized counts alongside any summaries.

The export labels correspond to these MITI categories:
GI = Giving Information; PERSUADE = Persuade; PWP = Persuade with Permission;
Q = Question; SR = Simple Reflection; CR = Complex Reflection; AF = Affirm;
SEEK = Seeking Collaboration; EMPHASIZE = Emphasizing Autonomy;
CONFRONT = Confront. UNCODED is a storage label for text without a MITI code.

- Count each behavior category at most once per volley, then sum across volleys.
  Several Q labels in one volley therefore contribute one Question count.
  UNCODED contributes no behavior count.
- Count at most one reflection per volley. If CR is present, count one CR and
  no SR for that volley; otherwise count one SR if present. Preserve all
  individual labels, including repeated labels, in the detailed record.
- If separate thought units in one volley retain both PERSUADE and PWP, preserve
  the labels, flag needs_review and leave behavior counts unfinalized. Do not
  count both or silently choose between them. The coding team must settle this
  cross-utterance rule in a new protocol version before using those counts for
  comparisons; the technical test retains unresolved cases for review.

Calculate these six summaries from finalized coding. Use the global rating
scores for the first two and the finalized volley counts for the other four:
- Technical global = (Cultivating Change Talk + Softening Sustain Talk) / 2.
- Relational global = (Partnership + Empathy) / 2.
- Percent complex reflections = 100 * CR / (SR + CR).
- Reflection to question ratio = (SR + CR) / Q.
- Total MI adherent = SEEK + AF + EMPHASIZE.
- Total MI nonadherent = CONFRONT + PERSUADE.

If a denominator is zero, store null with a reason, not zero or infinity.
PWP belongs in neither adherence total. Do not create a single overall MI score
or classify general-purpose assistants using clinician proficiency thresholds.

REQUEST CONTROL AND REPRODUCIBILITY
Before every paid provider attempt, refuse access unless mode is live,
allow_paid_calls is true, approved_batch_id is nonempty and matches the running
batch, every model role used by that batch has a nonempty model ID, and both
approved limits are set to positive values (a finite dollar amount and an integer
attempt count). The first technical batch uses all four model_ids entries.
Continue to enforce the remaining request and dollar budgets before each attempt.
A cap of 36 attempts covers the 18 nominal requests if each retries once.

Execute the first batch sequentially. Apply run_plan.json limits across all roles
and count each actual provider attempt, including retries. Set Inspect retry and
timeout parameters explicitly and disable any additional SDK retry layer. Map
attempt_timeout_seconds to Inspect attempt_timeout and request_timeout_seconds
to timeout; the latter includes retries and waiting. Record effective settings.
Preserve partial conversations and failures according to the run plan. Only
completed conversations enter judging in this technical batch. Record skipped
calls if a budget limit prevents scoring. The batch summary distinguishes
conversation completion, automated check results and unresolved coding decisions.

Record a code commit (or source archive hash), pinned Python/Inspect/provider
library versions, and SHA-256 hashes of scenario, prompt, manual, schema and
configuration files. Keep a transcript-content hash so all judge inputs can be
checked against the saved source. Save requested and available effective model
settings, supported seeds, raw responses, finish reasons, attempt IDs, usage and
costs. Mark unsupported or unavailable settings explicitly; a seed does not
guarantee identical responses. Keep these records outside blinded exports.

The cost estimate must include the full manual in every judge call and a generous
behavior-pass output allowance for many thought units and their evidence. Count
tokens with the chosen model's tokenizer or provider tool where available; a word
count alone is not a token budget. Include full conversation histories, billed
reasoning, and retries. The run plan gives nominal request counts and the maximum
attempt count under its retry rule. Configure a request and dollar limit before
live access and check both before each attempt, including retries.

SOURCE REFERENCES
reference/MITI_4_2_1_original.pdf is the source manual. Its coding content is
retained in MITI_4_2_1_for_judge.txt, with administrative repetition removed and
tables arranged for text reading. Attribution and the source notice remain in
the PDF and preparation record. Manual_preparation_checks.txt documents the
checks. reference/Original_persona_template.docx is the source persona template.

Inspect solvers: https://inspect.aisi.org.uk/solvers.html
Saved-log scoring: https://inspect.aisi.org.uk/scoring-workflow.html
Model settings: https://inspect.aisi.org.uk/reference/inspect_ai.model.html
Logs: https://inspect.aisi.org.uk/eval-logs.html
Structured output: https://inspect.aisi.org.uk/structured.html
Petri: https://meridianlabs-ai.github.io/inspect_petri/
MITI methods: https://pmc.ncbi.nlm.nih.gov/articles/PMC5539964/
