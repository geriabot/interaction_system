# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The shared mechanisms in every strategy, and the strategies' fixed phrases, in English and in
Spanish (layer 1 of the debugging before E0a: evaluation/doc/DEBUGGING.md, evaluation/debug/
coverage.yaml).

The system runs twice, once per language (`language` of the intelligence, its language file),
with the scripted LLM that says the real fixed phrases of the language file
(scripted_llm.LocalizedScriptedLLM) behind the injector of model failures, and the default
acknowledgments and fillers of the language. The robot's generated lines are still the scripted
LLM's tags ("[opening] drink?"); everything the system says without the model (closings, silence
prompts, cancel phrases, the strategies' fixed parts, the fallbacks) is the language's own, read
from its file, so that each test checks the same path in both languages. What the user says is
the scripted LLM's tag language, except what the system itself reads: the exact stop commands,
the yes/no words of the fallback and the noise phrases, which are the language's."""

import os

import pytest

from conftest import PARAMS, _launch
from interaction_intelligence.languages import load_language
from interaction_sim.llm_faults import ENV
from test_model_failures import (DRINKS, MUSEUM, QUESTIONS, SCENE, STEPS, Outcome, data, goal,
                                 resource, run)

LANGUAGES = {
    "English": {"stop": "Stop.", "yes": "yes", "no": "no", "yes_word": "Yes.",
                "noise": "Mm.", "noise_inside": "Hmm, stop now please."},
    "Spanish": {"stop": "Para.", "yes": "sí", "no": "no", "yes_word": "Vale.",
                "noise": "Eh.", "noise_inside": "Eh, para ya por favor."},
}
FASTING = {"key_points": ["No food from midnight", "Water until 6 am",
                          {"point": "No chewing gum", "critical": False}]}
SURVEY = {"name": "Satisfaction", "options": ["No", "Somewhat", "Yes"],
          "items": ["The robot understood me", {"text": "It was slow", "reverse": True}]}

# Every strategy: its request and a user line that makes no progress in it
STRATEGIES = {
    "slot_filling": (lambda **kw: goal(schema=DRINKS, **kw), "maybe"),
    "explanation": (lambda **kw: goal(task="Explain the fasting rules", mode="explanation",
                                      resources=resource("key points", FASTING), **kw),
                    "hmm what"),
    "quiz": (lambda **kw: goal(task="A quiz", mode="quiz",
                               resources=resource("questions", QUESTIONS), **kw), "repeat"),
    "procedure": (lambda **kw: goal(task="A warm-up", mode="procedure",
                                    resources=resource("steps", STEPS), **kw), "I see"),
    "grounded_qa": (lambda **kw: goal(task="Answer the visitors' questions",
                                      mode="grounded_qa", resources=MUSEUM, **kw), "lorem"),
    "questionnaire": (lambda **kw: goal(task="Satisfaction survey", mode="questionnaire",
                                        resources=resource("instrument", SURVEY), **kw),
                      "lorem"),
    "roleplay": (lambda **kw: goal(task="Practise a return", mode="roleplay",
                                   resources=resource("scene", SCENE), **kw), "repeat"),
}


def params(language: str) -> str:
    text = PARAMS.replace("interaction_sim.scripted_llm:make",
                          "interaction_sim.llm_faults:make_scripted_localized")
    old = '    ack_phrases: ["Noted.", "Done."]\n'
    assert old in text
    return text.replace(old, f"    language: {language}\n")


@pytest.fixture(scope="module", params=list(LANGUAGES))
def system(request, tmp_path_factory):
    """The method's trees in one language, with the localized scripted LLM behind the
    injector (its switch in the environment of the intelligence's process)."""
    language = request.param
    switch = tmp_path_factory.mktemp(f"faults_{language}") / "faults.json"
    old = os.environ.get(ENV)
    os.environ[ENV] = str(switch)
    try:
        for runner in _launch(tmp_path_factory, f"bilingual_{language}", params(language)):
            runner.switch = str(switch)
            runner.language = language
            runner.lang = load_language(language)
            runner.words = LANGUAGES[language]
            yield runner
    finally:
        if old is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = old


def phrases(system) -> dict:
    return system.lang.phrases


# ==== THE SHARED MECHANISMS IN EVERY STRATEGY ====


