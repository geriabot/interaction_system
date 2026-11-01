# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Whole dialogues through the interaction system while its model fails, with the scripted LLM
behind the injector of model failures (llm_faults.py, llm_client_factory
"interaction_sim.llm_faults:make_scripted"): each test plans its failures by user turn, as an E3
scenario does (`faults`), and checks the specification of the shared mechanisms and of the
strategies when the model fails (docs/behaviors: the "LLM fails" rows, endings.md): a failed
cancel detection is no cancellation, a failed reading of the confirmation withdraws it unless
the answer is exactly a yes or no word (the fallback, decided 2026-10-30), an exact stop command
with the model known down ends at once (decided 2026-10-30), a
failed skill or an intelligence that does not answer within intelligence_timeout ends with the
error closing and the outcome error, a hung model gets the robot's fallback within the reply's
time budget, a sustained failure ends as a breakdown (also in a role play), the quiz grading
falls back to the exact answer, a failed verification abstains, a role play whose evaluation
fails is partial.

A failed question returns the real client's fallback (LLMDialogClient with the failing call,
bounded by the limits of the request), so those lines are the method's fixed phrases of the
language ("Are you sure you want to cancel?"); the other lines are the scripted LLM's tags. Every
test runs in English and in Spanish (the `system` fixture). The system
runs with the time limits scaled down (TIMES) as the defaults are set (llm_timeout 15 s,
reply_budget 20 s, opening_budget 30 s, intelligence_timeout 30 s): one call times out before the
reply's budget is spent, and intelligence_timeout is longer than the budgets plus the time the
intelligence has to answer after its budget (2 s, AskIntelligence)."""

import json
import os

import pytest

from conftest import PARAMS, _launch
from ros2_dialog_interfaces.action import DialogTask
from interaction_sim.harness import ScriptedUser
from interaction_sim.llm_faults import ENV, FaultPlan

TIMES = {"llm_timeout": 1.5, "reply_budget": 2.5, "opening_budget": 3.0,
         "intelligence_timeout": 5.0}
FAULT_PARAMS = PARAMS.replace("interaction_sim.scripted_llm:make",
                              "interaction_sim.llm_faults:make_scripted") \
    .replace("    ack_phrases:", f"    llm_timeout: {TIMES['llm_timeout']}\n    ack_phrases:") \
    + "".join(f"    {k}: {TIMES[k]}\n"
              for k in ("reply_budget", "opening_budget", "intelligence_timeout"))

DRINKS = {"slots": [
    {"name": "drink", "type": "str", "canonical_values": ["coffee", "tea"]},
    {"name": "size", "type": "str"},
]}
NAME = {"slots": [{"name": "name", "type": "str"}]}
QUESTIONS = [{"question": "Capital of France?", "answer": "Paris"},
             {"question": "2+2?", "answer": "4"}]
STEPS = ["Sit down", {"step": "Raise your arms", "check": "arms up"}, "Breathe"]
SCENE = {"scenario": "Returns desk", "role": "Shop assistant",
         "criteria": ["Explains the fault", "Asks for a refund"], "max_exchanges": 4}
MUSEUM = [{"name": "museum", "description": "visitor information",
           "content": "The museum opens at 10. Tickets cost 5 euros."},
          {"name": "handover", "description": "who can help", "content": "Ask at the desk."}]
Outcome = DialogTask.Result
# What the user says that the system reads itself, per language: an exact stop command, and the
# yes and no words of the fallback reading of the confirmation
WORDS = {"English": {"cancel": "cancel", "stop": "Stop.", "yes": "Yes.", "no": "No."},
         "Spanish": {"cancel": "Cancela.", "stop": "Para.", "yes": "Sí.", "no": "No."}}


def said(system, key: str) -> str:
    return WORDS[getattr(system, "language", "English")][key]


def phrase(system, key: str) -> str:
    from interaction_intelligence.languages import load_language
    return load_language(getattr(system, "language", "English")).phrases[key]


@pytest.fixture(scope="module", params=["English", "Spanish"])
def system(request, tmp_path_factory):
    """The method's trees with the scripted LLM behind the injector, once in each language (the
    fixed phrases of the fallbacks and the exact stop commands are the language's); the switch
    of the injector is passed to the intelligence's process in its environment."""
    assert "llm_faults:make_scripted" in FAULT_PARAMS
    params = FAULT_PARAMS.replace("    ack_phrases:",
                                  f"    language: {request.param}\n    ack_phrases:")
    switch = tmp_path_factory.mktemp(f"faults_{request.param}") / "faults.json"
    old = os.environ.get(ENV)
    os.environ[ENV] = str(switch)
    try:
        for runner in _launch(tmp_path_factory, f"model_failures_{request.param}", params):
            runner.switch = str(switch)
            runner.language = request.param
            yield runner
    finally:
        if old is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = old


