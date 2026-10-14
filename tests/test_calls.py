"""call_model: retries, attempt counting and request settings (offline model, no network)."""

import asyncio

from inspect_ai.model import ChatMessageUser, ModelOutput, ModelUsage
from inspect_ai.model._model import AttemptTimeoutError

from miti_chat import offline
from miti_chat.budget import Ledger
from miti_chat.calls import OFFLINE_RESPONDER, call_model


def _run(protocol, tmp_path, responder):
    OFFLINE_RESPONDER["fn"] = responder
    ledger = Ledger(tmp_path / "ledger.json", protocol, protocol.batch("technical_check_01"))
    try:
        result = asyncio.run(call_model(ledger=ledger, role="target", model_id=offline.OFFLINE_MODEL,
                                        messages=[ChatMessageUser(content="hi")], max_tokens=50, purpose="t"))
    finally:
        offline.install()
    return result, ledger


def _ok():
    out = ModelOutput.from_content(offline.OFFLINE_MODEL, "hello")
    out.usage = ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2)
    return out


def test_attempt_timeout_is_retried_once(protocol, tmp_path, monkeypatch):
    monkeypatch.setattr("miti_chat.calls.RETRY_WAIT_SECONDS", 0)
    calls = []

    def flaky(*args):
        calls.append(1)
        if len(calls) == 1:
            raise AttemptTimeoutError("attempt timed out")
        return _ok()

    result, ledger = _run(protocol, tmp_path, flaky)
    assert result.ok and result.text == "hello" and len(result.attempt_ids) == 2
    assert [a["outcome"] for a in ledger.attempts] == ["error", "ok"]


def test_at_most_two_attempts(protocol, tmp_path, monkeypatch):
    monkeypatch.setattr("miti_chat.calls.RETRY_WAIT_SECONDS", 0)

    def always_timeout(*args):
        raise AttemptTimeoutError("attempt timed out")

    result, ledger = _run(protocol, tmp_path, always_timeout)
    assert not result.ok and result.failure == "request_failed" and len(ledger.attempts) == 2


def test_non_transient_error_not_retried(protocol, tmp_path):
    def broken(*args):
        raise ValueError("bad request")

    result, ledger = _run(protocol, tmp_path, broken)
    assert not result.ok and len(ledger.attempts) == 1 and ledger.attempts[0]["transient"] is False


def test_requested_settings_recorded(protocol, tmp_path):
    result, ledger = _run(protocol, tmp_path, lambda *a: _ok())
    s = ledger.attempts[0]["requested_settings"]
    assert s["max_retries"] == 0 and s["attempt_timeout"] == 120 and s["timeout"] == 300
    assert s["max_tokens"] == 50 and s["cache_prompt"] is False
