# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The annotator of the simulated user's lines (annotate_turns.py): one call per dialogue with the
goal items and the numbered user lines; for each item a line states, the model chooses the
scripted value, the planned change or "other" and quotes the line, and the mark holds the
scenario's own string for the first two; marks in a sidecar file (the run records are never
changed), merged by load_record for the adherence check and the grounded-QA scoring; an answer
still unusable after the retries is written as a failure marker, read as not annotated. No model
is called: the model is a stub."""

import json
import os

import adherence
import annotate_turns as at
import score_runs

ITEMS = [{"id": "q1", "value": "shares"}, {"id": "q2", "value": "child day pass"},
         {"id": "q3", "value": "first aid"}]


def record(sim_declared=False, items=ITEMS, log=None):
    log = log or [{"turn": 1, "robot": "Hello!", "text": "Should I buy shares?"},
                  {"turn": 2, "robot": "That is outside what we talk about.",
                   "text": "How much is a day pass for a child of eight?"},
                  {"turn": 3, "robot": "30 euros.", "text": "Thanks, that was helpful."}]
    if sim_declared:
        log[0]["revealed"] = [{"id": "q1", "value": "shares"}]
        log[1]["revealed"] = []  # the simulator forgot to declare q2
        log[2]["revealed"] = []
    return {"strategy": "grounded_qa", "goal_items": items, "user_log": log}


def answer(*lines):
    """The model's answer: per line (request, [ids] or [(id, choice, quote, value)])."""
    def item(x):
        if isinstance(x, str):
            return {"id": x, "quote": "q", "choice": "scripted", "value": ""}
        ident, choice, quote, value = x
        return {"id": ident, "quote": quote, "choice": choice, "value": value}
    return json.dumps({"lines": [{"n": n, "request": r, "items": [item(i) for i in ids]}
                                 for n, (r, ids) in enumerate(lines, 1)]})


def model(items_raw, verify=lambda prompt: '{"reason": "", "answer": "yes"}'):
    """A stub of the model: the items prompt gets items_raw, the request prompt the same answer
    (its "request"), a second look verify(prompt)."""
    calls = []

    def ask(prompt, schema, max_tokens, attempt):
        kind = ("verify" if "Look at line U" in prompt else
                "items" if "each with its SCRIPTED value" in prompt else "request")
        calls.append(kind)
        return verify(prompt) if kind == "verify" else items_raw
    ask.calls = calls
    return ask


def test_one_call_per_dialogue_with_the_items_and_the_numbered_lines():
    prompt, schema, turns = at.build(record())
    assert '- q2: "child day pass"' in prompt and "U2: How much is a day pass" in prompt
    assert "Robot: 30 euros." in prompt and turns == [1, 2, 3]
    lines = schema["properties"]["lines"]
    assert lines["minItems"] == lines["maxItems"] == 3
    # One alternative per goal item; "change" only for an item with a planned change
    alts = lines["items"]["properties"]["items"]["items"]["anyOf"]
    assert [a["properties"]["id"]["enum"] for a in alts] == [["q1"], ["q2"], ["q3"]]
    assert alts[0]["properties"]["choice"]["enum"] == ["scripted", "other"]
    _, schema, _ = at.build(record(items=[{"id": "t", "value": "60", "then": "40"}]))
    alt = schema["properties"]["lines"]["items"]["properties"]["items"]["items"]["anyOf"][0]
    assert alt["properties"]["choice"]["enum"] == ["scripted", "change", "other"]
    assert at.build({"goal_items": [], "user_log": record()["user_log"]}) is None


def test_the_model_chooses_and_the_mark_holds_the_scripted_string():
    items = [{"id": "temperature", "value": "coloured clothes at 60 degrees",
              "then": "coloured clothes at 40 degrees"}, {"id": "machines", "value": "4 or 5"}]
    raw = answer(("no", [("temperature", "scripted", "washed at 60 degrees", "60 degrees")]),
                 ("no", [("temperature", "change", "at 40 degrees", "")]),
                 ("no", [("temperature", "other", "at 30", "30 degrees"),
                         ("machines", "other", "machine 7", "")]))
    marks = at.parse(raw, [1, 2, 3], items)
    assert marks[0]["revealed"] == [{"id": "temperature", "value": "coloured clothes at 60 degrees",
                                     "choice": "scripted", "quote": "washed at 60 degrees"}]
    assert marks[1]["revealed"][0]["value"] == "coloured clothes at 40 degrees"
    assert marks[1]["revealed"][0]["choice"] == "then"
    # "other": the value said, or the quote when the model gave none
    assert [(m["value"], m["choice"]) for m in marks[2]["revealed"]] == \
        [("30 degrees", "other"), ("machine 7", "other")]
    # A change chosen for an item without one is not a planned value; an unknown id is dropped
    raw = answer(("no", [("machines", "change", "4", "4"), ("nope", "scripted", "x", "")]))
    assert at.parse(raw, [1], items)[0]["revealed"] == [
        {"id": "machines", "value": "4", "choice": "other", "quote": "4"}]


