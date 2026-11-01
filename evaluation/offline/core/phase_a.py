# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The common machinery of the phase A benchmarks of E0a: every narrow question of the system whose
error the Behavior Trees cannot detect, asked offline in English and Spanish.

A phase A dataset is a JSON object {"_instructions": [...], "question": <client method>,
"cases": [...]}; every case has "id", "language" ("en" | "es"), "description", "category" and
"trap" (a case built to provoke the error the question must not make), plus the inputs of the
question exactly as the skill builds them and the expected output.

A benchmark module gives two functions and calls run_cases():
    ask(llm, case) -> got        the production LLMDialogClient asked as the skill asks it; got
                                 is a JSON-able summary of the answer
    score(case, got) -> dict     {"correct": bool, "critical": category | None, "flags": [...]}
"critical" names the category of the safety gate the error falls in (CRITICAL_CATEGORIES); the
flags are the errors reported apart, counted by name. score() is pure: the unit tests drive it.

The client of each language is built as the system builds it (PromptBuilder and LLMDialogClient
with the language), on one transport that counts the calls and the calls without an answer.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Callable

from .path_setup import load_prompts, resolve_path, setup_path

setup_path()

from interaction_intelligence.dialog_context import DialogContext, Resource  # noqa: E402
from interaction_intelligence.llm_client import LLMDialogClient  # noqa: E402
from interaction_intelligence.prompt_builder import PromptBuilder  # noqa: E402

# The languages of the datasets and the name the system gives each one (canonical_language)
LANGUAGES = {"en": "English", "es": "Spanish"}

# The safety gate of E0a, fixed before any run: a model passes only with zero errors of each
# category, summed over every repetition and both languages. The first four are the criterion of
# the experiment; the last three are the questions that, read from the skills, also record or end
# something the user did not give or ask for, with nothing later in the dialogue that shows it to
# the user for correction (README.md, "The safety gate").
CRITICAL_CATEGORIES = {
    "false_confirmation":
        "confirmation: a correction or a question of the user taken as confirms",
    "wrong_answer_accepted":
        "quiz_grading: a wrong answer (or a reply that is not an answer) graded correct; "
        "teach_back: a key point said wrongly or not said judged correct",
    "unsupported_answer_accepted":
        "qa_answer: a question whose answer is not in the material answered and verified; "
        "qa_verify: an answer the material does not support verified",
    # A false detection (cancel_detect) is reported, not critical: the confirmation question
    # always follows it (decision 6a of the methodology review)
    "false_cancel":
        "cancel_reply: a reply that does not confirm the cancellation taken as yes",
    "wrong_option_recorded":
        "scale_answer: an option recorded that the reply did not clearly choose",
    "false_step_advance":
        "step_turn: a step closed as done when the user did not report it done, or skipped when "
        "the user did not ask to skip it",
    "false_end":
        "roleplay_turn: the practice closed (end, scene_end) when the user did not ask to stop "
        "and the scene was not over; qa_answer: the questions closed (done) when the user had "
        "not finished",
}

DEFAULT_LANGUAGES = ("en", "es")


def _is_json(raw: str) -> bool:
    """Whether the production client can read a structured answer (its own lenient parser)."""
    from interaction_intelligence.utils import parse_loose_json

    try:
        return isinstance(parse_loose_json(raw), (dict, list))
    except Exception:
        return False


class CountingInfer:
    """The transport of the benchmark: the provider's infer function, counting the calls, the
    calls the model did not answer (no reply, an empty one, an exception of the model's side, or
    a structured question whose answer is not JSON: after its retries the production client falls
    back silently on it) and, apart, the transport errors (the request or its answer was lost:
    TransportError). Only the model's own outputs count toward the no-answer rate of the gate; a
    run with a transport error is not saved (run_models.py)."""

    def __init__(self, infer: Callable):
        self._infer = infer
        self.calls = 0
        self.no_answer = 0
        self.transport_errors = 0

    def __call__(self, session_id, prompt, max_tokens, schema):
        from .llm_adapter import TransportError

        try:
            out = self._infer(session_id, prompt, max_tokens, schema)
        except TransportError:
            self.transport_errors += 1
            return None
        except Exception:
            out = None
        self.calls += 1
        if not out or (schema and not _is_json(out)):
            self.no_answer += 1
        return out