def goal(task="Take a drink order", mode="slot_filling", schema=None, resources=None, **kw):
    return DialogTask.Goal(
        task_description=task,
        dialog_mode=mode,
        frame_schema_json=json.dumps(schema) if schema is not None else "",
        resources_json=json.dumps(resources) if resources is not None else "",
        **kw,
    )


def resource(name, content):
    return [{"name": name, "description": name, "content": json.dumps(content)}]


def run(system, g, lines, faults, timeout=60):
    """A dialogue with the planned failures; returns it and the failed calls."""
    plan = FaultPlan(faults, system.switch)
    plan.start()
    try:
        r = system.node.run(g, plan.user(ScriptedUser(lines)), timeout=timeout)
    finally:
        applied = plan.finish()
    return r, applied


def data(r):
    return json.loads(r.result.final_frame_json)


def reply_waits(r):
    """For each user line, the seconds until the robot's next line (fillers aside)."""
    lines = [t for t in r.transcript if not t.get("filler")]
    return [b["t"] - a["t"] for a, b in zip(lines, lines[1:])
            if a["role"] == "user" and b["role"] == "robot"]


# ==== THE SPOKEN CANCELLATION ====


def test_a_failed_cancel_detection_is_no_cancellation(system):
    # On an open slot the scripted LLM takes "please cancel this" as a request (see
    # test_dialogues.py); with the detection failed, the strategy handles the turn
    r, applied = run(system, goal(schema=NAME), ["please cancel this", "name=Ana", "yes"],
                     [{"turn": 1, "kind": "error", "questions": ["detect_cancel_intent"]}])
    assert "[cancel?]" not in r.robot_lines()
    assert r.robot_lines()[1] == "[ask] name?"
    assert r.result.outcome == Outcome.OUTCOME_COMPLETED
    assert data(r) == {"name": "Ana"}
    assert [(a["turn"], a["question"]) for a in applied] == [(1, "detect_cancel_intent")]


def test_an_exact_command_cancels_even_when_the_model_fails(system):
    # Decided locally (no model); the confirmation question falls back to its fixed phrase
    r, applied = run(system, goal(schema=DRINKS), [said(system, "cancel"), "yes"],
                     [{"turn": 1, "kind": "error"}])
    assert r.robot_lines()[1:] == [phrase(system, "cancel_ask"), "[cancelled]"]
    assert r.result.outcome == Outcome.OUTCOME_CANCELLED
    assert {a["question"] for a in applied} == {"ask_cancel_confirmation"}


def test_a_failed_reading_of_the_confirmation_withdraws_the_cancellation(system):
    # An answer that is not exactly a yes or no word is read as "something else" by the
    # fallback: nothing is cancelled and the robot resumes with what it had asked
    r, applied = run(system, goal(schema=DRINKS),
                     [said(system, "cancel"), "yes I think", "drink=tea; size=small", "yes"],
                     [{"turn": 2, "kind": "invalid", "questions": ["classify_cancel_reply"]}])
    lines = r.robot_lines()
    assert lines[1] == "[cancel?]"
    assert lines[2].startswith("[cancel_no]")    # only a yes stops: resumes
    assert "[cancelled]" not in lines
    assert r.result.outcome == Outcome.OUTCOME_COMPLETED
    assert data(r) == {"drink": "tea", "size": "small"}
    assert len(applied) == 2                     # the client asks twice, then falls back