def test_a_paraphrase_chosen_as_scripted_is_on_script():
    items = [{"id": "temperature", "value": "coloured clothes at 60 degrees",
              "then": "coloured clothes at 40 degrees"}]
    log = [{"turn": 1, "robot": "And coloured clothes?",
            "text": "Coloured clothes are washed at 60 degrees."},
           {"turn": 2, "robot": "It is 40.", "text": "Right, at 40 degrees then."}]
    raw = answer(("no", [("temperature", "scripted", "washed at 60 degrees", "")]),
                 ("no", [("temperature", "change", "at 40 degrees", "")]))
    rec = at.merge({"goal_items": items, "user_log": log, "strategy": "explanation"},
                   {"annotator": {}, "turns": at.parse(raw, [1, 2], items)})
    c = adherence.check(rec, "e0a")
    assert c["adh_off_script"] == "" and not c["sim_excluded"] and c["adh_review"] == ""


def test_every_other_mark_gets_a_second_look():
    items = [{"id": "temperature", "value": "coloured clothes at 60 degrees",
              "then": "coloured clothes at 40 degrees"},
             {"id": "q1", "value": "366"}, {"id": "q3", "value": "forty"}]
    log = [{"turn": 1, "robot": "And coloured clothes?", "text": "At 60 degrees."},
           {"turn": 2, "robot": "What is your name?", "text": "My name is Alex."},
           {"turn": 3, "robot": "Hours in two days?", "text": "Forty-eight hours."},
           {"turn": 4, "robot": "It is 40.", "text": "Oh, forty then."},
           {"turn": 5, "robot": "Hours in two days?", "text": "That would be forty-eight."}]
    rec = {"strategy": "quiz", "goal_items": items, "user_log": log}
    marks = at.parse(answer(("no", [("temperature", "other", "At 60 degrees", "60 degrees")]),
                            ("no", [("q1", "other", "Alex", "Alex")]),
                            ("no", [("q3", "other", "Forty-eight", "forty-eight")]),
                            ("no", [("temperature", "other", "forty", "forty")]),
                            ("no", [("q3", "other", "forty-eight", "forty-eight")])),
                     [1, 2, 3, 4, 5], items)
    asked = []

    def verify(prompt):
        asked.append(prompt)
        if 'Look at line U2' in prompt:
            return '{"reason": "a name", "answer": "not about it"}'
        if 'Look at line U3' in prompt:
            return '{"reason": "48", "answer": "something else"}'
        if 'Look at line U5' in prompt:  # wrong: another number is not the scripted value
            return '{"reason": "", "answer": "yes"}'
        return '{"reason": "", "answer": "the change"}'
    at.verify_others(rec, marks, model("", verify))
    # a part of the scripted value: no call
    assert marks[0]["revealed"][0]["value"] == "coloured clothes at 60 degrees"
    assert marks[0]["revealed"][0]["verified"] == "part of the planned value"
    assert marks[1]["revealed"] == []  # a name is not the answer to q1
    assert marks[2]["revealed"][0]["value"] == "forty-eight"
    assert marks[2]["revealed"][0]["verified"] == "different"
    assert marks[2]["revealed"][0]["confirmed"] is True  # in the line, another number
    assert marks[3]["revealed"][0]["value"] == "coloured clothes at 40 degrees"
    assert marks[4]["revealed"][0]["verified"] == "different (another number)"
    assert len(asked) == 4 and '"the change"' in asked[2] and '"the change"' not in asked[0]
    # the whole conversation is in the second look, and the line it is about
    assert "U5: That would be forty-eight." in asked[0] and 'Look at line U2: "My name' in asked[0]

    def broken(prompt, schema, max_tokens, attempt):
        raise ConnectionError("down")
    marks = at.parse(answer(("no", []), ("no", []), ("no", [("q3", "other", "48", "48")]),
                            ("no", []), ("no", [])), [1, 2, 3, 4, 5], items)
    at.verify_others(rec, marks, broken)  # left as it was, but not evidence to exclude
    assert marks[2]["revealed"][0]["verified"] == "error"
    assert marks[2]["revealed"][0]["choice"] == "other"
    assert marks[2]["revealed"][0]["confirmed"] is False