@pytest.mark.parametrize("strategy", list(STRATEGIES))
def test_a_confirmed_cancellation_ends_every_strategy(system, strategy):
    make, _ = STRATEGIES[strategy]
    r, _ = run(system, make(), [system.words["stop"], system.words["yes"]], [])
    assert r.robot_lines()[1:] == ["[cancel?]", "[cancelled]"]
    assert r.result.outcome == Outcome.OUTCOME_CANCELLED
    assert r.result.failure_reason == "Cancelled by the user"


@pytest.mark.parametrize("strategy", list(STRATEGIES))
def test_a_denied_cancellation_resumes_with_the_last_utterance(system, strategy):
    make, _ = STRATEGIES[strategy]
    r, _ = run(system, make(), [system.words["stop"], system.words["no"]], [])
    lines = r.robot_lines()
    assert lines[1] == "[cancel?]"
    assert lines[2] == f"{phrases(system)['cancel_no']} {lines[0]}"
    assert r.result.outcome != Outcome.OUTCOME_CANCELLED


@pytest.mark.parametrize("strategy", list(STRATEGIES))
def test_silence_prompts_then_ends_every_strategy(system, strategy):
    make, _ = STRATEGIES[strategy]
    r, _ = run(system, make(), [None, None], [])
    p = phrases(system)
    assert r.robot_lines()[1:] == [p["timeout"], p["close_user_silent"]]
    assert r.result.outcome == Outcome.OUTCOME_USER_SILENT


@pytest.mark.parametrize("strategy", list(STRATEGIES))
def test_the_turn_limit_ends_every_strategy_with_its_closing(system, strategy):
    make, stall = STRATEGIES[strategy]
    r, _ = run(system, make(max_turns=1), [stall, stall], [])
    assert r.robot_lines()[-1] == phrases(system)["close_turn_limit"]
    assert r.result.outcome == Outcome.OUTCOME_TURN_LIMIT
    assert r.result.total_turns == 1


@pytest.mark.parametrize("strategy", list(STRATEGIES))
def test_turns_without_progress_end_every_strategy_as_a_breakdown(system, strategy):
    # N1 of the coverage audit: the breakdown of every strategy (explanation, procedure, role
    # play and questionnaire had no test)
    make, stall = STRATEGIES[strategy]
    r, _ = run(system, make(max_stalled=2), [stall] * 4, [])
    assert r.robot_lines()[-1] == phrases(system)["close_breakdown"], r.robot_lines()
    assert r.result.outcome == Outcome.OUTCOME_BREAKDOWN
    assert r.result.total_turns == 2


@pytest.mark.parametrize("strategy", list(STRATEGIES))
def test_a_failed_skill_ends_every_strategy_with_the_error_closing(system, strategy):
    make, stall = STRATEGIES[strategy]
    r, applied = run(system, make(), [stall, stall], [{"turn": 1, "kind": "crash"}])
    assert r.robot_lines()[-1] == phrases(system)["close_error"]
    assert r.result.outcome == Outcome.OUTCOME_ERROR
    assert applied


# ==== THE SPOKEN CANCELLATION: WHAT THE SYSTEM READS ITSELF ====


def test_with_the_model_known_down_an_exact_command_ends_without_the_question(system):
    # Every call of turn 1 failed: the exact command of turn 2 ends it at once (2026-10-30)
    r, _ = run(system, STRATEGIES["slot_filling"][0](),
               ["drink=tea", system.words["stop"]], [{"turn": 1, "kind": "error"}])
    p = phrases(system)
    assert p["cancel_ask"] not in r.robot_lines() and "[cancel?]" not in r.robot_lines()
    assert r.robot_lines()[-1] == p["cancel_ok"]
    assert r.result.outcome == Outcome.OUTCOME_CANCELLED


def test_a_failed_reading_of_the_confirmation_falls_back_to_the_yes_words(system):
    fault = [{"turn": 2, "kind": "invalid", "questions": ["classify_cancel_reply"]}]
    r, _ = run(system, STRATEGIES["slot_filling"][0](),
               [system.words["stop"], system.words["yes_word"]], fault)
    assert r.robot_lines()[1:] == ["[cancel?]", "[cancelled]"]
    assert r.result.outcome == Outcome.OUTCOME_CANCELLED


def test_a_whole_utterance_of_noise_is_silence(system):
    r, _ = run(system, STRATEGIES["slot_filling"][0](),
               [system.words["noise"], "drink=tea; size=small", system.words["yes"]], [])
    assert r.robot_lines()[1] == phrases(system)["timeout"]
    assert r.result.outcome == Outcome.OUTCOME_COMPLETED