@pytest.mark.parametrize("kind", ["invalid", "error"])
def test_a_failed_reading_of_an_exact_yes_or_no_falls_back_to_the_words(system, kind):
    # Decided 2026-10-30: when the model cannot read the answer, an exact yes word of the
    # language confirms and an exact no word resumes (utils.local_yes_no)
    fault = [{"turn": 2, "kind": kind, "questions": ["classify_cancel_reply"]}]
    r, _ = run(system, goal(schema=DRINKS), ["forget it", said(system, "yes")], fault)
    assert r.robot_lines()[1:] == ["[cancel?]", "[cancelled]"]
    assert r.result.outcome == Outcome.OUTCOME_CANCELLED
    r, _ = run(system, goal(schema=DRINKS),
               ["forget it", said(system, "no"), "drink=tea; size=small", "yes"], fault)
    assert r.robot_lines()[2] == "[cancel_no] [opening] drink?"
    assert r.result.outcome == Outcome.OUTCOME_COMPLETED


def test_with_the_model_down_an_exact_command_ends_without_the_question(system):
    # Decided 2026-10-30: the last call to the model failed (every call of turn 1), so the
    # exact command of turn 2 ends the conversation at once, with the fixed cancel closing
    r, applied = run(system, goal(schema=DRINKS), ["drink=tea", said(system, "stop")],
                     [{"turn": 1, "kind": "error"}])
    lines = r.robot_lines()
    assert "[cancel?]" not in lines and phrase(system, "cancel_ask") not in lines
    assert lines[-1] == "[cancel_ok]"
    assert r.result.outcome == Outcome.OUTCOME_CANCELLED
    assert r.result.failure_reason == "Cancelled by the user"
    # The model answering again (turn 2 has no fault) clears it: the question as usual
    r, _ = run(system, goal(schema=DRINKS),
               ["drink=tea", "size=small", said(system, "stop"), "yes"],
               [{"turn": 1, "kind": "error"}])
    assert r.robot_lines()[-2:] == ["[cancel?]", "[cancelled]"]


def test_a_failed_cancel_check_skill_leaves_the_turn_to_the_strategy(system):
    # The cancel check is the first branch of a fallback: when its skill fails, the strategy
    # handles the turn and the dialogue goes on (no error)
    r, _ = run(system, goal(schema=DRINKS), ["drink=tea; size=small", "yes"],
               [{"turn": 1, "kind": "crash", "questions": ["detect_cancel_intent"]}])
    assert r.robot_lines()[1].endswith("[confirm] {'drink': 'tea', 'size': 'small'}")
    assert r.result.outcome == Outcome.OUTCOME_COMPLETED


# ==== ENDINGS: ERROR AND BREAKDOWN ====


def test_a_failed_skill_ends_with_the_error_closing(system):
    r, applied = run(system, goal(task="A warm-up", mode="procedure",
                                  resources=resource("steps", STEPS)),
                     ["done", "done", "done"],
                     [{"turn": 2, "kind": "crash", "questions": ["classify_step_turn"]}])
    assert r.status == "aborted"
    assert r.robot_lines() == ["[procedure 3] [first 1/3] Sit down",
                               "[next 2/3] Raise your arms", "[close_error]"]
    assert r.result.outcome == Outcome.OUTCOME_ERROR
    assert "Injected model failure" in r.result.failure_reason
    assert r.result.total_turns == 2
    assert [(a["turn"], a["kind"]) for a in applied] == [(2, "crash")]


