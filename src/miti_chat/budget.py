"""Live-access gate and the request/cost ledger.

Every provider attempt (including retries) goes through `Ledger.reserve`, which
refuses the attempt unless the run plan's live-access conditions hold and the
approved request and dollar limits leave room for a conservative cost bound.
Offline models (mockllm/...) cost nothing and are always allowed, so the whole
pipeline can be exercised before approval.

The ledger is one JSON file per batch, shared by `generate` and `score`, so the
approved limits apply across conversation generation and judging together.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Batch, Protocol

FREE_PREFIXES = ("mockllm/",)

# Providers whose SDK retry layer we can switch off (max_retries=0 on the SDK
# client). Live calls to other providers are refused so nested retries can
# never exceed the run plan's attempt limit. Extend after checking the provider.
SDK_RETRY_DISABLE_ARGS: dict[str, dict[str, Any]] = {
    "openai": {"max_retries": 0},
    "anthropic": {"max_retries": 0},
}


class AccessRefused(Exception):
    """A model attempt was refused before it was sent."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def is_free(model_id: str) -> bool:
    return model_id.startswith(FREE_PREFIXES)


def provider(model_id: str) -> str:
    return model_id.split("/", 1)[0]


def _positive_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def _positive_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x > 0


def live_gate_problems(protocol: Protocol, batch: Batch, model_ids: list[str] | None = None) -> list[str]:
    """Unmet conditions for paid calls in this batch (empty list = live access allowed).

    These are the run plan's live_access_gate conditions, plus three this code
    needs to keep its promises: per-role response token limits (for the cost
    bound), a price for every paid model, and a provider whose SDK retries can
    be disabled.
    """
    plan = protocol.plan
    problems: list[str] = []
    if plan.get("mode") != "live":
        problems.append(f"mode is {plan.get('mode')!r}, not 'live'")
    if plan.get("allow_paid_calls") is not True:
        problems.append("allow_paid_calls is not true")
    approved = plan.get("approved_batch_id")
    if not approved:
        problems.append("approved_batch_id is empty")
    elif approved != batch.batch_id:
        problems.append(f"approved_batch_id {approved!r} does not match running batch {batch.batch_id!r}")
    for role in batch.roles:
        if not protocol.model_ids.get(role):
            problems.append(f"model_ids.{role} is empty")
    if not _positive_number(plan.get("approved_max_cost_usd")):
        problems.append("approved_max_cost_usd is not a finite positive number")
    if not _positive_int(plan.get("approved_max_total_requests")):
        problems.append("approved_max_total_requests is not a positive integer")
    for key in ("target", "client", "judge_globals", "judge_behaviors"):
        if not _positive_int(protocol.token_limit(key)):
            problems.append(f"generation.response_token_limits.{key} is not set")
    ids = list(model_ids or []) + [protocol.model_ids.get(r) for r in batch.roles]
    for m in dict.fromkeys(i for i in ids if i):
        if is_free(m):
            continue
        if provider(m) not in SDK_RETRY_DISABLE_ARGS:
            problems.append(f"{m}: provider SDK retry handling not verified in this code")
        price = protocol.pricing.get(m) or {}
        if not (_positive_number(price.get("input_usd_per_mtok")) and _positive_number(price.get("output_usd_per_mtok"))):
            problems.append(f"{m}: no price in {protocol.pricing_file}")
    return problems


def attempt_cost(protocol: Protocol, model_id: str, input_tokens: float, output_tokens: float) -> float:
    if is_free(model_id):
        return 0.0
    p = protocol.pricing[model_id]
    return (input_tokens * p["input_usd_per_mtok"] + output_tokens * p["output_usd_per_mtok"]) / 1e6