def test_a_noise_word_inside_an_utterance_keeps_it_a_turn(system):
    # Decided 2026-10-30: "Eh, para ya por favor." is a turn, not noise (the scripted LLM does
    # not take it as a cancel request in strict mode: the strategy reads it)
    r, _ = run(system, STRATEGIES["slot_filling"][0](), [system.words["noise_inside"]], [])
    assert r.robot_lines()[1] == "[ask] drink?"


# ==== WHAT THE SYSTEM SAYS WITHOUT THE MODEL, PER STRATEGY ====


def test_slot_filling_acknowledgments_and_skipped_slot(system):
    ack = system.lang.ack_phrases
    r, _ = run(system, STRATEGIES["slot_filling"][0](),
               ["drink=coffee", "size=large", system.words["yes"]], [])
    assert r.robot_lines()[1] == f"{ack[0]} [ask] size?"
    assert r.robot_lines()[2].startswith(f"{ack[1]} [confirm]")
    assert r.result.outcome == Outcome.OUTCOME_COMPLETED
    r, _ = run(system, STRATEGIES["slot_filling"][0](), ["drink=tea", "skip", system.words["yes"]],
               [])
    assert r.robot_lines()[2] == f"{phrases(system)['slot_skipped']} [confirm] {{'drink': 'tea'}}"
    assert r.result.outcome == Outcome.OUTCOME_PARTIAL


def test_the_fillers_of_the_language(system):
    r, _ = run(system, STRATEGIES["slot_filling"][0](),
               ["slow 3; drink=coffee", "size=large", system.words["yes"]], [])
    assert r.fillers() and all(f["text"] in system.lang.filler_phrases for f in r.fillers())


def test_explanation_gives_up_reminding_the_critical_points(system):
    r, _ = run(system, STRATEGIES["explanation"][0](), ["wrong 1"] * 3, [])
    p = phrases(system)
    points = "No food from midnight; Water until 6 am"
    summary = p["key_points_summary"].replace("{points}", points)
    assert r.robot_lines()[-1] == f"{p['understand_fail']} {summary}"
    assert r.result.outcome == Outcome.OUTCOME_FAILED


def test_quiz_question_left_unanswered(system):
    r, _ = run(system, STRATEGIES["quiz"][0](), ["repeat", "pardon?", "repeat", "4"], [])
    assert r.robot_lines()[3] == f"{phrases(system)['quiz_unanswered']} [question 2/2] 2+2?"
    assert r.result.outcome == Outcome.OUTCOME_PARTIAL


def test_procedure_step_repeated_literally(system):
    r, _ = run(system, STRATEGIES["procedure"][0](), ["repeat"], [])
    p = phrases(system)
    step = p["procedure_step"].format(n=1, total=3, step="Sit down").rstrip(".")
    assert r.robot_lines()[1] == f"{step}. {p['procedure_remind']}"


def test_grounded_qa_abstains_declines_and_says_goodbye(system):
    r, _ = run(system, STRATEGIES["grounded_qa"][0](), ["ask parking", "off topic", "no more"],
               [])
    p = phrases(system)
    assert r.robot_lines()[1:] == [f"{p['qa_abstain']} Ask at the desk. {p['qa_more']}",
                                   f"{p['qa_decline']} {p['qa_more']}", p["qa_bye"]]
    assert r.result.outcome == Outcome.OUTCOME_COMPLETED


def test_questionnaire_reads_items_and_options_in_the_language(system):
    r, _ = run(system, STRATEGIES["questionnaire"][0](), ["option 3", "option 1"], [])
    p = phrases(system)
    intro = p["questionnaire_intro"].replace("{total}", "2")
    options = p["questionnaire_options"].replace(
        "{options}", f"No, Somewhat {p['questionnaire_or']} Yes")
    head = p["questionnaire_item"].replace("{total}", "2")
    assert r.robot_lines() == [
        f"{intro} {head.replace('{n}', '1')} The robot understood me. {options}",
        f"{head.replace('{n}', '2')} It was slow. {options}",
        p["questionnaire_end"]]
    assert data(r)["score"] == 6


