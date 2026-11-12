"""Explicit reasoning settings per role, checked before paid calls and verified after each call.

The run plan sets `generation.reasoning_effort` per model role (e.g. medium for the
targets and the client, high for the judge). The same label is sent to every provider
through Inspect's `reasoning_effort`, which providers implement differently:

- OpenAI (Responses API): request field `reasoning.effort`
- Anthropic (Claude 4.6+): adaptive thinking, request fields `thinking` and `output_config.effort`

A label does not imply equal compute across providers. Before any paid call the code
checks that Inspect will send the requested effort for that model (no silent fallback to
a provider default). After each call it reads the request Inspect actually sent and
records the reasoning fields; a mismatch stops the conversation or judge pass.
"""

from __future__ import annotations

from typing import Any

from inspect_ai.model import GenerateConfig, get_model

from .budget import SDK_RETRY_DISABLE_ARGS, is_free, provider

EFFORTS = ("low", "medium", "high", "xhigh", "max")
PROVIDER_MAPPING = {
    "openai": "Responses API reasoning.effort",
    "anthropic": "adaptive thinking; output_config.effort",
}
REQUEST_KEYS = ("model", "reasoning", "thinking", "output_config", "max_output_tokens", "max_tokens",
                "max_completion_tokens")


def support_problem(model_id: str, effort: str | None) -> str | None:
    """Why this model can't be sent this effort explicitly (None = supported). Makes no network call."""
    if is_free(model_id):
        return None
    if effort not in EFFORTS:
        return f"{model_id}: reasoning_effort {effort!r} is not one of {EFFORTS}"
    try:
        api = get_model(model_id, **SDK_RETRY_DISABLE_ARGS.get(provider(model_id), {})).api
    except Exception as ex:  # usually a missing API key
        if "API_KEY" in str(ex):
            return f"{model_id}: no {provider(model_id).upper()}_API_KEY in the environment or .env"
        return f"{model_id}: cannot check reasoning support ({type(ex).__name__}: {str(ex).splitlines()[0]})"
    config = GenerateConfig(reasoning_effort=effort)  # type: ignore[arg-type]
    if provider(model_id) == "anthropic":
        if api.effort_from_reasoning_effort(config) != effort:  # type: ignore[attr-defined]
            return f"{model_id}: Inspect would not send adaptive thinking with effort {effort!r}"
        return None
    if provider(model_id) == "openai":
        if not (getattr(api, "responses_api", False) and api.has_reasoning_options()):  # type: ignore[attr-defined]
            return f"{model_id}: not a reasoning model on the Responses API; effort would be ignored"
        return None
    return f"{model_id}: reasoning support not verified for provider {provider(model_id)!r}"


def request_settings(request: dict[str, Any] | None) -> dict[str, Any] | None:
    """The reasoning and output-limit fields of the request Inspect sent to the provider."""
    if not isinstance(request, dict):
        return None
    return {k: request[k] for k in REQUEST_KEYS if k in request}


def applied(model_id: str, effort: str | None, sent: dict[str, Any] | None) -> bool | None:
    """Whether the sent request carries the requested effort (None = can't tell, e.g. offline model)."""
    if is_free(model_id) or effort is None or sent is None:
        return None
    if provider(model_id) == "openai":
        return (sent.get("reasoning") or {}).get("effort") == effort
    if provider(model_id) == "anthropic":
        return (sent.get("output_config") or {}).get("effort") == effort and \
            (sent.get("thinking") or {}).get("type") == "adaptive"
    return None