def usage_cost(protocol: Protocol, model_id: str, usage: dict[str, Any] | None) -> float | None:
    """Cost of a completed attempt from provider-reported usage (None if usage is missing).

    Cache writes are priced at 1.25x input and cache reads at the full input
    price, which is conservative for both providers. Reasoning tokens are
    already included in output_tokens by Inspect for OpenAI and Anthropic.
    """
    if is_free(model_id):
        return 0.0
    if not usage:
        return None
    inp = (usage.get("input_tokens") or 0) + 1.25 * (usage.get("input_tokens_cache_write") or 0) \
        + (usage.get("input_tokens_cache_read") or 0)
    return attempt_cost(protocol, model_id, inp, usage.get("output_tokens") or 0)


class Ledger:
    """Append-only record of every provider attempt for one batch.

    The file is re-read under a lock before every change, so separate processes
    (e.g. `generate` and `score`) share one count of attempts and costs.
    """

    def __init__(self, path: Path, protocol: Protocol, batch: Batch) -> None:
        self.path = path
        self.protocol = protocol
        self.batch = batch
        self.attempts: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if self.path.is_file():
            self.attempts = json.loads(self.path.read_text(encoding="utf-8"))["attempts"]

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_suffix(".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                self._load()
                yield
                self._save()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    # ---- totals --------------------------------------------------------
    def paid_attempts(self) -> int:
        return sum(1 for a in self.attempts if not is_free(a["model"]))

    def committed_cost(self) -> float:
        """Known costs plus conservative reservations for attempts with uncertain charges."""
        total = 0.0
        for a in self.attempts:
            c = a.get("cost_usd")
            total += a["reserved_cost_usd"] if c is None else c
        return total

    # ---- attempt lifecycle --------------------------------------------
    def reserve(self, *, role: str, model: str, purpose: str, request_id: str, attempt_number: int,
                est_input_tokens: int, max_output_tokens: int | None, settings: dict[str, Any]) -> dict[str, Any]:
        """Check the gate and limits, then record the attempt as started. Raises AccessRefused."""
        with self._locked():
            return self._reserve(role=role, model=model, purpose=purpose, request_id=request_id,
                                 attempt_number=attempt_number, est_input_tokens=est_input_tokens,
                                 max_output_tokens=max_output_tokens, settings=settings)

    def _reserve(self, *, role: str, model: str, purpose: str, request_id: str, attempt_number: int,
                 est_input_tokens: int, max_output_tokens: int | None, settings: dict[str, Any]) -> dict[str, Any]:
        reserved = 0.0
        if not is_free(model):
            problems = live_gate_problems(self.protocol, self.batch, [model])
            if problems:
                raise AccessRefused("live_access_refused", "; ".join(problems))
            plan = self.protocol.plan
            if self.paid_attempts() + 1 > plan["approved_max_total_requests"]:
                raise AccessRefused("request_limit_reached",
                                    f"{self.paid_attempts()} of {plan['approved_max_total_requests']} attempts used")
            reserved = attempt_cost(self.protocol, model, est_input_tokens, max_output_tokens or 0)
            if self.committed_cost() + reserved > plan["approved_max_cost_usd"]:
                raise AccessRefused("cost_limit_reached",
                                    f"committed ${self.committed_cost():.4f} + reservation ${reserved:.4f} "
                                    f"> approved ${plan['approved_max_cost_usd']}")
        attempt = {
            "attempt_id": f"att_{uuid.uuid4().hex[:12]}",
            "request_id": request_id,
            "attempt_number": attempt_number,
            "batch_id": self.batch.batch_id,
            "role": role,
            "model": model,
            "purpose": purpose,
            "requested_settings": settings,
            "est_input_tokens": est_input_tokens,
            "reserved_cost_usd": reserved,
            "started_at": now(),
            "finished_at": None,
            "outcome": "started",
            "cost_usd": None,
        }
        self.attempts.append(attempt)
        return dict(attempt)

    def finish(self, attempt: dict[str, Any], **fields: Any) -> None:
        """Record the attempt's outcome. A missing cost keeps the conservative reservation."""
        with self._locked():
            stored = next(a for a in self.attempts if a["attempt_id"] == attempt["attempt_id"])
            stored.update(fields)
            stored["finished_at"] = now()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"batch_id": self.batch.batch_id, "attempts": self.attempts},
                                  indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, self.path)