def test_a_different_value_needs_evidence_besides_the_model_to_exclude():
    step = {"id": "step_4", "value": "cannot wind the thread under the button (three times)"}
    proc = {"strategy": "procedure"}
    assert at.confirmed(proc, step, "done", "Done.")  # a short answer, no word in common
    assert not at.confirmed(proc, step, "the button is sewn on", "Well, the button is sewn on.")
    assert not at.confirmed(proc, step, "done", "I give up.")  # not in the line
    quiz = {"strategy": "quiz"}
    assert at.confirmed(quiz, {"value": "forty"}, "forty-eight", "Forty-eight hours")
    assert at.confirmed(quiz, {"value": "40"}, "cuarenta y ocho", "Son cuarenta y ocho")
    assert not at.confirmed(quiz, {"value": "forty"}, "hours in two days", "That was tricky")
    role = {"strategy": "roleplay"}
    assert not at.confirmed(role, {"value": "greets the seller"}, "Thank you", "Thank you!")
    assert at.confirmed(role, {"value": "since last week"}, "two months", "For two months.") is \
        False  # no number in the scripted value: the model's judgement alone
    assert at.confirmed(role, {"value": "after 12", "then": "after 11"}, "at 9",
                        "It starts at 9")
    assert at.numbers("forty-eight, 12 and veintidós") == {48, 12, 22}
    # the adherence check: unconfirmed is flagged for review, never excluded
    items = [{"id": "greeting", "value": "greets the seller"}]
    log = [{"turn": 1, "text": "Thank you", "revealed": [
        {"id": "greeting", "value": "Thank you", "choice": "other", "confirmed": False}]}]
    c = adherence.check({"goal_items": items, "user_log": log, "strategy": "roleplay"}, "e0a")
    assert c["adh_unconfirmed"] == "greeting=Thank you" and c["adh_off_script"] == ""
    assert c["adh_check"] == "review" and not c["sim_excluded"]
    log[0]["revealed"][0]["confirmed"] = True
    assert adherence.check({"goal_items": items, "user_log": log}, "e0a")["sim_excluded"]


QN = {"strategy": "questionnaire", "goal": {"resources_json": json.dumps([{
    "name": "instrument", "content": json.dumps({
        "options": ["Never", "Once or twice a week", "Most days", "Every day"],
        "items": ["How often do you do crosswords?", "How often do you play cards?"]})}])}}


def test_a_questionnaire_option_goes_to_the_question_the_robot_read():
    items = [{"id": "item_1", "value": "Every day"},
             {"id": "item_2", "value": "about three times a week",
              "then": "Once or twice a week"}]
    opts = " The options are: Never, Once or twice a week, Most days or Every day."
    log = [{"turn": 1, "robot": "Question 1: How often do you do crosswords?" + opts,
            "text": "Every day."},
           {"turn": 2, "robot": "Question 2: How often do you play cards?" + opts,
            "text": "About three times a week."},
           {"turn": 3, "robot": opts, "text": "Most days."},
           {"turn": 4, "robot": opts, "text": "Once or twice a week, I mean."}]
    rec = dict(QN, goal_items=items, user_log=log)
    # the model marked the wrong item, and missed the last line
    marks = at.parse(answer(("no", ["item_1"]), ("no", ["item_2"]),
                            ("no", [("item_1", "other", "Most days", "Most days")]), ("no", [])),
                     [1, 2, 3, 4], items)
    at.recheck_marks(rec, marks)
    assert [(m["id"], m["choice"], m["value"]) for e in marks for m in e["revealed"]] == [
        ("item_1", "scripted", "Every day"), ("item_2", "scripted", "about three times a week"),
        ("item_2", "other", "Most days"), ("item_2", "then", "Once or twice a week")]
    assert marks[2]["revealed"][0]["confirmed"] is True
    at.verify_others(rec, marks, model("", lambda p: 1 / 0))  # decided: no second look
    c = adherence.check(at.merge(rec, {"turns": marks}), "e0a")
    assert c["adh_off_script"] == "item_2=Most days" and c["sim_excluded"]


def test_a_scripted_choice_whose_quote_has_other_numbers_gets_a_second_look():
    items = [{"id": "q3", "value": "forty"}]
    rec = {"strategy": "quiz", "goal_items": items,
           "user_log": [{"turn": 1, "robot": "Hours in two days?", "text": "Forty-eight."}]}
    marks = at.parse(answer(("no", [("q3", "scripted", "Forty-eight", "")])), [1], items)
    at.recheck_marks(rec, marks)
    assert marks[0]["revealed"][0]["choice"] == "other"
    assert marks[0]["revealed"][0]["value"] == "Forty-eight"


