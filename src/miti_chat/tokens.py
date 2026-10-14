"""Conservative input-token estimates for cost reservations and the cost estimate.

OpenAI models are counted with tiktoken's o200k_base encoding (+10%). Anthropic
has no offline tokenizer, so its counts use the same encoding +30%. If the
tiktoken encoding file can't be loaded, fall back to one token per 3 characters
(+30%). These numbers only size reservations and estimates; billed costs come
from provider-reported usage.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

PER_MESSAGE_OVERHEAD = 8


@lru_cache(maxsize=1)
def _encoding() -> Any | None:
    try:
        import tiktoken

        return tiktoken.get_encoding("o200k_base")
    except Exception:
        return None


def tokenizer_name() -> str:
    return "tiktoken o200k_base" if _encoding() is not None else "characters/3 fallback"


def raw_count(text: str) -> int:
    enc = _encoding()
    if enc is not None:
        return len(enc.encode(text, disallowed_special=()))
    return math.ceil(len(text) / 3)


def margin(model_id: str) -> float:
    if _encoding() is not None and model_id.startswith("openai/"):
        return 1.1
    return 1.3


def estimate(texts: list[str], model_id: str) -> int:
    """Estimated input tokens for a request made of these message texts."""
    n = sum(raw_count(t) + PER_MESSAGE_OVERHEAD for t in texts)
    return math.ceil(n * margin(model_id))