def test_questionnaire_item_without_an_answer_after_ambiguous_answers(system):
    # QN5 of the coverage audit (no test before): an answer between options max_attempts
    # times leaves the item without an answer; the dialogue goes on and ends partial
    r, _ = run(system, STRATEGIES["questionnaire"][0](max_stalled=0),
               ["between", "between", "between", "option 1"], [])
    answers = [a["answer"] for a in data(r)["answers"]]
    assert answers[0] is None and answers[1] == "No", data(r)
    assert r.result.outcome == Outcome.OUTCOME_PARTIAL


# ==== THE ROLE PLAY: STOPPING AND FINISHING LEAD TO THE SAME PLACE (2026-10-30) ====


def test_roleplay_stopped_after_a_line_gives_the_evaluation(system):
    r, _ = run(system, STRATEGIES["roleplay"][0](),
               ["Hello", system.words["stop"], system.words["yes"]], [])
    assert r.robot_lines() == ["[intro] [character 1]", "[character 2]", "[cancel?]",
                               "[feedback user]"]
    assert r.result.outcome == Outcome.OUTCOME_CANCELLED
    result = data(r)
    assert result["ended_by"] == "user" and result["exchanges"] == 1
    assert [s["criterion"] for s in result["scores"]] == SCENE["criteria"]


def test_roleplay_stopped_before_any_line_is_a_plain_cancel(system):
    r, _ = run(system, STRATEGIES["roleplay"][0](), [system.words["stop"], system.words["yes"]],
               [])
    assert r.robot_lines()[1:] == ["[cancel?]", "[cancelled]"]
    assert data(r)["scores"] == []
    assert r.result.outcome == Outcome.OUTCOME_CANCELLED


# ==== THE MODEL-FAILURE SCENARIOS OF E3, SCRIPTED (e3_failures.yaml) ====
# The single failures of the cancellation, the endings and most fallbacks are in
# test_model_failures.py (English) and test_spanish_model_failures.py; here, in both languages,
# the sustained failures (n4_*) and the two fallbacks those files lack (f_qn_map, f_ex_judge)

SUSTAINED = {  # the strategy, the user's lines (a first turn that completes, when needed)
    "slot_filling": (2, ["drink=tea; size=small", "yes", "yes", "yes"]),
    "explanation": (1, ["right 1, right 2"] * 3),
    "procedure": (1, ["done"] * 3),
    "grounded_qa": (1, ["ask opens"] * 3),
    "questionnaire": (1, ["option 1"] * 3),
    "roleplay": (1, ["Hello"] * 3),
}


@pytest.mark.parametrize("strategy", list(SUSTAINED))
def test_a_sustained_model_failure_ends_as_a_breakdown(system, strategy):
    # n4_*: the model fails from a turn to the end; every turn is not understood (no progress),
    # so the conversation ends as a breakdown at max_stalled, not in a loop
    first, lines = SUSTAINED[strategy]
    r, applied = run(system, STRATEGIES[strategy][0](max_stalled=2), lines,
                     [{"turn": first, "kind": "error", "turns": 0}])
    assert r.robot_lines()[-1] == phrases(system)["close_breakdown"], r.robot_lines()
    assert r.result.outcome == Outcome.OUTCOME_BREAKDOWN
    assert applied


def test_a_failed_mapping_of_a_questionnaire_answer_asks_the_item_again(system):
    # f_qn_map: nothing is recorded and the item is asked again; the next answer is mapped
    r, applied = run(system, STRATEGIES["questionnaire"][0](),
                     ["option 3", "option 3", "option 1"],
                     [{"turn": 1, "kind": "invalid", "questions": ["map_scale_answer"]}])
    assert len(r.robot_lines()) == 4
    assert [a["answer"] for a in data(r)["answers"]] == ["Yes", "No"]
    assert r.result.outcome == Outcome.OUTCOME_COMPLETED
    assert {a["question"] for a in applied} == {"map_scale_answer"}


def test_a_failed_judgement_of_a_teach_back_asks_the_check_again(system):
    # f_ex_judge: read as other, the check is asked again; the next teach-back is judged
    r, applied = run(system, STRATEGIES["explanation"][0](),
                     ["right 1, right 2", "right 1, right 2"],
                     [{"turn": 1, "kind": "error", "questions": ["judge_teach_back"]}])
    assert r.robot_lines()[-1] == "[understood]"
    assert len(r.robot_lines()) == 3
    assert r.result.outcome == Outcome.OUTCOME_COMPLETED
