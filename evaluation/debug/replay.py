# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The two checks that separate a program error from a model or prompt error in the debugging
before E0a (doc/DEBUGGING.md), on a dialogue recorded with its model calls
(record_model_calls of the debugging files).

    # (a) Oracle replay: the dialogue again, with one wrong answer of the model replaced by the
    #     correct one; if the tree still goes wrong, the error is the program's (C)
    python3 debug/replay.py plan <record.json> --turn K --question Q --answer '<raw answer>'
                                 [--out DIR]
    python3 scripts/run_experiment.py <DIR>/replay.yaml --hardware ... --lane local --out <DIR>
    python3 debug/replay.py report <record.json> <DIR>

    # (b) The same input to another model (the other family): the recorded prompt of one call,
    #     sent as the gateway sends it; the same wrong answer from both points to the prompt (P)
    python3 debug/replay.py same-input <record.json> --turn K --question Q [--index 1]
                                       --model llama3.1:8b [--host 127.0.0.1 --port 11434]

plan writes, next to nothing else (<results>/replays/<dialogue>__t<K>/ by default), a scenario
and an experiment file of one dialogue (the same model, condition, persona and language) in
which:
  - the user's turns 1..K are the lines the simulated user said in the recorded dialogue (as
    events, applied by code; a planned event keeps its kind), so the user side is the same up
    to the wrong turn; after K the simulated user plays on from its goal;
  - every call of the model in turns 0..K is pinned to the answer it got (a fault of kind
    `answer`, simulation/interaction_sim/interaction_sim/llm_faults.py; a call that failed is
    pinned as a failure, kind `error`), except question Q at turn K, which gets --answer, the
    raw text a correct model would have returned (JSON for an understanding question); after
    K the model answers live;
  - the scenario's own planned failures are kept after the pinned ones.
report prints the recorded and the replayed dialogue from turn K on, with the scoring of both.
same-input needs the GPU server (the model is asked); plan and report do not.

Limits (said in doc/DEBUGGING.md): a question asked several times in one turn gets its pinned
answers in the order recorded; a call that timed out is pinned as an error, which fails at once
instead of after the wait (the same fallback, a shorter turn); a call the client did not make
(the budget spent) was not recorded and is made in the replay unless the pinned failures before
it make the client skip it again; the robot's acknowledgment and generated lines up to K are the
pinned ones, so the user's recorded lines answer the same robot lines.
"""

import argparse
import copy
import glob
import json
import os
import sys
from collections import defaultdict

import yaml

EVAL = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PERSONAS = os.path.join(EVAL, "experiments", "personas.yaml")


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def calls_at(rec: dict, turn: int, question: str) -> list[dict]:
    return [c for c in rec.get("model_calls") or []
            if c.get("turn") == turn and c.get("question") == question]


def pinned_faults(rec: dict, turn: int, question: str, answer: str) -> list[dict]:
    """Faults that pin the recorded answers of turns 0..turn, with `answer` for question at
    turn (every call of that question in that turn)."""
    groups = defaultdict(list)
    order = []
    for c in rec.get("model_calls") or []:
        t = c.get("turn")
        if t is None or t > turn:
            continue
        key = (t, c.get("question"))
        if key not in groups:
            order.append(key)
        groups[key].append(c)
    out = []
    for key in order:
        t, q = key
        if (t, q) == (turn, question):
            out.append({"turn": t, "kind": "answer", "questions": [q], "answer": answer})
            continue
        answers = [c.get("answer") for c in groups[key]]
        if all(a is None for a in answers):
            out.append({"turn": t, "kind": "error", "questions": [q]})
        else:
            # A call that got nothing among calls that got an answer: the empty text, which
            # the client treats as no usable answer (it asks again or falls back)
            out.append({"turn": t, "kind": "answer", "questions": [q],
                        "answer": [a if a is not None else "" for a in answers]})
    return out


def user_events(rec: dict, turn: int) -> list[dict]:
    """The user's turns 1..turn as the recorded dialogue had them."""
    out = []
    for e in rec.get("user_log") or []:
        t = e.get("turn")
        if not t or t > turn or e.get("event") == "turn_limit":
            continue
        if e.get("event") == "silence" or e.get("action") == "end":
            out.append({"turn": t, "kind": "silence"})
            continue
        text = e.get("heard") or e.get("text") or ""
        kind = e.get("event") if e.get("event") in ("cancel", "text", "detour") else "text"
        event = {"turn": t, "kind": kind, "text": text}
        if kind == "detour":
            event["detour"] = e.get("detour") or "off_task"
        out.append(event)
    return out


