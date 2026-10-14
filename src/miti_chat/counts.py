"""Official MITI counts and summary scores from thought-unit labels (README rules).

- Each behavior code counts at most once per volley; counts are summed across volleys.
- At most one reflection per volley: CR if present, otherwise SR if present.
- UNCODED contributes nothing.
- PERSUADE and PWP retained in one volley (separate thought units) -> needs_review;
  counts stay unfinalized until the coding team sets the cross-utterance rule.
- Zero denominators give null plus a reason, never 0 or infinity.
- PWP is in neither adherence total. No single overall MI score is produced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CODES = ("GI", "PERSUADE", "PWP", "Q", "SR", "CR", "AF", "SEEK", "EMPHASIZE", "CONFRONT")
STORAGE_ONLY = ("UNCODED",)
GLOBAL_NAMES = ("cultivating_change_talk", "softening_sustain_talk", "partnership", "empathy")

MIXED_PERSUASION = "mixed_persuasion_in_volley"


@dataclass
class VolleyCounts:
    counts: dict[str, int] | None          # None when not finalized
    counted_by_volley: list[list[str]]     # codes that count, per volley (for audit)
    review_triggers: list[dict[str, Any]] = field(default_factory=list)

    @property
    def finalized(self) -> bool:
        return self.counts is not None


def counted_codes(labels: list[str]) -> list[str]:
    """The codes one volley contributes, in CODES order."""
    present = set(labels)
    if "CR" in present:
        present.discard("SR")
    return [c for c in CODES if c in present]


def count_volleys(volleys: list[list[str]], volley_ids: list[str] | None = None) -> VolleyCounts:
    """Apply the per-volley counting rules to each volley's list of thought-unit labels."""
    unknown = {lab for v in volleys for lab in v} - set(CODES) - set(STORAGE_ONLY)
    if unknown:
        raise ValueError(f"unknown codes: {sorted(unknown)}")
    ids = volley_ids or [f"volley_{i + 1}" for i in range(len(volleys))]
    counted = [counted_codes(v) for v in volleys]
    triggers = [
        {"trigger": MIXED_PERSUASION, "volley_id": vid,
         "detail": "PERSUADE and PWP retained in separate thought units of one volley"}
        for vid, v in zip(ids, volleys)
        if "PERSUADE" in v and "PWP" in v
    ]
    if triggers:
        return VolleyCounts(counts=None, counted_by_volley=counted, review_triggers=triggers)
    totals = {c: 0 for c in CODES}
    for codes in counted:
        for c in codes:
            totals[c] += 1
    return VolleyCounts(counts=totals, counted_by_volley=counted)


def _value(v: float | int | None, reason: str | None = None) -> dict[str, Any]:
    return {"value": v, "reason": reason}


def behavior_summaries(counts: dict[str, int]) -> dict[str, dict[str, Any]]:
    sr, cr, q = counts["SR"], counts["CR"], counts["Q"]
    return {
        "percent_complex_reflections": _value(100 * cr / (sr + cr)) if sr + cr
        else _value(None, "no reflections (SR + CR = 0)"),
        "reflection_question_ratio": _value((sr + cr) / q) if q else _value(None, "no questions (Q = 0)"),
        "total_mi_adherent": _value(counts["SEEK"] + counts["AF"] + counts["EMPHASIZE"]),
        "total_mi_nonadherent": _value(counts["CONFRONT"] + counts["PERSUADE"]),
    }


def global_summaries(scores: dict[str, int]) -> dict[str, dict[str, Any]]:
    return {
        "technical_global": _value((scores["cultivating_change_talk"] + scores["softening_sustain_talk"]) / 2),
        "relational_global": _value((scores["partnership"] + scores["empathy"]) / 2),
    }


def not_finalized(names: tuple[str, ...], reason: str) -> dict[str, dict[str, Any]]:
    return {n: _value(None, reason) for n in names}


BEHAVIOR_SUMMARY_NAMES = ("percent_complex_reflections", "reflection_question_ratio",
                          "total_mi_adherent", "total_mi_nonadherent")
GLOBAL_SUMMARY_NAMES = ("technical_global", "relational_global")
