# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
E0b, the validation of the judge (doc/JUDGE_VALIDATION.md), in stages, each one resumable:

    python3 run_e0b.py preflight [--allow-cloud]
        The GPU server is free, the API key is set and (with --allow-cloud) one tiny call to the
        simulator's Gemini model answers (the dialogues' only paid calls). No judge is called
        here: the cloud judges are called only by label-cloud, if no free judge passes.
    python3 run_e0b.py dialogues --allow-cloud [--allow-provisional]
        Phase 1: the dialogues in English and Spanish (e0b_judge_dialogues*.yaml; the simulated
        user is Gemini), each language split once into a development half (to adjust the judge's
        instructions) and a validation half. E0b runs with the model E0a decided for each
        language (results/e0a/decision.json, whatever it is, the provisional qwen3:8b included):
        refused while E0a has not decided, or while a file names another local model than the
        decision (run_e0a.py apply writes it; --allow-provisional for a trial before E0a). The
        dialogues are NOT scored: E0b must not show E1-like results before E1.
    python3 run_e0b.py label --development-only
        While the instructions are tuned: every local judge labels the development half only.
    python3 run_e0b.py label
        Phases 2-3, once the judge's instructions are frozen, FREE: every local judge of the
        ladder labels every dialogue, one judge on the server at a time (pulled if missing,
        unloaded and removed after its two languages; a judge with nothing left to label is not
        pulled), then the annotation sheets of each language are drawn from the validation half
        (no judge chooses them; the local judges' labels of the development half only size the
        sample). --keep-models keeps the
        judges pulled here.
    python3 run_e0b.py report --a-en A.csv --b-en B.csv --a-es A.csv --b-es B.csv
        Phase 4, once two people have filled the sheets: the report of each language
        (results/e0b/<lang>/e0/report.json), then ONE judge for both languages (the ladder across
        languages: results/e0b/report.json). If a local judge passes, it is the judge and E0b
        ends: no cloud call at all. If none does and the cloud judges have not labelled yet, the
        report says so (`next`: label-cloud).
    python3 run_e0b.py label-cloud --allow-cloud [--openai]
        Only when no local judge passed (the report): one tiny call to each cloud judge of the
        ladder, then each labels only the utterances of the sheets (cents); then run report again
        with the same sheets (the people annotate once).

The ladder is fixed here, before any result: the local judges from smaller to larger, then
Gemini, then OpenAI (only with --openai). A judge never labels a system of its own family: a judge
of the family of E0b's system model is skipped (local or cloud: it would be paid for nothing), and
the run stops when no judge of another family is left. Results in results/e0b/<en|es>/. The stages
that use the GPU hold the lock of gpu_lock.py (E0a and E0b never run at once).
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.normpath(os.path.join(HERE, ".."))
sys.path.append(os.path.join(EVAL, "offline", "core"))
sys.path.append(os.path.join(EVAL, "analysis"))
sys.path.append(os.path.join(EVAL, "simulation", "interaction_sim"))
import gpu_lock  # noqa: E402
import ollama_models  # noqa: E402
import provenance  # noqa: E402

RESULTS = os.path.join(EVAL, "results", "e0b")
PULLED = os.path.join(RESULTS, "pulled.json")
DECISION_E0A = os.path.join(EVAL, "results", "e0a", "decision.json")
HARDWARE = os.path.join(EVAL, "experiments", "hardware", "single_gpu.yaml")
OLLAMA = "http://localhost:11434"
LANGUAGES = {"en": "e0b_judge_dialogues.yaml", "es": "e0b_judge_dialogues_es.yaml"}
SIMULATOR = ("gemini", "gemini-2.5-flash-lite")
PROVISIONAL = "qwen3:8b"

