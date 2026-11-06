# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""A question the model could not answer is published on /llm/error (the GUI's Monitor shows
it), and only then: not for an answer, a decline, or a dialogue that has ended."""

from types import SimpleNamespace

import pytest

from interaction_intelligence import intelligence_node as node_module
from interaction_intelligence.intelligence_node import FAILURE_TYPES, InteractionIntelligence


class _Logger:
    def error(self, *_):
        pass

    def warning(self, *_):
        pass


class _Publisher:
    def __init__(self):
        self.sent = []

    def publish(self, msg):
        self.sent.append(msg)


def _node(available=True, response=None, finished=()):
    """The parts of the node that _interpret_sync uses, with the server's answer given."""
    stub = SimpleNamespace(
        _interpret=SimpleNamespace(wait_for_service=lambda timeout_sec: available),
        _call_sync=lambda *a, **k: response,
        _finished=set(finished),
        _error_pub=_Publisher(),
        get_logger=lambda: _Logger(),
    )
    stub._report_error = lambda *a: InteractionIntelligence._report_error(stub, *a)
    return stub


def _ask(stub, monkeypatch):
    monkeypatch.setattr(node_module, "interpret_request",
                        lambda *a, **k: {"question": "extract_turn"})
    return InteractionIntelligence._interpret_sync(stub, "d1", "a prompt", 100, timeout=1.0)


def _response(success=True, source="llm", reason="", answer='{"text": "ok"}', message=""):
    return SimpleNamespace(success=success, source=source, fallback_reason=reason,
                           answer=answer, message=message)


def test_server_not_available_is_reported(monkeypatch):
    stub = _node(available=False)
    assert _ask(stub, monkeypatch) is None
    (msg,) = stub._error_pub.sent
    assert (msg.error_type, msg.session_id, msg.provider) == ("connection", "d1", "interpretation")


def test_no_answer_in_time_is_reported(monkeypatch):
    stub = _node(response=None)
    assert _ask(stub, monkeypatch) is None
    assert [m.error_type for m in stub._error_pub.sent] == ["timeout"]


def test_a_finished_dialogue_is_not_reported(monkeypatch):
    stub = _node(response=None, finished=("d1",))
    assert _ask(stub, monkeypatch) is None
    assert stub._error_pub.sent == []


def test_malformed_request_is_reported(monkeypatch):
    stub = _node(response=_response(success=False, source="invalid", answer=""))
    assert _ask(stub, monkeypatch) is None
    assert [m.error_type for m in stub._error_pub.sent] == ["api"]


@pytest.mark.parametrize("reason", sorted(FAILURE_TYPES))
def test_fallback_after_a_failure_is_reported(monkeypatch, reason):
    stub = _node(response=_response(source="fallback", reason=reason))
    _ask(stub, monkeypatch)
    assert [m.error_type for m in stub._error_pub.sent] == [FAILURE_TYPES[reason]]


@pytest.mark.parametrize("reason", ["declined", "cancelled"])
def test_decline_or_cancel_is_not_reported(monkeypatch, reason):
    stub = _node(response=_response(source="fallback", reason=reason))
    _ask(stub, monkeypatch)
    assert stub._error_pub.sent == []


def test_an_answer_is_returned_and_not_reported(monkeypatch):
    stub = _node(response=_response())
    assert _ask(stub, monkeypatch) == "ok"
    assert stub._error_pub.sent == []
