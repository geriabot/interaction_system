# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Whole dialogues through the interaction system, with the scripted LLM (scripted_llm.py):
every branch of the dialogue behaviors (the Behavior Trees), the choice of the behavior, the
spoken cancellation, the silences, the limits, barge-in, the conversational fillers and the
action interface.

The robot's lines are the tags of the scripted LLM ("[opening] drink?"), so each test checks
the path the tree took. Every test runs twice, with the system in English and in Spanish
(conftest.system); the lines the system says itself (the silence prompt, the fillers) and the
exact stop commands are the language's. The scenarios port the unit tests of the strategies of the original
DialoStack to the running system."""

import json
import threading
import time

from rclpy.action import ActionClient

from interaction_intelligence.languages import load_language
from ros2_dialog_interfaces.action import DialogTask
from interaction_sim.harness import ScriptedUser, UserTurn

# An exact stop command of each language (decided without the LLM), and what the system says
# itself in it: every test runs in English and in Spanish (conftest.system)
STOP = {"English": "cancel", "Spanish": "Cancela."}


def stop(system) -> str:
    return STOP[system.language]


def timeout_prompt(system) -> str:
    return load_language(system.language).phrases["timeout"]

DRINKS = {"slots": [
    {"name": "drink", "type": "str", "canonical_values": ["coffee", "tea"]},
    {"name": "size", "type": "str"},
]}
QUESTIONS = [{"question": "Capital of France?", "answer": "Paris"},
             {"question": "2+2?", "answer": "4"}]
STEPS = ["Sit down", {"step": "Raise your arms", "check": "arms up"}, "Breathe"]
SCENE = {"scenario": "Job interview", "role": "Interviewer",
         "criteria": ["Clarity", "Examples"], "max_exchanges": 6}


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


def run(system, g, lines, **kw):
    return system.node.run(g, ScriptedUser(lines), timeout=kw.pop("timeout", 60), **kw)


def frame(r):
    return json.loads(r.result.final_frame_json)


# ==== SLOT FILLING ====


def test_slot_filling_happy_path(system):
    r = run(system, goal(schema=DRINKS), ["drink=coffee", "size=large", "yes"])
    assert r.status == "succeeded", r
    assert r.robot_lines() == [
        "[opening] drink?",
        "Noted. [ask] size?",
        "Done. [confirm] {'drink': 'coffee', 'size': 'large'}",
        "[confirms]",
    ]
    assert frame(r) == {"drink": "coffee", "size": "large"}
    assert r.result.total_turns == 3
    # Feedback on every utterance, before it is said
    assert [f["utterance"] for f in r.feedback] == r.robot_lines()
    assert r.feedback[-1]["state"] == "confirming"


def test_slot_filling_no_ack_without_extraction_and_rephrase_after_attempts(system):
    r = run(system, goal(schema=DRINKS),
            ["I am not sure", "I do not know", "no idea", "drink=tea; size=small", "yes"])
    assert r.status == "succeeded"
    lines = r.robot_lines()
    assert lines[1] == "[ask] drink?"            # nothing extracted: no acknowledgment
    assert lines[2] == "[ask] drink?"
    assert lines[3] == "[ask] drink? rephrased=['drink']"  # asked max_attempts times
    assert lines[4].startswith("Noted. [confirm]")


def test_slot_filling_correction_while_confirming(system):
    r = run(system, goal(schema=DRINKS), ["drink=coffee; size=large", "size=small", "yes"])
    assert r.status == "succeeded"
    # After a correction the frame is read back as recorded (provisional, 2026-11-01)
    assert r.robot_lines()[2].endswith("[confirm] {'drink': 'coffee', 'size': 'small'}")
    assert frame(r) == {"drink": "coffee", "size": "small"}


def test_slot_filling_unclear_answers_are_a_breakdown(system):
    r = run(system, goal(schema=DRINKS),
            ["drink=coffee; size=large", "maybe", "perhaps", "whatever", "who knows"])
    assert r.status == "aborted"
    lines = r.robot_lines()
    assert lines[1].startswith("Noted. [confirm]")
    assert all(line.startswith("[confirm]") for line in lines[2:5])
    assert lines[-1] == "[close_breakdown]"
    assert r.result.outcome == DialogTask.Result.OUTCOME_BREAKDOWN
    assert r.result.failure_reason.startswith("Communication breakdown")
    assert frame(r) == {"drink": "coffee", "size": "large"}  # the data, also on failure


def test_slot_filling_skip_a_slot(system):
    # The user cannot give the size: left unanswered, said in the read-back, outcome partial
    r = run(system, goal(schema=DRINKS), ["drink=tea", "I don't know", "yes"])
    assert r.status == "aborted"
    assert r.robot_lines()[2] == "[slot_skipped] [confirm] {'drink': 'tea'}"
    assert r.result.outcome == DialogTask.Result.OUTCOME_PARTIAL
    assert r.result.failure_reason == "Unanswered slots: size"
    assert frame(r) == {"drink": "tea"}


def test_slot_filling_slot_left_unanswered_after_the_attempts(system):
    # (not fillers such as "hmm": the intelligence drops them as noise, a silence)
    r = run(system, goal(schema=DRINKS),
            ["drink=tea", "not sure", "who knows", "the weather", "whatever", "yes"])
    lines = r.robot_lines()
    assert lines[2:5] == ["[ask] size?", "[ask] size?", "[ask] size? rephrased=['size']"]
    assert lines[5] == "[slot_skipped] [confirm] {'drink': 'tea'}"
    assert r.result.outcome == DialogTask.Result.OUTCOME_PARTIAL


def test_slot_filling_question_while_confirming_resets_the_count(system):
    r = run(system, goal(schema=DRINKS),
            ["drink=coffee; size=large", "maybe", "perhaps", "is it hot?", "maybe", "yes"])
    assert r.status == "succeeded"
    assert "[answer]" in r.robot_lines()


def test_slot_filling_out_of_range_values(system):
    schema = {"slots": [{"name": "age", "type": "int", "minimum": 18, "maximum": 120}]}
    r = run(system, goal(task="Register the user", schema=schema),
            ["age=10", "age=30", "age=200", "age=40", "yes"])
    assert r.status == "succeeded"
    lines = r.robot_lines()
    assert lines[1] == "[ask] age? rejected=['age']"
    assert lines[2].startswith("Noted. [confirm]")
    assert lines[3] == "[ask] age? rejected=['age']"  # an invalid correction reopens the slot
    assert frame(r) == {"age": 40}


def test_slot_filling_resume_and_complete_initial_frame(system):
    r = run(system, goal(schema=DRINKS, initial_frame_json='{"drink": "tea"}'), ["size=small", "yes"])
    assert r.robot_lines()[0] == "[resume] size?"
    assert r.status == "succeeded"
    r = run(system, goal(schema=DRINKS, initial_frame_json='{"drink": "tea", "size": "small"}'),
            ["yes"])
    assert r.robot_lines() == ["[confirm] {'drink': 'tea', 'size': 'small'}", "[confirms]"]
    assert r.status == "succeeded"


def test_slot_filling_skip_intro(system):
    r = run(system, goal(schema=DRINKS, skip_intro=True), ["drink=tea; size=small", "yes"])
    assert r.robot_lines()[0] == "[opening_direct] drink?"
    assert r.status == "succeeded"


def test_slot_filling_conditional_slot(system):
    schema = {"slots": [
        {"name": "kind", "type": "str", "canonical_values": ["a", "b"]},
        {"name": "detail", "type": "str", "condition_slot": "kind", "condition_value": "a"},
    ]}
    r = run(system, goal(schema=schema), ["kind=b", "yes"])
    assert r.robot_lines()[1].startswith("Noted. [confirm]")  # detail not in scope
    assert frame(r) == {"kind": "b"}
    r = run(system, goal(schema=schema), ["kind=a", "detail=x", "yes"])
    assert r.robot_lines()[1] == "Noted. [ask] detail?"
    assert frame(r) == {"kind": "a", "detail": "x"}


def test_slot_filling_slot_conditioned_on_an_unanswered_slot_is_not_asked(system):
    # The parent slot is left unanswered: its conditional slot never comes into scope, so it is
    # neither asked nor reported as unanswered (SF17 of the coverage audit)
    schema = {"slots": [
        {"name": "item", "type": "str", "canonical_values": ["chair", "parasol"]},
        {"name": "delivery", "type": "str", "canonical_values": ["home", "store"]},
        {"name": "address", "type": "str", "condition_slot": "delivery", "condition_value": "home"},
        {"name": "name", "type": "str"},
    ]}
    r = run(system, goal(task="Take a furniture order", schema=schema),
            ["item=parasol", "I don't know", "name=Ana", "yes"])
    assert r.robot_lines() == [
        "[opening] item?",
        "Noted. [ask] delivery?",
        "[slot_skipped] [ask] name?",             # not the address
        "Done. [confirm] {'item': 'parasol', 'name': 'Ana'}",
        "[confirms]",
    ]
    assert r.result.outcome == DialogTask.Result.OUTCOME_PARTIAL
    assert r.result.failure_reason == "Unanswered slots: delivery"
    assert frame(r) == {"item": "parasol", "name": "Ana"}


def test_slot_filling_list_operations(system):
    schema = {"slots": [{"name": "items", "type": "list_str"}, {"name": "name", "type": "str"}]}
    r = run(system, goal(schema=schema), ["+items=apple,pear", "-items=apple", "name=Ana", "yes"])
    assert r.status == "succeeded"
    assert frame(r) == {"items": ["pear"], "name": "Ana"}


def test_slot_filling_frame_designed_by_the_llm(system):
    r = run(system, goal(task="Register the user, slots: name, city"), ["name=Ana; city=Madrid", "yes"])
    assert r.status == "succeeded"
    assert frame(r) == {"name": "Ana", "city": "Madrid"}


def test_slot_filling_invalid_schema_fails_at_once(system):
    r = run(system, goal(schema={"slots": []}), [])
    assert r.status == "aborted"
    assert "must not be empty" in r.result.failure_reason
    assert r.robot_lines() == []


# ==== CANCELLATION, SILENCE, LIMITS ====


def test_spoken_cancellation_confirmed(system):
    r = run(system, goal(schema=DRINKS), [stop(system), "yes"])
    assert r.status == "aborted"
    assert r.robot_lines()[1:] == ["[cancel?]", "[cancelled]"]
    assert r.result.outcome == DialogTask.Result.OUTCOME_CANCELLED
    assert r.result.failure_reason == "Cancelled by the user"


def test_spoken_cancellation_denied(system):
    # The robot resumes with what it had asked before the cancel request
    r = run(system, goal(schema=DRINKS), [stop(system), "no", "drink=tea; size=small", "yes"])
    assert r.status == "succeeded"
    assert r.robot_lines()[1:3] == ["[cancel?]", "[cancel_no] [opening] drink?"]
    assert r.result.outcome == DialogTask.Result.OUTCOME_COMPLETED


def test_spoken_cancellation_answered_with_content(system):
    # Only a yes stops: an answer with content resumes with the question asked before, and
    # never reaches the strategy as an answer to it (decided by the author, 2026-11-01)
    r = run(system, goal(schema=DRINKS),
            [stop(system), "drink=tea; size=small", "drink=tea; size=small", "yes"])
    assert r.status == "succeeded"
    assert r.robot_lines()[2] == "[cancel_no] [opening] drink?"
    assert r.robot_lines()[3].startswith("Noted. [confirm]")
    assert frame(r) == {"drink": "tea", "size": "small"}


def test_cancellation_is_strict_on_a_closed_choice(system):
    # The next slot (drink) has canonical values: the LLM is asked in strict mode, so a
    # cancel-like word is not a request...
    r = run(system, goal(schema=DRINKS), ["please cancel this", "drink=tea; size=small", "yes"])
    assert "[cancel?]" not in r.robot_lines()
    assert r.status == "succeeded"
    # ...but an explicit request that is not an exact command still cancels (it never did)
    r = run(system, goal(schema=DRINKS), ["forget it", "yes"])
    assert r.robot_lines()[1:] == ["[cancel?]", "[cancelled]"]
    # An open slot: the ambiguous request goes to the LLM, which takes it as a cancel
    open_slots = {"slots": [{"name": "name", "type": "str"}]}
    r = run(system, goal(schema=open_slots), ["please cancel this", "yes"])
    assert r.robot_lines()[1:] == ["[cancel?]", "[cancelled]"]


def test_silence_prompt_then_answer(system):
    r = run(system, goal(schema=DRINKS), [None, "drink=tea; size=small", "yes"])
    assert r.status == "succeeded"
    assert r.robot_lines()[1] == timeout_prompt(system)
    assert r.result.total_turns == 2


def test_silence_ends_the_dialogue(system):
    # max_silence 3 s with wait_timeout 1.5 s: a prompt, then the closing line
    r = run(system, goal(schema=DRINKS), [None, None])
    assert r.status == "aborted"
    assert r.result.outcome == DialogTask.Result.OUTCOME_USER_SILENT
    assert r.result.failure_reason.startswith("The user stayed silent")
    assert r.robot_lines()[1:] == [timeout_prompt(system), "[close_user_silent]"]


def test_silence_prompts_of_a_longer_wait(system):
    # The request's max_silence (6 s, four waits): waiting, the pending question again, quiet,
    # then the closing line
    r = run(system, goal(schema=DRINKS, max_silence=6.0), [None, None, None, None], timeout=60)
    assert r.robot_lines()[1:] == [
        timeout_prompt(system), "[opening] drink?", "[close_user_silent]"]
    assert r.result.outcome == DialogTask.Result.OUTCOME_USER_SILENT


def test_max_turns(system):
    r = run(system, goal(schema=DRINKS, max_turns=2), ["I am not sure", "no idea", "drink=tea"])
    assert r.status == "aborted"
    assert r.result.outcome == DialogTask.Result.OUTCOME_TURN_LIMIT
    assert r.result.failure_reason == "Max turns reached"
    assert r.result.total_turns == 2
    assert r.robot_lines()[-1] == "[close_turn_limit]"   # never a silent ending


def test_max_stalled_of_the_request(system):
    # The request's max_stalled (2) instead of the system's (4)
    r = run(system, goal(schema=DRINKS, max_stalled=2),
            ["drink=coffee; size=large", "maybe", "perhaps", "whatever"])
    assert r.result.outcome == DialogTask.Result.OUTCOME_BREAKDOWN
    assert r.result.total_turns == 3
    assert r.robot_lines()[-1] == "[close_breakdown]"


def test_unlimited_stalled_turns(system):
    # max_stalled 0: no breakdown, the turn limit ends the dialogue
    r = run(system, goal(schema=DRINKS, max_stalled=0, max_turns=7),
            ["drink=coffee; size=large"] + ["maybe"] * 7)
    assert r.result.outcome == DialogTask.Result.OUTCOME_TURN_LIMIT
    assert r.result.total_turns == 7
    assert "[close_breakdown]" not in r.robot_lines()


def test_unlimited_silence(system):
    # max_silence 0: the robot keeps waiting well past the system's 3 s, until cancelled
    r = run(system, goal(schema=DRINKS, max_silence=0.0), [None] * 8, cancel_after=7.0)
    assert r.status == "canceled"
    assert r.result.outcome == DialogTask.Result.OUTCOME_CANCELLED
    assert "[close_user_silent]" not in r.robot_lines()


def test_action_cancel(system):
    r = run(system, goal(schema=DRINKS), [None, None], cancel_after=0.8)
    assert r.status == "canceled"
    assert r.result.outcome == DialogTask.Result.OUTCOME_CANCELLED
    assert r.result.failure_reason == "Cancelled"


def test_second_request_rejected_while_running(system):
    client = ActionClient(system.node, DialogTask, "/dialog/execute_task")
    result = {}

    def first():
        result["run"] = run(system, goal(schema=DRINKS), [None, None])

    thread = threading.Thread(target=first)
    thread.start()
    time.sleep(0.8)
    accepted = client.send_goal_async(goal(schema=DRINKS))
    deadline = time.time() + 10
    while not accepted.done() and time.time() < deadline:
        time.sleep(0.05)
    assert accepted.done() and not accepted.result().accepted
    thread.join()
    assert result["run"].status == "aborted"
    client.destroy()


def test_another_runners_status_does_not_end_the_dialogue(system):
    """/behavior_status is shared by every behavior runner on the ROS domain: a SUCCESS or a
    FAILURE published by another process (another robot's tree) does not end this dialogue."""
    from std_msgs.msg import String
    pub = system.node.create_publisher(String, "/behavior_status", 10)
    stop_flag = threading.Event()

    def stray():
        time.sleep(0.3)
        while not stop_flag.is_set():
            for status in ("FAILURE", "SUCCESS"):
                pub.publish(String(data=status))
            time.sleep(0.2)

    thread = threading.Thread(target=stray)
    thread.start()
    try:
        r = run(system, goal(schema=DRINKS), ["drink=coffee", "size=large", "yes"])
    finally:
        stop_flag.set()
        thread.join()
        system.node.destroy_publisher(pub)
    assert r.status == "succeeded", r
    assert frame(r) == {"drink": "coffee", "size": "large"}
    assert r.result.total_turns == 3


def test_barge_in_cuts_the_robot(system):
    r = run(system, goal(schema=DRINKS),
            [UserTurn("drink=tea; size=small", barge_in_after=0.3), "yes"])
    assert r.status == "succeeded"
    assert len(r.barge_ins) == 1
    assert r.barge_ins[0]["utterance"].startswith("Noted. [confirm]")


# ==== EXPLANATION ====

FASTING = {"key_points": ["No food from midnight", "Water until 6 am",
                          {"point": "No chewing gum", "critical": False}]}


def explanation(**kw):
    return goal(task="Explain the fasting rules", mode="explanation",
                resources=resource("key points", FASTING), **kw)


def test_explanation_teach_back_verified(system):
    r = run(system, explanation(), ["right 1, right 2"])
    assert r.robot_lines() == ["[explanation] [teach_back]", "[understood]"]
    assert r.status == "succeeded"
    assert r.result.outcome == DialogTask.Result.OUTCOME_COMPLETED
    data = frame(r)
    assert [p["status"] for p in data["key_points"]] == ["correct", "correct", "pending"]
    assert data["self_reported"] is False


def test_explanation_repairs_only_the_points_missing_or_wrong(system):
    # Point 1 right, point 2 wrong: only point 2 is explained again; then it is said back
    # (only the points still to verify are judged: point 2 is now the first of them)
    r = run(system, explanation(), ["right 1, wrong 2", "right 1"])
    assert r.robot_lines()[1:] == ["[repair Water until 6 am] [teach_back_again]", "[understood]"]
    assert r.status == "succeeded"


def test_explanation_partial_teach_back_asks_only_what_is_missing(system):
    # Point 1 right and nothing wrong: progress, so the robot asks only for point 2 (without
    # explaining it again) and the check is not counted as failed
    r = run(system, explanation(), ["right 1", "right 1"])
    assert r.robot_lines()[1:] == ["[ask_missing Water until 6 am]", "[understood]"]
    assert r.status == "succeeded"
    assert frame(r)["attempts"] == 0


def test_explanation_question_then_teach_back(system):
    r = run(system, explanation(), ["can I drink water?", "restate all"])
    assert r.robot_lines()[1:] == ["[answer] [teach_back]", "[understood]"]
    assert r.status == "succeeded"


def test_explanation_declines_then_targeted_question(system):
    # "I understood" is not evidence: the fallback asks about the most critical point
    r = run(system, explanation(), ["I understood", "right 1", "right 1"])
    assert r.robot_lines()[1:] == [
        "[targeted] No food from midnight?",
        "[ask_missing Water until 6 am]",
        "[understood]",
    ]
    assert frame(r)["self_reported"] is True
    assert r.status == "succeeded"


def test_explanation_not_understood_explained_again(system):
    r = run(system, explanation(), ["I do not understand", "restate all"])
    assert r.robot_lines()[1:] == ["[rephrase 2] [teach_back_again]", "[understood]"]


def test_explanation_gives_up_after_max_attempts(system):
    r = run(system, explanation(), ["wrong 1", "wrong 1", "wrong 1"])
    lines = r.robot_lines()
    assert lines[1].startswith("[repair No food from midnight,Water until 6 am]")
    assert lines[-1] == "[understand_fail] [key_points_summary]"
    assert r.status == "aborted"
    assert r.result.outcome == DialogTask.Result.OUTCOME_FAILED
    assert r.result.failure_reason.startswith("Critical points not verified after 3 checks")
    data = frame(r)
    assert data["attempts"] == 3 and data["completed"] is False


def test_explanation_key_points_from_the_llm_and_skip_intro(system):
    r = run(system, goal(task="Explain it", mode="explanation", skip_intro=True), ["right 1"])
    assert r.robot_lines() == ["[explanation_direct] [teach_back]", "[understood]"]
    assert frame(r)["key_points"][0]["point"] == "the main point"


# ==== QUIZ ====

def test_quiz_scores_the_answers(system):
    r = run(system, goal(task="A quiz", mode="quiz", resources=resource("questions", QUESTIONS)),
            ["Paris", "five"])
    assert r.robot_lines() == [
        "[quiz 2] Capital of France?",
        "[right] [question 2/2] 2+2?",
        "[wrong] [score 1/2]",
    ]
    assert r.status == "succeeded"
    assert r.result.outcome == DialogTask.Result.OUTCOME_COMPLETED
    data = frame(r)
    assert data["correct_answers"] == 1 and data["total_questions"] == 2
    assert [x["correct"] for x in data["results"]] == [True, False]


def test_quiz_repeat_then_unanswered(system):
    # Not answers: the question again (literally, then rephrased), then left unanswered
    r = run(system, goal(task="A quiz", mode="quiz", resources=resource("questions", QUESTIONS)),
            ["repeat please", "pardon?", "repeat", "4"])
    assert r.robot_lines() == [
        "[quiz 2] Capital of France?",
        "Capital of France?",
        "[rephrased] Capital of France?",
        "[quiz_unanswered] [question 2/2] 2+2?",
        "[right] [score 1/2]",
    ]
    assert r.result.outcome == DialogTask.Result.OUTCOME_PARTIAL
    data = frame(r)
    assert data["unanswered"] == 1
    assert [x["answered"] for x in data["results"]] == [False, True]


def test_quiz_breakdown(system):
    r = run(system, goal(task="A quiz", mode="quiz", resources=resource("questions", QUESTIONS)),
            ["repeat", "repeat", "repeat", "repeat"])
    assert r.robot_lines()[-1] == "[close_breakdown]"
    assert r.result.outcome == DialogTask.Result.OUTCOME_BREAKDOWN


def test_quiz_skip_intro_and_missing_bank(system):
    r = run(system, goal(task="A quiz", mode="quiz", skip_intro=True,
                         resources=resource("questions", QUESTIONS)), ["Paris", "4"])
    assert r.robot_lines()[0] == "Capital of France?"
    assert r.status == "succeeded"
    r = run(system, goal(task="A quiz", mode="quiz"), [])
    assert r.status == "aborted"
    assert "question bank" in r.result.failure_reason or "quiz requires" in r.result.failure_reason


# ==== PROCEDURE ====

def test_procedure_every_kind_of_turn(system):
    r = run(system, goal(task="A warm-up", mode="procedure", resources=resource("steps", STEPS)),
            ["done", "how high?", "problem", "problem", "problem", "back", "repeat", "skip",
             "I see", "done"])
    assert r.robot_lines() == [
        "[procedure 3] [first 1/3] Sit down",
        "[next 2/3] Raise your arms",
        "[answer]",
        "[help 2]",
        "[help 3]",
        "[next 3/3] Breathe",          # stuck after max_attempts helps
        "[back 2/3] Raise your arms",
        "[procedure_step]. [procedure_remind]",  # the step again, literally (fixed phrases)
        "[next 3/3] Breathe",          # skipped
        "[remind]",
        "[summary 2/3]",
    ]
    # A step that is not optional was skipped: partial
    assert r.status == "aborted"
    assert r.result.outcome == DialogTask.Result.OUTCOME_PARTIAL
    assert r.result.failure_reason == "Steps not done: Raise your arms (skipped)"
    data = frame(r)
    assert [x["status"] for x in data["results"]] == ["done", "skipped", "done"]
    assert data["completed"] == 2 and data["skipped"] == 1


def test_procedure_step_duration_and_optional_step(system):
    # The first step takes 3 s: the robot waits in silence (two waits of 1.5 s) without
    # prompting, and only then says it is waiting; the second step is optional, so skipping it
    # still completes the procedure
    steps = [{"step": "Measure", "duration_s": 3.0}, {"step": "Rest", "optional": True}]
    r = run(system, goal(task="A check", mode="procedure", resources=resource("steps", steps)),
            [None, "done", "skip"])
    assert r.robot_lines() == [
        "[procedure 2] [first 1/2] Measure", timeout_prompt(system),
        "[next 2/2] Rest", "[summary 1/2]"]
    robot = [t for t in r.transcript if t["role"] == "robot"]
    assert robot[1]["t"] - robot[0]["t"] >= 4.5   # the step's 3 s, then one more wait
    assert r.status == "succeeded"
    assert r.result.outcome == DialogTask.Result.OUTCOME_COMPLETED


# ==== GROUNDED QUESTION ANSWERING ====

MUSEUM = [{"name": "museum", "description": "visitor information",
           "content": "The museum opens at 10. Tickets cost 5 euros."},
          {"name": "handover", "description": "who can help", "content": "Ask at the desk."}]


def test_grounded_qa_answers_abstains_and_declines(system):
    r = run(system, goal(task="Answer the visitors' questions", mode="grounded_qa",
                         resources=MUSEUM),
            ["ask opens", "what a shame", "ask parking", "ask unsupported", "off topic", "repeat",
             "no more"])
    assert r.robot_lines() == [
        "[qa_open]",
        "[opens] [qa_more]",                              # from the material, verified
        "[qa_more]",                                      # a comment: not a question
        "[qa_abstain] Ask at the desk. [qa_more]",        # not in the material: the handover
        "[qa_abstain] Ask at the desk. [qa_more]",        # an answer that fails the verification
        "[qa_decline] [qa_more]",                         # outside the task
        "[qa_abstain] Ask at the desk. [qa_more]",        # the last answer again
        "[qa_bye]",
    ]
    assert r.status == "succeeded"
    data = frame(r)
    assert (data["answered"], data["abstained"], data["declined"]) == (1, 2, 1)
    assert [q["resolution"] for q in data["questions"]] == [
        "answered", "abstained", "abstained", "declined"]


def test_grounded_qa_needs_material(system):
    r = run(system, goal(task="Answer questions", mode="grounded_qa",
                         resources=resource("questions", QUESTIONS)), [])
    assert r.status == "aborted"
    assert r.result.outcome == DialogTask.Result.OUTCOME_ERROR
    assert "material" in r.result.failure_reason


def test_grounded_qa_breakdown_and_skip_intro(system):
    r = run(system, goal(task="Answer the visitors' questions", mode="grounded_qa",
                         resources=MUSEUM, skip_intro=True), ["lorem", "ipsum dolor", "sit amet", "foo bar"])
    assert r.robot_lines()[0] == "[qa_invite]"
    assert r.robot_lines()[-1] == "[close_breakdown]"
    assert r.result.outcome == DialogTask.Result.OUTCOME_BREAKDOWN


def test_grounded_qa_closes_after_max_off_task_requests_in_a_row(system):
    # Decided by the author (2026-11-01): requests in a row outside the task end the dialogue
    # with their own reason; a question within the task starts the count again, a reaction does
    # not (the limit travels in the request)
    museum = dict(task="Answer the visitors' questions", mode="grounded_qa", resources=MUSEUM)
    r = run(system, goal(max_off_task=3, **museum), ["off topic", "off topic", "off topic"])
    assert r.robot_lines()[-1] == "[close_off_task]"
    assert r.result.outcome == DialogTask.Result.OUTCOME_BREAKDOWN
    assert r.result.failure_reason.startswith("Off-task")
    r = run(system, goal(max_off_task=3, **museum),
            ["off topic", "off topic", "ask opens", "off topic", "what a shame", "off topic",
             "no more"])
    assert r.robot_lines()[-1] == "[qa_bye]" and r.status == "succeeded"


# ==== QUESTIONNAIRE ====

SURVEY = {"name": "Satisfaction", "options": ["No", "Somewhat", "Yes"],
          "items": ["The robot understood me",
                    {"text": "It was slow", "reverse": True,
                     "clarification": "Slow means it took long to answer."}]}


def test_questionnaire_reads_items_literally_and_scores(system):
    r = run(system, goal(task="Satisfaction survey", mode="questionnaire",
                         resources=resource("instrument", SURVEY)),
            ["option 3", "meaning", "between", "option 1"])
    assert r.robot_lines() == [
        "[questionnaire_intro] [questionnaire_item] The robot understood me. [questionnaire_options]",
        "[questionnaire_item] It was slow. [questionnaire_options]",
        "Slow means it took long to answer. [questionnaire_options]",   # the clarification
        "[questionnaire_options]",                                      # between options
        "[questionnaire_end]",
    ]
    assert r.status == "succeeded"
    data = frame(r)
    assert [a["answer"] for a in data["answers"]] == ["Yes", "No"]
    assert data["score"] == 6        # 3 + (1 reversed: 3)


def test_questionnaire_instructions_are_read_even_with_skip_intro(system):
    # The instructions are part of the administration of the instrument (QN10 of the coverage
    # audit): skip_intro does not drop them, nor the fixed introduction when there are none
    survey = dict(SURVEY, instructions="I will read you two statements about the robot.")
    r = run(system, goal(task="Satisfaction survey", mode="questionnaire", skip_intro=True,
                         resources=resource("instrument", survey)), ["option 3", "option 1"])
    assert r.robot_lines()[0] == ("I will read you two statements about the robot. "
                                  "[questionnaire_item] The robot understood me. "
                                  "[questionnaire_options]")
    assert r.status == "succeeded"
    r = run(system, goal(task="Satisfaction survey", mode="questionnaire", skip_intro=True,
                         resources=resource("instrument", SURVEY)), ["option 3", "option 1"])
    assert r.robot_lines()[0].startswith("[questionnaire_intro] [questionnaire_item]")


def test_questionnaire_declined_item_is_partial(system):
    r = run(system, goal(task="Satisfaction survey", mode="questionnaire",
                         resources=resource("instrument", SURVEY)), ["pass", "option 2"])
    assert r.result.outcome == DialogTask.Result.OUTCOME_PARTIAL
    assert r.result.failure_reason == "Items without an answer: 1 (refused)"
    assert frame(r)["score"] == 2


# ==== ROLEPLAY ====

def test_roleplay_coach_and_limit(system):
    r = run(system, goal(task="Practise an interview", mode="roleplay",
                         resources=resource("scene", SCENE)),
            ["Hello", "help me", "I worked at a lab", "In Madrid", "Two years", "Yes",
             "Thanks"])
    # The hint does not use up an exchange of the scene (max_exchanges 6)
    assert r.robot_lines() == ["[intro] [character 1]", "[character 2]", "[hint]",
                               "[character 3]", "[character 4]", "[character 5]",
                               "[character 6]", "[feedback limit]"]
    assert r.status == "succeeded"
    data = frame(r)
    assert data["ended_by"] == "limit" and data["average"] == 3
    assert [s["criterion"] for s in data["scores"]] == ["Clarity", "Examples"]


def test_roleplay_scene_has_at_least_six_exchanges(system):
    # A scene that asks for fewer exchanges still gets MIN_EXCHANGES (6)
    short = dict(SCENE, max_exchanges=2)
    r = run(system, goal(task="Practise an interview", mode="roleplay",
                         resources=resource("scene", short)),
            ["one", "two", "three", "four", "five", "six"])
    assert r.robot_lines()[-1] == "[feedback limit]"
    assert frame(r)["exchanges"] == 6


def test_roleplay_repeat_literal_then_in_other_words(system):
    r = run(system, goal(task="Practise an interview", mode="roleplay",
                         resources=resource("scene", SCENE)),
            ["Hello", "repeat", "repeat please", "stop practice"])
    assert r.robot_lines() == ["[intro] [character 1]", "[character 2]",
                               "[character 2]",   # literally, no LLM
                               "[character 3]",   # then the character says it again
                               "[feedback user]"]
    assert frame(r)["exchanges"] == 1             # repeat requests are not exchanges


def test_roleplay_ended_by_the_user_or_the_scene(system):
    scene = resource("scene", SCENE)
    r = run(system, goal(task="Practise", mode="roleplay", resources=scene), ["stop practice"])
    assert r.robot_lines()[-1] == "[feedback user]"
    r = run(system, goal(task="Practise", mode="roleplay", resources=scene), ["Hi", "the end"])
    assert r.robot_lines()[-1] == "[feedback scene]"


def test_roleplay_limit_capped_by_max_turns(system):
    r = run(system, goal(task="Practise", mode="roleplay", resources=resource("scene", SCENE),
                         max_turns=2), ["Hello", "Fine"])
    assert r.robot_lines()[-1] == "[feedback limit]"
    assert r.status == "succeeded"


def test_roleplay_needs_criteria(system):
    scene = dict(SCENE, criteria=[])
    r = run(system, goal(task="Practise", mode="roleplay", resources=resource("scene", scene)), [])
    assert r.status == "aborted"
    assert "criterion" in r.result.failure_reason


# ==== THE CHOICE OF THE BEHAVIOR ====

def test_behavior_chosen_from_the_task(system):
    r = run(system, goal(task="Explain the fasting rules", mode=""), ["got it"])
    assert r.robot_lines()[0] == "[explanation] [teach_back]"
    r = run(system, goal(task="Register the user, slots: name", mode=""), ["name=Ana", "yes"])
    assert r.robot_lines()[0] == "[opening] name?"


def test_behavior_offered_only_with_what_it_needs(system):
    # With a question bank the quiz is offered and chosen
    r = run(system, goal(task="Run a quiz", mode="", resources=resource("q", QUESTIONS)),
            ["Paris", "4"])
    assert r.robot_lines()[0] == "[quiz 2] Capital of France?"
    # Without it the quiz is not offered: the default, slot filling
    r = run(system, goal(task="Run a quiz, slots: answer", mode=""), ["answer=1", "yes"])
    assert r.robot_lines()[0] == "[opening] answer?"


def test_choosing_the_behavior_failed(system):
    # The model fails while choosing the behavior (O12 of the coverage audit): the request ends
    # as an error with its reason, before any utterance
    r = run(system, goal(task="Take an order, choice fails", mode=""), [])
    assert r.status == "aborted"
    assert r.result.outcome == DialogTask.Result.OUTCOME_ERROR
    assert r.result.failure_reason == (
        "Choosing the dialogue behavior failed: scripted failure of the choice")
    assert r.robot_lines() == []


def test_unknown_mode(system):
    r = run(system, goal(mode="dance"), [])
    assert r.status == "aborted"
    assert r.result.failure_reason.startswith("Unknown dialog mode 'dance'. Available:")


# ==== CONVERSATIONAL FILLERS ====
# "slow N" makes the scripted LLM take N seconds (scripted_llm.py). The system's filler_after_s
# and filler_repeat_s are the defaults, 2 s and 8 s: the ladder at 2, 5, 9 and 14 s, then every 8 s. The times are those of the harness: a user
# line when it is heard, a robot line when it starts.


def _user_t(r, text):
    return next(t["t"] for t in r.transcript if t["role"] == "user" and t["text"] == text)


def _robot_t(r, text):
    return next(t["t"] for t in r.transcript
                if t["role"] == "robot" and not t.get("filler") and t["text"] == text)


HAPPY = ["[opening] drink?", "Noted. [ask] size?",
         "Done. [confirm] {'drink': 'coffee', 'size': 'large'}", "[confirms]"]


def test_no_filler_when_the_reply_is_fast(system):
    r = run(system, goal(schema=DRINKS), ["drink=coffee", "size=large", "yes"])
    assert r.status == "succeeded"
    assert r.fillers() == []
    assert r.robot_lines() == HAPPY


def test_filler_after_two_seconds(system):
    slow = "slow 3; drink=coffee"
    r = run(system, goal(schema=DRINKS), [slow, "size=large", "yes"])
    assert r.status == "succeeded", r
    fillers = r.fillers()
    assert len(fillers) == 1
    filler = fillers[0]
    assert filler["role"] == "robot" and filler["filler"] is True
    assert filler["text"] in load_language(system.language).filler_phrases
    # Said about 2 s after the user's turn, before the reply, which is said when it is ready
    assert 1.8 <= filler["t"] - _user_t(r, slow) <= 3.0
    assert filler["t"] < _robot_t(r, "Noted. [ask] size?")
    assert 2.8 <= _robot_t(r, "Noted. [ask] size?") - _user_t(r, slow) <= 4.5
    # In order in the transcript, right before the reply
    i = r.transcript.index(filler)
    assert r.transcript[i - 1]["text"] == slow
    assert r.transcript[i + 1]["text"] == "Noted. [ask] size?"
    # Not a line of the dialogue: the same lines, turns and feedback as without it
    assert r.robot_lines() == HAPPY
    assert r.result.total_turns == 3
    assert [f["utterance"] for f in r.feedback] == r.robot_lines()
    # The user answered the reply, not the filler
    assert r.user_lines() == [slow, "size=large", "yes"]


def test_the_filler_ladder_changes_its_wording_as_the_wait_grows(system):
    slow = "slow 24; drink=coffee"
    r = run(system, goal(schema=DRINKS), [slow, "size=large", "yes"], timeout=120)
    assert r.status == "succeeded", r
    fillers = r.fillers()
    # At about 2, 5, 9 and 14 s, each with its own phrase, then the last one at 22 s
    phrases = load_language(system.language).filler_phrases
    assert [f["text"] for f in fillers] == list(phrases)  # the fifth repeats after the ladder
    times = [f["t"] - _user_t(r, slow) for f in fillers]
    for t, due in zip(times, (2, 5, 9, 14, 22)):
        assert due - 0.3 <= t <= due + 1.5, times
    assert r.robot_lines() == HAPPY


def test_filler_in_a_slow_opening(system):
    # The same rule at the opening: a filler only because it takes more than 2 s
    r = run(system, goal(task="Take a drink order slow 3", schema=DRINKS),
            ["drink=coffee", "size=large", "yes"])
    assert r.status == "succeeded"
    assert len(r.fillers()) == 1
    assert r.transcript[0].get("filler") is True
    assert r.robot_lines() == HAPPY


def test_fillers_leave_the_counters_alone(system):
    # Turns without progress: the breakdown comes at the same turn as without fillers
    r = run(system, goal(schema=DRINKS),
            ["drink=coffee; size=large", "slow 2.5 maybe", "perhaps", "slow 2.5 whatever",
             "who knows"])
    assert r.status == "aborted"
    assert r.result.outcome == DialogTask.Result.OUTCOME_BREAKDOWN
    assert r.result.total_turns == 5
    assert len(r.fillers()) == 2
    lines = r.robot_lines()
    assert len(lines) == 6 and lines[-1] == "[close_breakdown]"
    assert [f["utterance"] for f in r.feedback] == lines
    # Silence: the robot repeats its last line of the dialogue, not the filler
    r = run(system, goal(schema=DRINKS, max_silence=6.0),
            ["slow 3; drink=coffee", None, None, None], timeout=60)
    assert len(r.fillers()) == 1
    assert r.robot_lines() == [
        "[opening] drink?", "Noted. [ask] size?", timeout_prompt(system),
        "Noted. [ask] size?", "[close_user_silent]"]
    assert r.result.outcome == DialogTask.Result.OUTCOME_USER_SILENT
    assert r.result.total_turns == 1


def test_fillers_disabled_by_the_request(system):
    r = run(system, goal(schema=DRINKS, filler_after_s=0.0),
            ["slow 3; drink=coffee", "size=large", "yes"])
    assert r.status == "succeeded"
    assert r.fillers() == []
    assert r.robot_lines() == HAPPY


def test_filler_time_of_the_request(system):
    slow = "slow 2.5; drink=coffee"
    r = run(system, goal(schema=DRINKS, filler_after_s=1.0), [slow, "size=large", "yes"])
    assert r.status == "succeeded"
    fillers = r.fillers()
    assert len(fillers) == 1  # at about 1 s; the second would be at 4 s
    assert 0.8 <= fillers[0]["t"] - _user_t(r, slow) <= 1.8
    # Unset (USE_DEFAULT_SECONDS): the system's 2 s, so this reply has no filler
    r = run(system, goal(schema=DRINKS, filler_after_s=DialogTask.Goal.USE_DEFAULT_SECONDS),
            ["slow 1; drink=coffee", "size=large", "yes"])
    assert r.fillers() == []


def test_barge_in_waits_for_the_reply_after_a_filler(system):
    # The user cuts the robot's next utterance: the reply, not the filler before it
    r = run(system, goal(schema=DRINKS),
            [UserTurn("slow 3; drink=tea; size=small", barge_in_after=0.3), "yes"])
    assert r.status == "succeeded"
    assert len(r.fillers()) == 1
    assert len(r.barge_ins) == 1
    assert r.barge_ins[0]["utterance"].startswith("Noted. [confirm]")