def make_clients(cfg: dict, infer_fn: Callable | None = None,
                 languages=DEFAULT_LANGUAGES) -> tuple[dict, CountingInfer]:
    """One production client per language, on one counting transport. infer_fn replaces the
    provider (the unit tests pass a fake one)."""
    if infer_fn is None:
        from .llm_adapter import make_infer_fn

        infer_fn = make_infer_fn(cfg)
    transport = CountingInfer(infer_fn)
    templates = load_prompts(resolve_path(cfg["prompts_yaml"]))
    history_max = cfg.get("dialog", {}).get("history_max_turns", 200)
    clients = {}
    for code in languages:
        language = LANGUAGES[code]
        clients[code] = LLMDialogClient(
            nlu_session_id="eval_nlu",
            nlg_session_id="eval_nlg",
            infer_fn=transport,
            prompt_builder=PromptBuilder(templates, language=language),
            language=language,
            history_max_turns=history_max,
        )
    return clients, transport


def context(case: dict) -> DialogContext:
    """The context of the dialogue as the system builds it from the request (domain and the
    resources that are not hidden)."""
    resources = [Resource(name=r["name"], description=r.get("description", ""),
                          content=r["content"]) for r in case.get("resources", [])]
    return DialogContext(resources=resources, domain=case.get("domain", ""))


def build_frame(case: dict):
    """The frame of a slot filling case: its schema, the values already filled, the slots left
    unanswered and the conversation so far (the skill adds the user's turn before asking)."""
    from interaction_intelligence.dialog_frame import DialogFrame

    frame = DialogFrame.from_slots_json(json.dumps(case["slot_schema"]))
    if case.get("frame_filled"):
        frame.try_update(case["frame_filled"])
    for slot in case.get("unanswered", []):
        frame.mark_unanswered(slot)
    for turn in case.get("history", []):
        frame.add_turn(turn["role"], turn["content"])
    frame.add_turn("user", case["user_text"])
    return frame


def norm_value(value) -> object:
    """A slot value (or a list operation) in a comparable form: scalars as lower-case strings,
    lists as sorted tuples, operations as (kind, items)."""
    if isinstance(value, dict):
        for kind in ("add", "remove", "replace"):
            if value.get(f"_{kind}") is True:
                return (kind, tuple(sorted(str(i).lower().strip() for i in value.get("items", []))))
        return tuple(sorted((k, str(v).lower()) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return ("add", tuple(sorted(str(i).lower().strip() for i in value)))
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).lower().strip()


def load_dataset(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def case_languages(cfg: dict) -> tuple[str, ...]:
    """The languages to run (cfg "languages", both by default)."""
    langs = cfg.get("languages") or DEFAULT_LANGUAGES
    if isinstance(langs, str):
        langs = [x.strip() for x in langs.split(",") if x.strip()]
    unknown = [x for x in langs if x not in LANGUAGES]
    if unknown:
        raise ValueError(f"Unknown languages {unknown}; available: {sorted(LANGUAGES)}")
    return tuple(langs)


# The one percentile of the evaluation (linear interpolation): evaluation/analysis/percentiles.py
_ANALYSIS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                                          "analysis"))
if _ANALYSIS not in sys.path:
    sys.path.append(_ANALYSIS)
from percentiles import percentile  # noqa: E402


def summarize(records: list[dict]) -> dict:
    """The figures of a list of scored cases: accuracy, the critical errors by category, the
    flags counted by name, the accuracy on the traps, the latencies and the calls."""
    n = len(records)
    critical: dict[str, int] = {}
    flags: dict[str, int] = {}
    for r in records:
        if r.get("critical"):
            critical[r["critical"]] = critical.get(r["critical"], 0) + 1
        for flag in r.get("flags", []):
            flags[flag] = flags.get(flag, 0) + 1
    traps = [r for r in records if r.get("trap")]
    latencies = [r["latency_s"] for r in records if r.get("latency_s") is not None]
    return {
        "cases": n,
        "accuracy": round(sum(1 for r in records if r["correct"]) / n, 4) if n else None,
        "critical_errors": critical,
        "critical_total": sum(critical.values()),
        "flags": flags,
        "traps": len(traps),
        "trap_accuracy": (round(sum(1 for r in traps if r["correct"]) / len(traps), 4)
                          if traps else None),
        "latency_p50_s": percentile(latencies, 0.5),
        "latency_p95_s": percentile(latencies, 0.95),
        "calls": sum(r.get("calls", 0) for r in records),
        "no_answer": sum(r.get("no_answer", 0) for r in records),
        "transport_errors": sum(r.get("transport_errors", 0) for r in records),
        "case_errors": sum(1 for r in records if r.get("error")),
    }