def test_a_stage_direction_is_shown_apart_and_the_mark_keeps_the_scripted_string():
    assert at.split_direction("cannot wind the thread (three times)") == \
        ("cannot wind the thread", "three times")
    assert at.split_direction("done") == ("done", "")
    item = {"id": "step_4", "value": "cannot wind the thread (three times)"}
    prompt, _, _ = at.build(record(items=[item]))
    assert '- step_4: "cannot wind the thread" (stage direction for the user: three times)' \
        in prompt
    raw = answer(("no", [("step_4", "scripted", "I cannot wind it", "")]), ("no", []), ("no", []))
    assert at.parse(raw, [1, 2, 3], [item])[0]["revealed"][0]["value"] == item["value"]


def test_the_marks_per_turn_and_a_line_left_out_is_unknown():
    marks = at.parse(answer(("yes", ["q1"]), ("yes", ["q2"])), [1, 2, 3], ITEMS)
    assert marks[1] == {"turn": 2, "request": "yes", "revealed": [
        {"id": "q2", "value": "child day pass", "choice": "scripted", "quote": "q"}]}
    assert marks[2] == {"turn": 3, "revealed": []}  # not covered: request unknown
    assert at.parse("not json", [1]) == [{"turn": 1, "revealed": []}]


def test_an_unusable_answer_is_retried_then_written_as_a_failure():
    calls = []
    good = answer(("yes", ["q1"]), ("yes", ["q2"]), ("no", []))

    def flaky(prompt, schema, max_tokens, attempt):
        calls.append((max_tokens, attempt))
        if attempt == 0:
            raise TimeoutError("read timed out")
        if attempt == 1:
            return '{"lines": [{"n": 1, "request": "yes", "items": []'  # truncated
        return good
    out = at.annotate(record(), flaky, "m")
    # the items: two failures, then the answer; the requests: the same
    assert [a for _, a in calls] == [0, 1, 2, 0, 1, 2] and calls[1][0] > calls[0][0]
    assert "failed" not in out and out["turns"][1]["revealed"][0]["id"] == "q2"
    assert out["turns"][1]["request"] == "yes"

    out = at.annotate(record(), model(answer(("yes", ["q1"]))), "m")  # one line of three
    assert out["failed"] is True and out["turns"] == [] and out["attempts"] == at.ATTEMPTS
    assert "lines not covered: [2, 3]" in out["reason"]
    # one entry per line but numbered otherwise: read by position
    raw = json.dumps({"lines": [{"n": 1, "request": "yes", "items": []},
                                {"n": 1, "request": "no", "items": []},
                                {"n": 2, "request": "no", "items": []}]})
    out = at.annotate(record(), model(raw), "m")
    assert "failed" not in out and [t["request"] for t in out["turns"]] == ["yes", "no", "no"]


def test_the_requests_are_marked_with_the_first_annotators_prompt():
    prompt, schema = at.build_request(record())
    assert "Copy the value\n  as the list gives it" in prompt and '- q2: "child day pass"' in prompt
    assert schema["properties"]["lines"]["items"]["required"] == ["n", "request", "items"]
    ask = model(answer(("yes", ["q1"]), ("yes", ["q2"]), ("no", [])))
    out = at.annotate(record(), ask, "m")
    assert ask.calls == ["items", "request"]
    assert [t["request"] for t in out["turns"]] == ["yes", "yes", "no"]


def test_a_failed_annotation_is_not_annotated_never_a_user_who_said_nothing(tmp_path):
    run = tmp_path / "runs" / "m" / "bounded" / "sf__cooperative__r1.json"
    run.parent.mkdir(parents=True)
    items = [{"id": "flavour", "value": "lemon", "then": "almond"}]
    log = [{"turn": 1, "robot": "Flavour?", "text": "Lemon."},
           {"turn": 2, "robot": "Anything else?", "text": "No, bye."}]
    rec = {"strategy": "slot_filling", "goal_items": items, "user_log": log,
           "outcome": "user_silent", "expect": {"outcome": "success"}}
    run.write_text(json.dumps(rec))
    side = at.sidecar(str(run))
    os.makedirs(os.path.dirname(side))
    with open(side, "w") as f:
        json.dump({"annotator": {"model": "m"}, "failed": True, "reason": "not JSON",
                   "turns": []}, f)
    loaded = at.load_record(str(run))
    assert loaded["annotation_failed"] == "not JSON" and not at.annotated(str(run))
    assert all("revealed" not in e for e in loaded["user_log"])
    for rule in adherence.RULES:  # "user_silent" would exclude a user who said nothing
        c = adherence.check(loaded, rule)
        assert c["adh_check"] == "unannotated" and c["sim_excluded"] is False
    # A record never annotated reads the same
    assert adherence.check(rec, "user_silent")["adh_check"] == "unannotated"
    with open(side, "w") as f:
        json.dump({"annotator": {"model": "m"}, "turns": []}, f)
    assert at.annotated(str(run))