def test_no_answer_within_intelligence_timeout_ends_with_the_error_closing(system):
    # The intelligence itself does not answer (a question frozen for 20 s, beyond the client's
    # own limits): the orchestrator stops waiting at intelligence_timeout, the robot says the
    # closing line fetched at the start, the outcome is error
    r, applied = run(system, goal(task="A warm-up", mode="procedure",
                                  resources=resource("steps", STEPS)),
                     ["done", "done", "done"],
                     [{"turn": 2, "kind": "freeze", "hang_s": 20,
                       "questions": ["classify_step_turn"]}])
    assert r.robot_lines()[-1] == "[close_error]"
    assert r.result.outcome == Outcome.OUTCOME_ERROR
    assert "did not answer" in r.result.failure_reason
    assert len(r.fillers()) <= 2
    assert TIMES["intelligence_timeout"] - 0.5 < reply_waits(r)[-1] \
        < TIMES["intelligence_timeout"] + 1.5
    assert r.duration_s < 15
    assert applied and applied[0]["kind"] == "freeze"


def test_a_hung_model_gets_the_fallback_within_the_reply_budget(system):
    # Every call hangs at turn 2 (the model never answers): the cancel check waits llm_timeout,
    # the reading of the report only what is left of the budget, and the reminder makes no
    # call (its fixed phrase); the robot answers when the budget is spent, not before, and the
    # dialogue goes on
    r, applied = run(system, goal(task="A warm-up", mode="procedure",
                                  resources=resource("steps", STEPS)),
                     ["done", "done", "done", "done"], [{"turn": 2, "kind": "hang"}])
    lines = r.robot_lines()
    assert lines[2] == phrase(system, "procedure_remind")
    assert r.result.outcome == Outcome.OUTCOME_COMPLETED
    waits = reply_waits(r)
    assert TIMES["reply_budget"] - 0.6 < waits[1] < TIMES["reply_budget"] + 1.0
    assert max(waits[:1] + waits[2:]) < 1.0       # the other turns: no wait
    assert [a["question"] for a in applied] == ["detect_cancel_intent", "classify_step_turn"]
    timeouts = sum(s.get("llm_timeouts", 0) for s in r.skills)
    skipped = sum(s.get("llm_skipped", 0) for s in r.skills)
    assert timeouts == 2 and skipped >= 1


def test_a_failed_frame_design_is_an_error_before_the_robot_speaks(system):
    r, applied = run(system, goal(task="Register the user, slots: name, city"), [],
                     [{"turn": 0, "kind": "error", "questions": ["create_frame"]}])
    assert r.status == "aborted"
    assert r.result.outcome == Outcome.OUTCOME_ERROR
    assert r.robot_lines() == []
    assert {a["question"] for a in applied} == {"create_frame"}


def test_a_sustained_failure_ends_as_a_breakdown_not_a_loop(system):
    # Every question fails from the first turn on: each turn is not understood
    r, _ = run(system, goal(task="Answer the visitors' questions", mode="grounded_qa",
                            resources=MUSEUM),
               ["ask opens"] * 8, [{"turn": 1, "kind": "error", "turns": 0}])
    assert r.robot_lines() == ["[qa_open]", "[unclear]", "[unclear]", "[unclear]",
                               "[close_breakdown]"]
    assert r.result.outcome == Outcome.OUTCOME_BREAKDOWN
    assert r.result.total_turns == 4


def test_a_sustained_failure_in_a_roleplay_ends_as_a_breakdown(system):
    # Every turn the model cannot read is unclear (a request to repeat, no progress), not a line
    # of the scene: the scene does not run to its limit, the dialogue breaks down
    r, _ = run(system, goal(task="Practise a return", mode="roleplay",
                            resources=resource("scene", SCENE)),
               ["It does not heat."] * 8, [{"turn": 1, "kind": "error", "turns": 0}])
    assert r.robot_lines() == ["[intro] [character 1]", "[unclear]", "[unclear]", "[unclear]",
                               "[close_breakdown]"]
    assert r.result.outcome == Outcome.OUTCOME_BREAKDOWN
    assert r.result.total_turns == 4
    assert data(r)["exchanges"] == 0