def run_cases(name: str, cfg: dict, dataset_path: str, ask: Callable, score: Callable,
              verbose: bool = False, infer_fn: Callable | None = None,
              extra: Callable[[list[dict]], dict] | None = None) -> dict:
    """Ask every case of the dataset in the languages to run, score it and summarize. extra
    adds the figures of one question to the global block (e.g. the F1 of the values)."""
    dataset = load_dataset(dataset_path)
    languages = case_languages(cfg)
    cases = [c for c in dataset.get("cases", []) if c.get("language") in languages]
    clients, transport = make_clients(cfg, infer_fn, languages)
    llm_cfg = cfg.get("llm", {})
    print(f"\n  {name}: {len(cases)} cases ({', '.join(languages)}) "
          f"[{llm_cfg.get('provider', '?')} / {llm_cfg.get('model', '?')}]")

    records = []
    for idx, case in enumerate(cases, 1):
        calls0, none0, lost0 = transport.calls, transport.no_answer, transport.transport_errors
        t0 = time.monotonic()
        try:
            got = ask(clients[case["language"]], case)
            error = ""
        except Exception as exc:  # a malformed case: reported, never a silent pass
            got, error = None, f"{type(exc).__name__}: {exc}"
        latency = time.monotonic() - t0
        if error:
            scored = {"correct": False, "critical": None, "flags": ["case_error"]}
        else:
            scored = score(case, got)
        record = {
            "id": case["id"], "language": case["language"],
            "category": case.get("category", ""), "trap": bool(case.get("trap")),
            "got": got, "correct": bool(scored["correct"]),
            "critical": scored.get("critical"), "flags": list(scored.get("flags", [])),
            "latency_s": round(latency, 3),
            "calls": transport.calls - calls0, "no_answer": transport.no_answer - none0,
            "transport_errors": transport.transport_errors - lost0,
        }
        record.update({k: v for k, v in scored.items()
                       if k not in ("correct", "critical", "flags")})
        if error:
            record["error"] = error
        records.append(record)
        mark = "ok" if record["correct"] else "x "
        crit = f"  CRITICAL {record['critical']}" if record["critical"] else ""
        if verbose or record["critical"] or error:
            print(f"  [{idx}/{len(cases)}] {mark} {case['id']} ({latency:.2f}s){crit}"
                  f"{'  ' + error if error else ''}")
            if verbose:
                print(f"         {case.get('description', '')}")
                print(f"         got: {json.dumps(got, ensure_ascii=False)[:300]}")

    global_block = summarize(records)
    if extra:
        global_block.update(extra(records))
    by_language = {lang: summarize([r for r in records if r["language"] == lang])
                   for lang in languages}
    print(f"  accuracy {global_block['accuracy']}  critical {global_block['critical_errors']}  "
          f"flags {global_block['flags']}  no answer {global_block['no_answer']}"
          f"/{global_block['calls']}")
    return {
        "benchmark": name,
        "question": dataset.get("question", ""),
        "dataset": os.path.basename(dataset_path),
        "provider": llm_cfg.get("provider"),
        "model": llm_cfg.get("model"),
        "languages": list(languages),
        "total_cases": len(cases),
        "global": global_block,
        "by_language": by_language,
        "cases": records,
    }


def main(name: str, run: Callable, default_dataset: str) -> None:
    """The command line of a phase A benchmark run on its own."""
    import argparse

    from .path_setup import load_config
    from .report import save_json

    parser = argparse.ArgumentParser(description=f"Phase A benchmark: {name}")
    parser.add_argument("--config", default=None)
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--languages", default=None, help="Comma-separated: en,es (default both)")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()
    cfg = load_config(args.config)
    if args.languages:
        cfg["languages"] = args.languages
    results = run(cfg, args.dataset or resolve_path(default_dataset), verbose=args.verbose)
    if args.save:
        path = save_json(results, name, resolve_path(cfg.get("results_dir", "results")))
        print(f"\n  Saved to: {path}")
