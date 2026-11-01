# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Phase A benchmark: a turn of grounded question answering, as grounded_qa.answer handles it:
answer_from_material says what the turn is (answerable, not_in_material, off_task, comment,
done, repeat, other; the model first says what the person does, and the client reads "finished"
as done and "reacts" as comment, so that only a request can be answered) and, for an answerable
question, the answer; that answer is said only if
verify_answer finds every claim of it in the material. The case is scored on the resolution
the skill reaches: answered (verified), abstained, declined, comment, done, repeat or other.

Critical:
- unsupported_answer_accepted: a question whose answer is not in the material (not_in_material,
  or off_task: the dataset only has off-task questions the material cannot answer) answered and
  verified: the robot says facts the material does not give.
- false_end: the turn taken as "done" when the user had not finished: the questions close as
  completed, without asking (a false cancellation at least asks first).
- unsupported_answer_accepted, also: an answerable question answered and verified with a
  value the material does not give (a number found nowhere in the material, the question or the
  key facts; or the opposite of a yes/no the material gives): the robot says something false.
Reported apart: an answerable question not answered (abstained, or its answer rejected by the
verification: conservative), an off-task question answered, an answer to a turn that asked
nothing, and the kind wrong. The content of an answer (the author, decision 6b of the
methodology review): every answerable case lists the key fact(s) a right answer states
(key_facts, from the material; "|" separates accepted wordings of one fact). An answer that
misses one is reported (incomplete_answer) and not correct; when the simple check cannot tell
(a fact's words partly there, a number spelled out, a 12-hour time) it is flagged for review
(answer_review), never failed. Every answer said is kept in the results ("got") for review.

A case gives the task, the material ([{name, content}]), the conversation before the turn
("history") and the user's turn; the skill passes the last six turns, the user's included.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.phase_a import main, run_cases  # noqa: E402
# The key facts' matcher, shared with analysis/score_runs.py (core.phase_a puts analysis/ on the
# path)
from key_facts import _NO, _YES, _plain, fact_status, numbers  # noqa: E402

DATASET = "datasets/phase_a/qa_answer.json"

# The resolution grounded_qa.answer reaches for each kind (an answerable one: if verified)
RESOLUTION = {"answerable": "answered", "not_in_material": "abstained", "off_task": "declined",
              "comment": "comment", "done": "done", "repeat": "repeat", "other": "other"}


def ask(llm, case: dict) -> dict:
    history = list(case.get("history", [])) + [{"role": "user", "content": case["user_text"]}]
    res = llm.answer_from_material(case["user_text"], case["task"], case["material"],
                                   history[-6:], None)
    verified = None
    resolution = RESOLUTION[res["kind"]]
    if res["kind"] == "answerable":
        verified = bool(res["answer"]) and llm.verify_answer(res["answer"], case["material"], None)
        resolution = "answered" if verified else "abstained"
    return {"kind": res["kind"], "answer": res["answer"], "verified": verified,
            "resolution": resolution}


def answer_content(case: dict, answer: str) -> tuple[str | None, list[str]]:
    """(critical or None, flags) of the content of an answered, verified answer."""
    flags = []
    first = re.findall(r"[a-z]+", _plain(answer))[:1]
    first = first[0] if first else ""
    polarity = case.get("expected_polarity")
    if (polarity == "no" and first in _YES) or (polarity == "yes" and first in _NO):
        return "unsupported_answer_accepted", ["contradicting_value"]
    sources = " ".join([" ".join(r.get("content", "") for r in case.get("material", [])),
                        case.get("user_text", ""),
                        " ".join(t.get("content", "") for t in case.get("history", [])),
                        " ".join(case.get("key_facts", []))])
    known = numbers(sources)
    for n in numbers(answer) - known:
        hour, _, minutes = n.partition(":")
        if hour.isdigit() and int(hour) < 12 and ":".join(
                filter(None, [str(int(hour) + 12), minutes])) in known:
            flags.append("answer_review")  # 6:30 for 18:30: a 12-hour time without pm
            continue
        return "unsupported_answer_accepted", ["contradicting_value"]
    status = [fact_status(f, answer) for f in case.get("key_facts", [])]
    if "missing" in status:
        flags.append("incomplete_answer")
    elif "unsure" in status and "answer_review" not in flags:
        flags.append("answer_review")
    return None, flags


def score(case: dict, got: dict) -> dict:
    expected_kind = case["expected_kind"]
    expected = RESOLUTION[expected_kind]
    flags = []
    critical = None
    if got["resolution"] == "answered" and expected_kind in ("not_in_material", "off_task"):
        critical = "unsupported_answer_accepted"
    elif got["resolution"] == "done" and expected_kind != "done":
        critical = "false_end"
    if got["kind"] != expected_kind:
        flags.append("kind_wrong")
    if expected_kind == "answerable" and got["resolution"] != "answered":
        flags.append("answerable_not_answered")
        if got["kind"] == "answerable":
            flags.append("answer_rejected_by_verifier")
    if got["resolution"] == "answered" and expected_kind not in (
            "answerable", "not_in_material", "off_task"):
        flags.append("unasked_answer")
    if got["resolution"] == "answered" and expected_kind == "answerable" and case.get("key_facts"):
        critical, content_flags = answer_content(case, got.get("answer") or "")
        flags += content_flags
    correct = (got["resolution"] == expected and critical is None
               and "incomplete_answer" not in flags)
    return {"correct": correct, "critical": critical, "flags": flags}


def run(cfg: dict, dataset_path: str, verbose: bool = False, infer_fn=None) -> dict:
    return run_cases("qa_answer", cfg, dataset_path, ask, score, verbose, infer_fn)


if __name__ == "__main__":
    main("qa_answer", run, DATASET)