# (provider, model, tag), in the order of the ladder, fixed before any result (doc/JUDGE_VALIDATION.md;
# the literature behind each rung: E0B_JUDGES review). The tag names the labels file and must start
# with the family's name (judge_validation.family). Local judges first (free): the first that passes
# is used. Otherwise every cloud judge labels the same annotated utterances and the cheapest that
# passes is used: they are listed from cheaper to dearer (Flash-Lite added by the author for its
# price; the literature has no evidence for the Lite tier). Options are added, never replaced.
LOCAL_JUDGES = [("ollama", "mistral-small3.2:24b", "mistral_small"),
                ("ollama", "llama3.1:8b", "llama3_1_8b"),
                ("ollama", "gemma3:27b", "gemma3_27b"),
                ("ollama", "phi4:14b", "phi4_14b"),
                ("ollama", "qwen3:32b", "qwen3_32b")]  # only when the system model is not a Qwen
CLOUD_JUDGES = [("gemini", "gemini-2.5-flash-lite", "gemini_2_5_flash_lite"),
                ("gemini", "gemini-2.5-flash", "gemini_2_5_flash"),
                # Gemini 2.5 Pro is no longer served to this key (HTTP 404, 2026-10-29): its
                # successor, a preview (it may change; every call logs the version that answered)
                ("gemini", "gemini-3.1-pro-preview", "gemini_3_1_pro_preview")]
OPENAI_JUDGES = [("openai", "gpt-4o-mini", "gpt_4o_mini"),
                 ("openai", "gpt-4o", "gpt_4o")]
# Models that were on the server before this work: never removed (one list: ollama_models)
KEEP = ollama_models.KEEP


def run(cmd: list[str]) -> None:
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=EVAL, check=True)


def py(script: str, *args: str) -> list[str]:
    return [sys.executable, os.path.join(EVAL, script), *args]


def folder(lang: str) -> str:
    return os.path.join(RESULTS, lang)


def labels_path(lang: str, tag: str) -> str:
    return os.path.join(folder(lang), f"judgments_{tag}.jsonl")


def system_models() -> set:
    """The local system models of E0b's dialogues."""
    import yaml
    out = set()
    for experiment in LANGUAGES.values():
        with open(os.path.join(EVAL, "experiments", experiment), encoding="utf-8") as f:
            out |= {m for m in yaml.safe_load(f).get("models", []) if "/" not in m}
    return out


def system_families() -> set:
    """The families of the local system models of E0b's dialogues (a judge never labels its own
    family: a judge of that family would label nothing)."""
    from judge_validation import family
    return {family(m) for m in system_models()}


def ladder(args) -> tuple[list, list]:
    """(local judges, cloud judges) of the ladder that may label E0b's system: those of its family
    are left out (a Gemma system blinds every Gemini judge: they are not paid for)."""
    from judge_validation import family
    skip = system_families()
    local, cloud = [], []
    for judges, kept in ((LOCAL_JUDGES, local),
                         (CLOUD_JUDGES + (OPENAI_JUDGES if getattr(args, "openai", False)
                                          else []), cloud)):
        for judge in judges:
            if family(judge[2]) in skip:
                print(f"   {judge[1]}: of the family of the system model ({family(judge[2])}), "
                      f"skipped", flush=True)
            else:
                kept.append(judge)
    if not local and not (cloud and getattr(args, "allow_cloud", False)):
        sys.exit("No judge of another family than the system model's is left: the ladder cannot "
                 "validate any judge (add one of another family, or --allow-cloud)")
    if not local:
        print("WARNING: no local judge of another family is left: only cloud judges", flush=True)
    return local, cloud


def ping(provider: str, model: str) -> None:
    """One tiny paid call; stops the run if the model does not answer JSON."""
    from interaction_sim.cloud_llm import ask_json
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    try:
        raw, meta = ask_json(provider, model, 'Answer {"ok": true}.', schema, max_tokens=40)
    except Exception as exc:
        sys.exit(f"{provider}/{model} does not answer: {type(exc).__name__}: {exc}")
    if '"ok"' not in raw:
        sys.exit(f"{provider}/{model} did not answer JSON: {raw!r}")
    print(f"{provider}/{model} answers (version {meta.get('model_version', '?')})", flush=True)