def test_the_command_counts_failures_and_tries_them_again(tmp_path, monkeypatch, capsys):
    import sys
    import types
    run = tmp_path / "runs" / "m" / "bounded" / "qa__cooperative__r1.json"
    run.parent.mkdir(parents=True)
    run.write_text(json.dumps(record()))
    answers = iter(["oops"] * at.ATTEMPTS + [answer(("yes", ["q1"]), ("yes", ["q2"]),
                                                    ("no", []))] * 2)
    fake = types.ModuleType("interaction_sim.cloud_llm")
    fake.ask_json = lambda *a, **k: (next(answers), {})
    monkeypatch.setitem(sys.modules, "interaction_sim.cloud_llm", fake)
    monkeypatch.setitem(sys.modules, "interaction_sim", types.ModuleType("interaction_sim"))
    out_dir = tmp_path / "scratch"
    monkeypatch.setattr(sys, "argv", ["annotate_turns.py", str(tmp_path), "--out", str(out_dir)])
    at.main()
    assert "annotated 0, failed 1" in capsys.readouterr().out
    side = out_dir / "m" / "bounded" / "qa__cooperative__r1.json"
    assert json.loads(side.read_text())["failed"] is True
    assert not os.path.exists(at.sidecar(str(run)))  # --out: the real folder is untouched
    at.main()  # the failure is tried again
    assert "annotated 1, failed 0" in capsys.readouterr().out


def test_the_sidecar_is_merged_and_the_record_is_not_changed(tmp_path):
    run = tmp_path / "runs" / "m" / "bounded" / "qa__cooperative__r1.json"
    run.parent.mkdir(parents=True)
    run.write_text(json.dumps(record(sim_declared=True)))
    ask = model(answer(("yes", ["q1"]), ("yes", ["q2"]), ("no", [])))
    annotation = at.annotate(json.loads(run.read_text()), ask, "gemma3:12b")
    side = at.sidecar(str(run))
    assert side.endswith(os.path.join("annotations", "m", "bounded", "qa__cooperative__r1.json"))
    os.makedirs(os.path.dirname(side))
    with open(side, "w") as f:
        json.dump(annotation, f)
    rec = at.load_record(str(run))
    assert ask.calls == ["items", "request"] and rec["annotator"]["model"] == "gemma3:12b"
    assert [(m["id"], m["value"]) for m in rec["user_log"][1]["revealed"]] == \
        [("q2", "child day pass")]
    assert rec["user_log"][1]["revealed_sim"] == []
    assert rec["user_log"][2]["request"] == "no"
    assert "revealed_sim" not in json.loads(run.read_text())["user_log"][0]  # untouched
    # Compared with the simulator's own declarations: the line it forgot is a disagreement
    out = at.compare(str(tmp_path))
    assert (out["lines"], out["items_agree"]) == (3, 2)
    assert out["disagree"][0]["turn"] == 2


def test_the_grounded_qa_scoring_reads_the_annotators_marks(tmp_path):
    run = tmp_path / "runs" / "m" / "bounded" / "qa__cooperative__r1.json"
    run.parent.mkdir(parents=True)
    rec = record()
    rec["data"] = {"questions": [
        {"question": "Should I buy shares?", "resolution": "declined"},
        {"question": "How much is a day pass for a child of eight?", "resolution": "answered"},
        {"question": "Thanks, that was helpful.", "resolution": "answered", "answer": "9:00"}]}
    run.write_text(json.dumps(rec))
    side = at.sidecar(str(run))
    os.makedirs(os.path.dirname(side))
    with open(side, "w") as f:
        json.dump({"annotator": {"model": "m"}, "turns": at.parse(answer(
            ("yes", ["q1"]), ("yes", ["q2"]), ("no", [])), [1, 2, 3], ITEMS)}, f)
    loaded = at.load_record(str(run))
    out = score_runs.score_grounded_qa(loaded["data"], {"qa_resolutions": [
        "declined", "answered", "abstained"]}, loaded)
    assert (out["qa_false_questions"], out["qa_false_with_info"], out["qa_never_asked"]) == \
        (1, 1, 1)