def experiment_of(results: str) -> dict:
    """The experiment file the runner saved in the results folder (experiment*.yaml)."""
    found = sorted(glob.glob(os.path.join(results, "experiment*.yaml")))
    if not found:
        raise FileNotFoundError(f"No experiment*.yaml in {results}")
    with open(found[0], encoding="utf-8") as f:
        return yaml.safe_load(f)


def results_of(record_path: str) -> str:
    """The results folder of a record: <results>/runs/<model>/<condition>/<dialogue>.json."""
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(record_path)),
                                         "..", "..", ".."))


def plan(record_path: str, turn: int, question: str, answer: str,
         out: str | None = None) -> str:
    rec = load(record_path)
    if "model_calls" not in rec:
        sys.exit("The record has no model_calls: run the debugging file with "
                 "record_model_calls: true")
    if not calls_at(rec, turn, question):
        asked = sorted({(c.get("turn"), c.get("question")) for c in rec["model_calls"]})
        sys.exit(f"No call of {question} at turn {turn}. Calls: {asked}")
    results = results_of(record_path)
    exp = copy.deepcopy(experiment_of(results))
    name = os.path.basename(record_path)[:-5]
    out = os.path.abspath(out or os.path.join(results, "replays", f"{name}__t{turn}"))
    os.makedirs(out, exist_ok=True)
    pinned = pinned_faults(rec, turn, question, answer)
    planned = [f for f in rec.get("faults") or []]
    events = user_events(rec, turn)
    later = [e for e in rec.get("events") or [] if e.get("turn", 0) > turn]
    scenario = {
        "id": f"{rec['scenario']}__replay_t{turn}",
        "strategy": rec.get("strategy"),
        "goal": rec.get("goal"),
        "user_goal": rec.get("user_goal"),
        "goal_items": rec.get("goal_items") or [],
        "expect": rec.get("expect") or {},
        "events": events + later,
        "faults": pinned + planned,
        "replay_of": {"record": os.path.abspath(record_path), "turn": turn,
                      "question": question, "answer": answer},
    }
    with open(os.path.join(out, "scenarios.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump({"scenarios": [scenario]}, f, sort_keys=False, allow_unicode=True)
    condition = (exp.get("conditions") or {}).get(rec.get("condition")) or {}
    exp.update({
        "name": f"replay_{name}_t{turn}",
        "scenarios": os.path.join(out, "scenarios.yaml"),
        "personas_file": PERSONAS,
        "personas": [rec["persona"]],
        "models": [rec["model"]],
        "repetitions": 1,
        "conditions": {rec.get("condition", "bounded"): condition},
        "record_model_calls": True,
    })
    for key in ("scenario_ids", "scenarios_per_strategy", "hardware"):
        exp.pop(key, None)
    path = os.path.join(out, "replay.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Oracle replay of {record_path}, turn {turn}, question {question} "
                f"(debug/replay.py). Not an experiment.\n")
        yaml.safe_dump(exp, f, sort_keys=False, allow_unicode=True)
    return path


def _robot_lines(rec: dict) -> list[str]:
    return [t["text"] for t in rec.get("transcript") or []
            if t["role"] == "robot" and not t.get("filler")]


def report(record_path: str, replay_dir: str) -> list[str]:
    sys.path.insert(0, os.path.join(EVAL, "analysis"))
    import score_runs  # noqa: E402

    rec = load(record_path)
    found = score_runs.run_files(replay_dir)
    if not found:
        return [f"No replayed dialogue in {replay_dir} (run the replay first)"]
    new = load(found[0])
    turn = (new.get("faults") or [{}])[0].get("turn", 0)
    with open(os.path.join(replay_dir, "scenarios.yaml"), encoding="utf-8") as f:
        plan_ = yaml.safe_load(f)["scenarios"][0].get("replay_of", {})
    turn = plan_.get("turn", turn)
    lines = [f"Replay of {record_path}: turn {turn}, {plan_.get('question')} answered "
             f"{plan_.get('answer')!r}"]
    for label, r in (("recorded", rec), ("replayed", new)):
        row = score_runs.score(r)
        lines.append(f"{label:9s} outcome {r.get('outcome')}, task_success "
                     f"{row.get('task_success')}, mechanisms_ok {row.get('mechanisms_ok')}, "
                     f"ending_ok {row.get('ending_ok')}: {r.get('failure_reason') or '-'}")
    a, b = _robot_lines(rec), _robot_lines(new)
    lines.append(f"Robot lines from turn {turn} (recorded | replayed):")
    for k in range(turn, max(len(a), len(b))):
        lines.append(f"  {k:2d} {a[k] if k < len(a) else '-'}")
        lines.append(f"     {b[k] if k < len(b) else '-'}")
    lines.append("If the replayed dialogue still goes wrong where the recorded one did, with the "
                 "correct answer given, the error is the program's (C); read both sheets "
                 "(triage.py sheet) before classifying.")
    return lines


def same_input(record_path: str, turn: int, question: str, index: int, model: str,
               host: str, port: int, num_ctx: int = 8192) -> list[str]:
    """Ask `model` the recorded call, as the gateway asks it (its Ollama provider: think off,
    the context window, temperature 0 for an understanding question)."""
    rec = load(record_path)
    calls = calls_at(rec, turn, question)
    if not calls or not 1 <= index <= len(calls):
        sys.exit(f"No call {index} of {question} at turn {turn}")
    call = calls[index - 1]
    from ros2_llm_manager.providers.ollama_provider import OllamaProvider

    sys.path.insert(0, os.path.join(EVAL, "offline", "core"))
    import ollama_models  # noqa: E402

    provider = OllamaProvider(host, port, 180.0, model, think=False, num_ctx=num_ctx)
    ok, message = provider.connect()
    if not ok:
        sys.exit(f"Ollama at {host}:{port} does not answer: {message}")
    try:
        result = provider.generate(model, [{"role": "user", "content": call["prompt"]}],
                                   int(call.get("max_tokens") or 0), call.get("schema"))
    finally:
        provider.close()
        # The provider keeps the model loaded (keep_alive -1): nothing stays on the GPU
        ollama_models.unload_all(f"http://{host}:{port}", [model])
    answer = result.text if getattr(result, "success", False) else f"ERROR {result.error}"
    return [f"{question}, turn {turn}, call {index}",
            f"  {rec.get('model')} (recorded): {call.get('answer')!r}",
            f"  {model}: {answer!r}"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("record")
    p.add_argument("--turn", type=int, required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--answer", required=True,
                   help="The raw text a correct model returns (JSON for an understanding "
                        "question, as the recorded answers are)")
    p.add_argument("--out", default=None)
    p = sub.add_parser("report")
    p.add_argument("record")
    p.add_argument("replay_dir")
    p = sub.add_parser("same-input")
    p.add_argument("record")
    p.add_argument("--turn", type=int, required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--index", type=int, default=1)
    p.add_argument("--model", required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=11434)
    args = parser.parse_args(argv)
    if args.command == "plan":
        path = plan(args.record, args.turn, args.question, args.answer, args.out)
        print(f"Replay experiment: {path}\nRun it (GPU server free, workspace sourced):\n"
              f"  python3 scripts/run_experiment.py {path} --hardware "
              f"experiments/hardware/single_gpu.yaml --lane local --out {os.path.dirname(path)}"
              f"\nThen: python3 debug/replay.py report {args.record} {os.path.dirname(path)}")
        return 0
    if args.command == "report":
        print("\n".join(report(args.record, args.replay_dir)))
        return 0
    print("\n".join(same_input(args.record, args.turn, args.question, args.index, args.model,
                               args.host, args.port)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