def test_a_roleplay_whose_evaluation_fails_is_partial(system):
    # A normal scene, ended by the user, whose evaluation fails: no scores, outcome partial with
    # the criteria not scored
    r, _ = run(system, goal(task="Practise a return", mode="roleplay",
                            resources=resource("scene", SCENE)),
               ["It does not heat.", "stop practice"],
               [{"turn": 2, "kind": "error", "questions": ["evaluate_roleplay"]}])
    assert r.robot_lines()[-1] == "[feedback user]"
    assert r.result.outcome == Outcome.OUTCOME_PARTIAL
    assert r.result.failure_reason == \
        "Criteria not scored: Explains the fault; Asks for a refund"
    assert data(r)["scores"] == [] and data(r)["ended_by"] == "user"


def test_a_sustained_failure_in_a_procedure_ends_as_a_breakdown(system):
    r, _ = run(system, goal(task="A warm-up", mode="procedure",
                            resources=resource("steps", STEPS)),
               ["done"] * 8, [{"turn": 1, "kind": "invalid", "turns": 0}])
    assert r.robot_lines()[-1] == "[close_breakdown]"
    assert r.result.outcome == Outcome.OUTCOME_BREAKDOWN
    assert r.result.total_turns == 4


# ==== THE STRATEGIES' FALLBACKS ====


def test_quiz_grading_falls_back_to_the_exact_answer(system):
    # Right only when the reply is the expected answer itself (normalised): "It is 4 I think"
    # is graded wrong, a conservative error rather than a wrong answer accepted
    r, applied = run(system, goal(task="A quiz", mode="quiz",
                                  resources=resource("questions", QUESTIONS)),
                     ["Paris.", "It is 4 I think"],
                     [{"turn": 1, "kind": "error", "turns": 2,
                       "questions": ["evaluate_quiz_answer"]}])
    assert r.robot_lines() == ["[quiz 2] Capital of France?",
                               f"{phrase(system, 'quiz_correct')} [question 2/2] 2+2?",
                               f"{phrase(system, 'quiz_wrong').format(expected='4')} "
                               "[score 1/2]"]
    assert [x["correct"] for x in data(r)["results"]] == [True, False]
    assert {a["turn"] for a in applied} == {1, 2}


def test_a_failed_verification_abstains(system):
    r, _ = run(system, goal(task="Answer the visitors' questions", mode="grounded_qa",
                            resources=MUSEUM),
               ["ask opens", "ask opens", "no more"],
               [{"turn": 1, "kind": "error", "questions": ["verify_answer"]}])
    assert r.robot_lines()[1:] == ["[qa_abstain] Ask at the desk. [qa_more]",
                                   "[opens] [qa_more]", "[qa_bye]"]
    questions = data(r)["questions"]
    assert [q["resolution"] for q in questions] == ["abstained", "answered"]
    assert questions[0]["unverified"] == "[opens]"   # the answer that was not said


def test_a_failed_step_reading_is_a_reminder(system):
    r, _ = run(system, goal(task="A warm-up", mode="procedure",
                            resources=resource("steps", STEPS)),
               ["done", "done", "done", "done"],
               [{"turn": 1, "kind": "invalid", "questions": ["classify_step_turn"]}])
    assert r.robot_lines()[1] == "[remind]"
    assert r.result.outcome == Outcome.OUTCOME_COMPLETED
    assert [x["status"] for x in data(r)["results"]] == ["done", "done", "done"]


def test_a_failed_extraction_is_a_turn_not_understood(system):
    r, _ = run(system, goal(schema=DRINKS), ["drink=tea; size=small", "drink=tea; size=small",
                                             "yes"],
               [{"turn": 1, "kind": "error", "questions": ["extract_turn"]}])
    assert r.robot_lines()[1] == "[ask] drink?"
    assert r.result.outcome == Outcome.OUTCOME_COMPLETED
    assert data(r) == {"drink": "tea", "size": "small"}