def preflight(args) -> None:
    run(py("scripts/run_parallel.py", os.path.join("experiments", LANGUAGES["en"]), HARDWARE,
           "--check-servers"))
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        sys.exit("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set")
    if args.allow_cloud:
        ping(*SIMULATOR)    # the judges are pinged by label-cloud, only if it is needed
    print("Preflight OK")


def e0a_decision() -> dict:
    """{language: the model E0a decided} from results/e0a/decision.json (run_e0a.py decide),
    a language with no model chosen left out; {} when E0a has not decided."""
    try:
        with open(DECISION_E0A, encoding="utf-8") as f:
            languages = json.load(f).get("languages") or {}
    except (OSError, ValueError):
        return {}
    return {lang: d["chosen"] for lang, d in languages.items() if (d or {}).get("chosen")}


def file_models(experiment: str) -> list[str]:
    """The local system models an E0b experiment file names."""
    import yaml
    with open(os.path.join(EVAL, "experiments", experiment), encoding="utf-8") as f:
        return [m for m in yaml.safe_load(f).get("models") or [] if "/" not in m]


def decision_problems() -> list[str]:
    """Why E0b cannot run with E0a's decision ([] when it can): E0a has not decided, a language
    has no model chosen, or a file names another local model than the decision (apply not run,
    or the file edited by hand). The files, the provenance and what runs must agree."""
    decided = e0a_decision()
    if not os.path.exists(DECISION_E0A):
        return [f"E0a has not decided ({DECISION_E0A} is missing): run_e0a.py decide, then apply"]
    problems = []
    for lang, experiment in LANGUAGES.items():
        chosen = decided.get(lang)
        if not chosen:
            problems.append(f"E0a chose no model for {lang} (decision.json): E0b cannot run in "
                            f"that language")
            continue
        named = file_models(experiment)
        if named != [chosen]:
            problems.append(f"experiments/{experiment} names {named}, but E0a decided {chosen} "
                            f"for {lang}: run run_e0a.py apply (or restore the file)")
    return problems


def dialogues(args) -> None:
    if not args.allow_cloud:
        sys.exit("The simulated user is Gemini: --allow-cloud")
    decided = {}
    if not args.allow_provisional:
        problems = decision_problems()
        if problems:
            sys.exit("E0b runs with the model E0a decided for each language: "
                     + "; ".join(problems) + " (--allow-provisional for a trial before E0a)")
        decided = e0a_decision()
    with gpu_lock.held("run_e0b.py dialogues"):
        for lang, experiment in LANGUAGES.items():
            out = folder(lang)
            # The model E0a decided, taken from its decision (the file names the same one)
            models = ["--models", decided[lang]] if lang in decided else []
            try:
                run(py("scripts/run_parallel.py", os.path.join("experiments", experiment),
                       HARDWARE, "--out", out, "--allow-cloud", *models))
            finally:
                ollama_models.unload_all(OLLAMA, system_models())
            # Not scored: E0b must not show E1-like results before E1's pilot
            if not os.path.exists(os.path.join(out, "e0", "split.json")):
                run(py("analysis/judge_validation.py", "split", out))
            provenance.catalog("dialogues", out, {"experiment": experiment,
                                                  "models": sorted(system_models())})


def server_models() -> set:
    return ollama_models.installed(OLLAMA)


def pending(tag: str, half: str | None = None) -> int:
    """The utterances a judge still has to label, in both languages (no call made)."""
    import judge_runs
    total = 0
    for lang in LANGUAGES:
        if not os.path.isdir(folder(lang)):
            continue
        runs = judge_runs.load_runs(folder(lang))
        only = judge_runs.split_runs(folder(lang), half) if half else None
        total += len(judge_runs.pending(runs, judge_runs.load_judgments(labels_path(lang, tag)),
                                        runs_only=only))
    return total


