# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The annotator of the simulated user's lines (annotate_turns.py; decided by the author,
2026-10-31): one call per dialogue with the goal items and the numbered user lines, marks in a
sidecar file (the run records are never changed), merged by load_record for the adherence check
and the grounded-QA scoring, and compared with the simulator's own declarations of earlier runs."""

import json
import os

import annotate_turns as at
import score_runs

ITEMS = [{"id": "q1", "value": "shares"}, {"id": "q2", "value": "child day pass"},
         {"id": "q3", "value": "first aid"}]


def record(sim_declared=False):
    log = [{"turn": 1, "robot": "Hello!", "text": "Should I buy shares?"},
           {"turn": 2, "robot": "That is outside what we talk about.",
            "text": "How much is a day pass for a child of eight?"},
           {"turn": 3, "robot": "30 euros.", "text": "Thanks, that was helpful."}]
    if sim_declared:
        log[0]["revealed"] = [{"id": "q1", "value": "shares"}]
        log[1]["revealed"] = []  # the simulator forgot to declare q2
        log[2]["revealed"] = []
    return {"strategy": "grounded_qa", "goal_items": ITEMS, "user_log": log}


def answer(*lines):
    return json.dumps({"lines": [{"n": n, "request": r, "items": [{"id": i, "value": "x"}
                                                                  for i in ids]}
                                 for n, (r, ids) in enumerate(lines, 1)]})


def test_one_call_per_dialogue_with_the_items_and_the_numbered_lines():
    prompt, schema, turns = at.build(record())
    assert '- q2: "child day pass"' in prompt and "U2: How much is a day pass" in prompt
    assert "Robot: 30 euros." in prompt and turns == [1, 2, 3]
    lines = schema["properties"]["lines"]
    assert lines["minItems"] == lines["maxItems"] == 3
    assert lines["items"]["properties"]["items"]["items"]["properties"]["id"]["enum"] == \
        ["q1", "q2", "q3"]
    assert at.build({"goal_items": [], "user_log": record()["user_log"]}) is None


def test_the_marks_per_turn_and_a_line_left_out_is_unknown():
    marks = at.parse(answer(("yes", ["q1"]), ("yes", ["q2"])), [1, 2, 3])
    assert marks[1] == {"turn": 2, "revealed": [{"id": "q2", "value": "x"}], "request": "yes"}
    assert marks[2] == {"turn": 3, "revealed": []}  # not covered: request unknown
    assert at.parse("not json", [1]) == [{"turn": 1, "revealed": []}]


def test_the_sidecar_is_merged_and_the_record_is_not_changed(tmp_path):
    run = tmp_path / "runs" / "m" / "bounded" / "qa__cooperative__r1.json"
    run.parent.mkdir(parents=True)
    run.write_text(json.dumps(record(sim_declared=True)))
    calls = []

    def ask(prompt, schema, max_tokens):
        calls.append(prompt)
        return answer(("yes", ["q1"]), ("yes", ["q2"]), ("no", []))

    annotation = at.annotate(json.loads(run.read_text()), ask, "gemma3:12b")
    side = at.sidecar(str(run))
    assert side.endswith(os.path.join("annotations", "m", "bounded", "qa__cooperative__r1.json"))
    os.makedirs(os.path.dirname(side))
    with open(side, "w") as f:
        json.dump(annotation, f)
    rec = at.load_record(str(run))
    assert len(calls) == 1 and rec["annotator"]["model"] == "gemma3:12b"
    assert rec["user_log"][1]["revealed"] == [{"id": "q2", "value": "x"}]
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
            ("yes", ["q1"]), ("yes", ["q2"]), ("no", [])), [1, 2, 3])}, f)
    loaded = at.load_record(str(run))
    out = score_runs.score_grounded_qa(loaded["data"], {"qa_resolutions": [
        "declined", "answered", "abstained"]}, loaded)
    assert (out["qa_false_questions"], out["qa_false_with_info"], out["qa_never_asked"]) == \
        (1, 1, 1)
