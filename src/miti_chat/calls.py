"""One logical model request = up to (transport_retries_max + 1) gated attempts.

Retries are run here rather than inside Inspect so that every attempt is
checked against the live-access gate and the request/dollar limits *before*
it is sent, and logged. Inspect's own retry is set to 0 (`max_retries=0`) and
the provider SDK's retry layer is switched off (see budget.SDK_RETRY_DISABLE_ARGS),
so nested retries cannot add attempts. `attempt_timeout_seconds` maps to
Inspect's `attempt_timeout`; `request_timeout_seconds` bounds the whole request
including retries and waiting, and is passed to Inspect as `timeout`.
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from inspect_ai.model import ChatMessage, GenerateConfig, Model, ModelOutput, get_model
from inspect_ai.model._model import AttemptTimeoutError, StreamIdleTimeoutError

from . import tokens
from .budget import SDK_RETRY_DISABLE_ARGS, AccessRefused, Ledger, is_free, provider, usage_cost

TRUNCATED_STOP_REASONS = {"max_tokens", "model_length"}
RETRY_WAIT_SECONDS = 5.0

# Offline stand-in used by `miti dry-run` and the tests (set by offline.py).
OFFLINE_RESPONDER: dict[str, Callable[..., ModelOutput]] = {}


@dataclass
class CallResult:
    request_id: str
    ok: bool
    text: str = ""
    stop_reason: str | None = None
    served_model: str | None = None
    usage: dict[str, Any] | None = None
    failure: str | None = None      # reason code when ok is False
    error: str | None = None
    attempt_ids: list[str] = field(default_factory=list)

    @property
    def truncated(self) -> bool:
        return self.stop_reason in TRUNCATED_STOP_REASONS

    @property
    def empty(self) -> bool:
        return not self.text.strip()


def model_args(model_id: str) -> dict[str, Any]:
    if is_free(model_id):
        return {"custom_outputs": OFFLINE_RESPONDER["fn"]} if "fn" in OFFLINE_RESPONDER else {}
    return dict(SDK_RETRY_DISABLE_ARGS.get(provider(model_id), {}))


def _model(model_id: str) -> Model:
    return get_model(model_id, **model_args(model_id))


def _unwrap(ex: BaseException) -> BaseException:
    """Inspect wraps the provider exception in tenacity.RetryError."""
    last = getattr(ex, "last_attempt", None)
    if last is not None:
        inner = last.exception()
        if inner is not None:
            return inner
    return ex


def _is_transient(model: Model, ex: BaseException) -> bool:
    if isinstance(ex, (TimeoutError, asyncio.TimeoutError, AttemptTimeoutError, StreamIdleTimeoutError)):
        return True
    try:
        return bool(model.api.should_retry(ex))  # provider's own transient/rate-limit classification
    except Exception:
        return False


def _status_code(ex: BaseException) -> int | None:
    code = getattr(ex, "status_code", None)
    return code if isinstance(code, int) else None


def _usage_dict(output: ModelOutput) -> dict[str, Any] | None:
    return output.usage.model_dump(exclude_none=True) if output.usage else None


def _seed_note(model_id: str) -> str:
    return {
        "openai": "seed supported by provider; not requested (provider defaults)",
        "anthropic": "seed not supported by provider",
        "mockllm": "offline model; deterministic",
    }.get(provider(model_id), "seed support unknown; not requested")


async def call_model(*, ledger: Ledger, role: str, model_id: str, messages: list[ChatMessage],
                     max_tokens: int | None, purpose: str) -> CallResult:
    """Send one logical request with gated, logged retries. Never raises for provider errors."""
    ex_cfg = ledger.protocol.execution
    max_attempts = int(ex_cfg["transport_retries_max"]) + 1
    attempt_timeout = int(ex_cfg["attempt_timeout_seconds"])
    request_timeout = int(ex_cfg["request_timeout_seconds"])

    result = CallResult(request_id=f"req_{uuid.uuid4().hex[:12]}", ok=False)
    model = _model(model_id)
    est_input = tokens.estimate([m.text for m in messages], model_id)
    deadline = time.monotonic() + request_timeout

    for attempt_number in range(1, max_attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            result.failure = "request_timeout"
            return result
        config = GenerateConfig(
            max_tokens=max_tokens,
            max_retries=0,
            timeout=math.ceil(remaining),
            attempt_timeout=min(attempt_timeout, math.ceil(remaining)),
            cache_prompt=False,  # explicit: no provider prompt caching (simpler, predictable billing)
            # For OpenAI, an unset reasoning_summary makes Inspect send a one-off probe request
            # that would bypass the ledger; setting it explicitly prevents that.
            reasoning_summary="none" if provider(model_id) == "openai" else None,
        )
        settings = {
            **config.model_dump(exclude_none=True),
            "sampling": "provider defaults (temperature, top_p not set)",
            "seed": _seed_note(model_id),
            "sdk_client_args": {k: v for k, v in model_args(model_id).items() if k != "custom_outputs"},
        }
        try:
            attempt = ledger.reserve(role=role, model=model_id, purpose=purpose, request_id=result.request_id,
                                     attempt_number=attempt_number, est_input_tokens=est_input,
                                     max_output_tokens=max_tokens, settings=settings)
        except AccessRefused as refused:
            previous = f" (previous attempt: {result.error})" if result.error else ""
            result.failure, result.error = refused.code, refused.detail + previous
            return result
        result.attempt_ids.append(attempt["attempt_id"])

        try:
            output = await model.generate(messages, config=config)
        except (asyncio.CancelledError, KeyboardInterrupt):
            ledger.finish(attempt, outcome="cancelled")  # cost stays at the reservation
            raise
        except Exception as raw_ex:
            ex = _unwrap(raw_ex)
            transient = _is_transient(model, ex)
            status = _status_code(ex)
            # A 4xx rejection is not billed; anything else may have been, so keep the reservation.
            ledger.finish(attempt, outcome="error", error=f"{type(ex).__name__}: {ex}"[:2000],
                          error_status=status, transient=transient,
                          cost_usd=0.0 if status is not None and 400 <= status < 500 else None)
            result.error = f"{type(ex).__name__}: {ex}"[:2000]
            if transient and attempt_number < max_attempts:
                await asyncio.sleep(max(0.0, min(RETRY_WAIT_SECONDS, deadline - time.monotonic() - 1)))
                continue
            result.failure = "request_failed"
            return result

        usage = _usage_dict(output)
        ledger.finish(
            attempt,
            outcome="ok",
            served_model=output.model,
            stop_reason=output.stop_reason,
            usage=usage,
            cost_usd=usage_cost(ledger.protocol, model_id, usage),
            output_metadata=output.metadata,
            response_text=output.completion,
        )
        result.ok = True
        result.text = output.completion
        result.stop_reason = output.stop_reason
        result.served_model = output.model
        result.usage = usage
        return result

    result.failure = result.failure or "request_failed"
    return result