def drop(model: str, pulled: list[str], keep: bool) -> None:
    """Unload a judge; remove it if this stage pulled it (unless kept), and forget it."""
    ollama_models.unload(OLLAMA, model)
    if model not in pulled or keep or ollama_models.protected(model):
        return
    if model in server_models():
        ollama_models.remove(OLLAMA, model)
    pulled.remove(model)
    ollama_models.save_pulled(PULLED, pulled)


def label_local(args, local: list) -> list[str]:
    """Every local judge on every dialogue (or the development half), one at a time. The judges
    that labelled anything."""
    half = "development" if args.development_only else None
    pulled = ollama_models.load_pulled(PULLED)
    done = []
    for provider, model, tag in local:
        todo = pending(tag, half)
        if not todo:
            print(f"   {model}: nothing left to label", flush=True)
            if model in pulled:
                drop(model, pulled, args.keep_models)
            continue
        if model not in server_models():
            if model not in pulled:  # recorded before the pull: a crash is cleaned on resume
                pulled.append(model)
                ollama_models.save_pulled(PULLED, pulled)
            ollama_models.pull(OLLAMA, model)
        try:
            for lang in LANGUAGES:
                provenance.add_models(folder(lang), [model], OLLAMA)  # the judge's digest
                cmd = py("analysis/judge_runs.py", folder(lang), "--provider", provider,
                         "--model", model, "--tag", tag, "--host", OLLAMA)
                run(cmd + (["--only-split", half] if half else []))
        finally:
            ollama_models.unload(OLLAMA, model)
        drop(model, pulled, args.keep_models)  # right after its two languages: disk is limited
        done.append(tag)
    for model in list(pulled):  # pulled before a crash, no longer needed
        drop(model, pulled, args.keep_models)
    return done


def label(args) -> None:
    """The local judges (free), then the sheets: no cloud call (label-cloud, only if needed)."""
    local, _ = ladder(args)
    with gpu_lock.held("run_e0b.py label"):
        done = label_local(args, local)
    if args.development_only:
        provenance.catalog("label", RESULTS, {"judges": done, "half": "development"})
        print("Development half labelled: tune the instructions, then label without "
              "--development-only")
        return
    sheets = []
    for lang in LANGUAGES:
        if not os.path.exists(os.path.join(folder(lang), "e0", "key.json")):
            run(py("analysis/judge_validation.py", "sheets", folder(lang), "--n", str(args.n)))
            sheets.append(lang)
    provenance.catalog("label", RESULTS, {"judges": done, "sheets_made": sheets})
    print("The sheets are ready for the two annotators; then report. The cloud judges are called "
          "only if no local judge passes (label-cloud)")


def local_tags() -> set:
    return {tag for _, _, tag in LOCAL_JUDGES}


def report_state() -> dict:
    """What the combined report (results/e0b/report.json) says: {} before any report; else the
    chosen judge, whether it is local, and whether the cloud judges must still label."""
    try:
        with open(os.path.join(RESULTS, "report.json"), encoding="utf-8") as f:
            rep = json.load(f)
    except (OSError, ValueError):
        return {}
    chosen = rep.get("chosen")
    return {"chosen": chosen, "local": chosen in local_tags(),
            "cloud_labelled": bool(rep.get("cloud_labelled")),
            "next": "" if chosen or rep.get("cloud_labelled") else "label-cloud"}


