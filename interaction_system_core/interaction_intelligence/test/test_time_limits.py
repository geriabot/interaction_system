# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The bounded wait for the model (CallLimits): each call waits at most llm_timeout; a call that
times out is not repeated and the other calls of the same skill fail at once; once the time
budget of the reply is spent, no call is made and the questions fall back. Calls that fail fast
(an error) or answer something invalid are still asked again while there is time."""

import os
import time

import yaml

from interaction_intelligence.llm_client import CallLimits, LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")


class Clock:
    """A clock the test moves by hand."""

    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def builder():
    with open(_PROMPTS) as f:
        data = yaml.safe_load(f)
    return PromptBuilder(data["interaction_intelligence"]["ros__parameters"]["prompts"])


def client(answers, limits, clock=None, took=0.0):
    """A client whose model gives these answers in turn (None: no answer); each call takes
    `took` seconds of the clock, or its whole wait when it returns nothing (a timeout)."""
    calls = []

    def infer(session, prompt, max_tokens, schema):
        calls.append(limits.wait)
        answer = answers[min(len(calls), len(answers)) - 1]
        if clock is not None:
            clock.now += (limits.wait or 0.0) if answer is None else took
        return answer

    return LLMDialogClient("nlu", "nlg", infer, builder(), limits=limits), calls


# ==== THE LIMITS ====


def test_the_wait_of_a_call_is_the_timeout_or_what_is_left_of_the_budget():
    clock = Clock()
    assert CallLimits(clock=clock).next_wait() is None
    assert CallLimits(15.0, clock=clock).next_wait() == 15.0
    limits = CallLimits(15.0, 20.0, clock=clock)
    assert limits.next_wait() == 15.0
    clock.now += 12.0
    assert limits.next_wait() == 8.0
    assert limits.refusal() == ""
    clock.now += 7.8
    assert "budget" in limits.refusal()  # less than MIN_CALL_S left


def test_a_call_that_returns_nothing_at_its_limit_timed_out():
    clock = Clock()
    limits = CallLimits(15.0, clock=clock)
    limits.done(clock.now - 1.0, 15.0, answered=False)  # a fast failure: not a timeout
    assert not limits.unresponsive
    limits.done(clock.now - 15.0, 15.0, answered=False)
    assert limits.unresponsive and limits.timeouts == 1
    assert "did not answer" in limits.refusal()


# ==== THE CLIENT ====


def test_a_timed_out_understanding_question_is_not_asked_again():
    clock = Clock()
    limits = CallLimits(15.0, 20.0, clock=clock)
    c, calls = client([None], limits, clock)
    # Its fallback (decided 2026-10-30): an exact yes word is "yes", anything else "other"
    assert c.classify_cancel_reply("Yes, but first the size") == "other"
    assert calls == [15.0]  # one call, not two
    assert (limits.timeouts, limits.skipped) == (1, 1)


def test_after_a_timeout_the_other_questions_of_the_skill_fall_back_without_a_call():
    clock = Clock()
    limits = CallLimits(15.0, 20.0, clock=clock)
    c, calls = client([None, '{"cancel": true}'], limits, clock)
    assert c.detect_cancel_intent("I'd rather leave it") is False
    assert c.ask_cancel_confirmation("Book a taxi") == c.phrase("cancel_ask")
    assert len(calls) == 1


def test_a_hung_model_costs_at_most_the_budget():
    # The reply's budget (20 s) is shorter than two timeouts (15 s each): a new skill of the same
    # reply waits only for what is left, and the next one makes no call
    clock = Clock()
    deadline_budget = 20.0
    first = CallLimits(15.0, deadline_budget, clock=clock)
    c, calls = client([None], first, clock)
    c.classify_step_turn("done", "Sit down", "", [])
    second = CallLimits(15.0, deadline_budget - 15.0, clock=clock)
    c, more = client([None], second, clock)
    assert c.procedure_remind("Sit down") == c.phrase("procedure_remind")
    third = CallLimits(15.0, 0.001, clock=clock)
    c, none = client([None], third, clock)
    assert c.roleplay_reply({"scenario": "s", "role": "r", "user_goal": "", "criteria": []},
                            []) == c.phrase("roleplay_reply")
    assert (calls, more, none) == ([15.0], [5.0], [])
    assert clock.now - 100.0 == 20.0


def test_an_invalid_answer_is_asked_again_while_there_is_time():
    clock = Clock()
    limits = CallLimits(15.0, 20.0, clock=clock)
    c, calls = client(["not json", '{"answer": "yes"}'], limits, clock, took=1.0)
    assert c.classify_cancel_reply("yes") == "yes"
    assert len(calls) == 2
    # Without time left, an invalid answer is not asked again
    clock = Clock()
    limits = CallLimits(15.0, 1.2, clock=clock)
    c, calls = client(["not json", '{"answer": "yes"}'], limits, clock, took=1.0)
    assert c.classify_cancel_reply("I suppose so") == "other"
    assert len(calls) == 1 and limits.skipped == 1


def test_a_failed_call_that_is_not_a_timeout_is_asked_again():
    clock = Clock()
    limits = CallLimits(15.0, 20.0, clock=clock)
    c, calls = client([None, '{"answer": "no"}'], limits)  # the clock does not move: an error
    assert c.classify_cancel_reply("no") == "no"
    assert len(calls) == 2 and limits.timeouts == 0


def test_a_short_generated_line_is_asked_again_and_a_timeout_is_not():
    limits = CallLimits(15.0, 20.0, clock=Clock())
    c, calls = client(["", "A line long enough."], limits)
    assert c.ask_cancel_confirmation("Book a taxi") == "A line long enough."
    assert len(calls) == 2
    clock = Clock()
    limits = CallLimits(15.0, 20.0, clock=clock)
    c, calls = client([None, "A line long enough."], limits, clock)
    assert c.ask_cancel_confirmation("Book a taxi") == c.phrase("cancel_ask")
    assert len(calls) == 1


def test_without_limits_the_client_waits_as_long_as_the_model_takes():
    c, calls = client(['{"answer": "yes"}'], CallLimits())
    assert c.classify_cancel_reply("yes") == "yes"
    assert calls == [None]


def test_the_wait_is_real_time_by_default():
    limits = CallLimits(15.0, 20.0)
    assert 19.0 < limits.time_left() <= 20.0
    assert limits.deadline > time.monotonic()


# ==== THE ROLE PLAY ====

SCENE = {"scenario": "A shop", "role": "The assistant", "user_goal": "",
         "criteria": ["Explains the fault"]}


def test_a_roleplay_turn_the_model_cannot_read_is_unclear_not_a_line_of_the_scene():
    for answers in ([None], ["not json"], ['{"intent": "dance"}'], ["{}"]):
        c, calls = client(answers, CallLimits())
        assert c.classify_roleplay_turn("It does not heat.", SCENE, []) == "unclear"
        assert len(calls) == c.MAX_RETRIES
    c, _ = client(['{"intent": "continue"}'], CallLimits())
    assert c.classify_roleplay_turn("It does not heat.", SCENE, []) == "continue"


# ==== SELECTIVE REASONING ====


def reasoning_client(answers, limits, clock, took=0.5):
    """A client that reasons before the critical questions; a call that returns nothing takes
    its whole wait (a timeout), the others `took` seconds."""
    calls = []

    def infer(session, prompt, max_tokens, schema, think=False):
        calls.append((think, limits.wait))
        answer = answers[min(len(calls), len(answers)) - 1]
        clock.now += (limits.wait or 0.0) if answer is None else took
        return answer

    return LLMDialogClient("nlu", "nlg", infer, builder(), limits=limits,
                           reasoning="critical"), calls


MATERIAL = [{"name": "information", "content": "Open at 10."}]


def test_a_long_reasoning_is_not_cut():
    # Author, 2026-11-02: reasoning runs to its end and the latencies are measured. A call that
    # reasons waits only for reasoning_timeout (a hung model), not for llm_timeout nor the budget
    clock = Clock()
    limits = CallLimits(15.0, 20.0, clock=clock, reasoning_timeout=120.0)
    c, calls = reasoning_client(['{"supported": true}'], limits, clock, took=40.0)
    assert c.verify_answer("It opens at 10.", MATERIAL)
    assert calls == [(True, 120.0)]
    assert (limits.timeouts, limits.unresponsive) == (0, False)


def test_the_time_of_a_reasoning_call_is_not_charged_to_the_budget():
    clock = Clock()
    limits = CallLimits(15.0, 20.0, clock=clock, reasoning_timeout=120.0)
    c, calls = reasoning_client(['{"supported": true}'], limits, clock, took=40.0)
    c.verify_answer("It opens at 10.", MATERIAL)
    assert limits.refusal() == "" and 19.0 < limits.time_left() <= 20.0
    assert limits.next_wait() == 15.0


def test_a_reasoning_call_is_made_even_when_the_budget_is_spent():
    clock = Clock()
    limits = CallLimits(15.0, 0.001, clock=clock, reasoning_timeout=120.0)
    assert "budget" in limits.refusal() and limits.refusal(think=True) == ""


def test_a_hung_model_that_may_reason_is_unresponsive_after_the_reasoning_timeout():
    clock = Clock()
    limits = CallLimits(15.0, 20.0, clock=clock, reasoning_timeout=120.0)
    c, calls = reasoning_client([None], limits, clock)
    assert not c.verify_answer("It opens at 10.", MATERIAL)
    # Not asked again without reasoning: a hung model costs one wait
    assert calls == [(True, 120.0)] and clock.now - 100.0 == 120.0
    assert limits.unresponsive and limits.timeouts == 1
    assert limits.refusal()  # the other questions of the skill make no call


def test_an_empty_answer_with_reasoning_is_asked_again_without_it():
    # Past its token allowance the model answers empty at once: the same question without
    # reasoning, within the usual limits
    clock = Clock()
    limits = CallLimits(15.0, 20.0, clock=clock, reasoning_timeout=120.0)
    c, calls = reasoning_client(["", '{"supported": true}'], limits, clock, took=3.0)
    assert c.verify_answer("It opens at 10.", MATERIAL)
    assert calls == [(True, 120.0), (False, 15.0)]


def test_a_call_without_reasoning_does_not_use_the_reasoning_timeout():
    clock = Clock()
    limits = CallLimits(15.0, clock=clock, reasoning_timeout=120.0)
    assert limits.next_wait() == 15.0 and limits.next_wait(think=True) == 120.0
    assert CallLimits(15.0, clock=clock).next_wait(think=True) is None