def label_cloud(args) -> None:
    """Only when no local judge passed: the cloud judges of the ladder on the sheets' utterances
    (each after one tiny call), then report again."""
    if not args.allow_cloud:
        sys.exit("The cloud judges are paid: --allow-cloud")
    state = report_state()
    if not state:
        sys.exit("No report yet: the annotators fill the sheets, then report (a local judge may "
                 "pass: then no cloud call is made)")
    if state["chosen"]:
        sys.exit(f"The judge is chosen ({state['chosen']}): no cloud judge is needed")
    _, cloud = ladder(args)
    for lang in LANGUAGES:
        if not os.path.exists(os.path.join(folder(lang), "e0", "key.json")):
            sys.exit(f"No sheets in {folder(lang)}/e0: run label first")
    for provider, model, _ in cloud:  # a judge that cannot answer is not paid for
        ping(provider, model)
    done = []
    for provider, model, tag in cloud:
        for lang in LANGUAGES:
            key = os.path.join(folder(lang), "e0", "key.json")
            run(py("analysis/judge_runs.py", folder(lang), "--provider", provider,
                   "--model", model, "--tag", tag, "--only-key", key, "--allow-cloud"))
        done.append(tag)
    path = os.path.join(RESULTS, "report.json")
    with open(path, encoding="utf-8") as f:
        rep = json.load(f)
    rep["cloud_labelled"] = done
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1)
    provenance.catalog("label-cloud", RESULTS, {"judges": done})
    print("The cloud judges labelled the sheets' utterances: run report again (same sheets)")


def report(args) -> None:
    order = ",".join(tag for _, _, tag in LOCAL_JUDGES + CLOUD_JUDGES + OPENAI_JUDGES)
    reports = []
    for lang in LANGUAGES:
        a, b = getattr(args, f"a_{lang}"), getattr(args, f"b_{lang}")
        if a and b:
            run(py("analysis/judge_validation.py", "report", folder(lang), "--a", a, "--b", b,
                   "--order", order))
            reports.append(os.path.join(folder(lang), "e0", "report.json"))
    if len(reports) < len(LANGUAGES):
        sys.exit("One judge for both languages: give the filled sheets of every language")
    out = os.path.join(RESULTS, "report.json")
    cloud_before = report_state().get("cloud_labelled") or any(
        os.path.exists(labels_path(lang, tag)) for lang in LANGUAGES
        for _, _, tag in CLOUD_JUDGES + OPENAI_JUDGES)
    run(py("analysis/judge_validation.py", "combine", *reports, "--order", order, "--out", out))
    with open(out, encoding="utf-8") as f:
        chosen = json.load(f)
    # Whether the cloud judges were in this report: if no judge was chosen without them, the next
    # stage is label-cloud (paid); a local judge chosen ends E0b with no cloud call
    chosen["cloud_labelled"] = bool(cloud_before)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(chosen, f, indent=1)
    if not chosen.get("chosen"):
        print("No judge passes" + ("" if cloud_before else " among the local judges: run "
                                   "label-cloud --allow-cloud, then report again with the same "
                                   "sheets"))
    provenance.catalog("report", RESULTS, {"chosen": chosen.get("chosen"),
                                           "tentative": chosen.get("tentative"),
                                           "report_json": out})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="stage", required=True)
    p = sub.add_parser("preflight")
    p.add_argument("--allow-cloud", action="store_true",
                   help="One tiny paid call to the simulated user's model")
    p = sub.add_parser("dialogues")
    p.add_argument("--allow-cloud", action="store_true")
    p.add_argument("--allow-provisional", action="store_true",
                   help="Run with the provisional model, before E0a's decision (a trial)")
    p = sub.add_parser("label")
    p.add_argument("--keep-models", action="store_true")
    p.add_argument("--development-only", action="store_true",
                   help="Only the local judges, only the development half (to tune the "
                        "instructions)")
    p.add_argument("--n", type=int, default=150, help="Utterances per sheet (per language)")
    p = sub.add_parser("label-cloud")
    p.add_argument("--allow-cloud", action="store_true", help="The cloud judges are paid")
    p.add_argument("--openai", action="store_true", help="Also the OpenAI judges")
    p = sub.add_parser("report")
    for lang in LANGUAGES:
        p.add_argument(f"--a-{lang}", default=None, help=f"Annotator A's sheet ({lang}), filled in")
        p.add_argument(f"--b-{lang}", default=None, help=f"Annotator B's sheet ({lang}), filled in")
    args = parser.parse_args()
    {"preflight": preflight, "dialogues": dialogues, "label": label, "label-cloud": label_cloud,
     "report": report}[args.stage](args)


if __name__ == "__main__":
    main()
