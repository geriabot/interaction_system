# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The exit criteria of the pilot (doc/PILOT.md, "Exit criteria"), checked on the results of every
pilot file (results/pilot/<name>/): no model is called.

    python3 scripts/pilot_check.py [--root results/pilot] [--only NAME,...] [--json FILE]

Per pilot, each criterion is OK, FAIL (the pilot is not passed: a program or pipeline error to fix
and run again), LOOK (a person reads the dialogues and attributes it: C program, P prompt or method,
S simulator, M model, as in the debugging; only C and P block) or n/a:

  provenance      provenance.json and experiment*.yaml written                       FAIL if not
  dialogues       every planned dialogue saved (missing listed; the simulated user's
                  failures, not saved, are S)                                       FAIL if missing
  system errors   no dialogue ended by the system's own error (status rejected or
                  timeout; outcome error, a skill that raised) where no failure is
                  injected                                                          FAIL
  records         every record has its transcript, frame (data), outcome, model_wait,
                  simulated user's log, and scores (score_runs.score: the mechanisms'
                  columns, mechanisms_ok where the scenario plans them, the strategy
                  checks of its m_ scenario), and latency_summary has its turns     FAIL
  mechanisms      the mechanisms the scenarios plan happened (mechanisms_ok)        LOOK if not
  calls cut       llm_timeouts = llm_skipped = 0 (the pilot's model is a 32B: a cut
                  turn is looked at, not a failure by itself)                       LOOK; n/a with
                                                                                    injected failures
  faults          every planned model failure was injected (faults_hit)             FAIL if not
  judge           the judge labelled every robot utterance without errors (E0b: the
                  split, the sheets, the key, every judge of the ladder on the
                  sheets, the report)                                               FAIL
  stand-ins       every call went to the stand-in (model versions *-pilot-standin);
                  the token use per role, for COSTES.md §2                          FAIL if a call
                                                                                    did not

The tokens are those the stand-in counted (pilot_standin.py): Ollama's for the cloud system cells
(the real prompts), about four characters per token for the synthetic simulated user and judge
(the real prompts too; their answers are short by construction). Exit status 1 when a criterion
FAILs, else 0. Writes <root>/check.json.
"""

import argparse
import collections
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(EVAL, "analysis"))
import run_pilot  # noqa: E402
from experiment_files import load_experiment  # noqa: E402

SYSTEM_ERRORS = ("rejected", "timeout")
REQUIRED = ("transcript", "data", "outcome", "model_wait", "user_log", "status", "simulator")
STRATEGY_COLUMNS = {"out_of_range": "range_rejected", "unanswered": "unanswered_ok",
                    "teach_back": "tb_marked_wrong", "report_contradicts":
                    "contradiction_not_done", "repeat": "repeat_literal"}


def criterion(name: str, verdict: str, detail: str = "") -> dict:
    return {"criterion": name, "verdict": verdict, "detail": detail}


def records(folder: str) -> dict:
    """{(model, condition, scenario, persona, rep): record} of the dialogues saved."""
    out = {}
    for path in glob.glob(os.path.join(folder, "runs", "*", "*", "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            out[("?", path, "", "", 0)] = None
            continue
        out[(rec.get("model"), rec.get("condition"), rec.get("scenario"), rec.get("persona"),
             rec.get("rep"))] = rec
    return out


def standin_calls(folder: str) -> list[dict]:
    try:
        with open(os.path.join(folder, "standin_calls.jsonl"), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
        return []


def token_use(calls: list[dict], dialogues: int) -> list[str]:
    """Per provider and role: calls, tokens in and out, and per dialogue."""
    groups = collections.defaultdict(lambda: [0, 0, 0])
    for c in calls:
        g = groups[(c.get("provider"), c.get("role"), c.get("model"))]
        g[0] += 1
        g[1] += int(c.get("prompt_tokens") or 0)
        g[2] += int(c.get("completion_tokens") or 0)
    lines = []
    for (provider, role, model), (n, tin, tout) in sorted(groups.items()):
        per = f"; per dialogue {tin / dialogues:.0f} in, {tout / dialogues:.0f} out" \
            if dialogues else ""
        lines.append(f"{provider}/{model} ({role}): {n} calls, {tin} tokens in, {tout} out{per}")
    return lines


def check_pilot(name: str, path: str, root: str) -> dict:
    import score_runs
    exp = load_experiment(path)
    folder = run_pilot.results(name, root)
    out = {"name": name, "folder": folder, "criteria": [], "tokens": []}
    add = out["criteria"].append
    faults_expected = any(True for _ in [1]) and "failures" in exp.get("pilot_of", "")
    if not os.path.isdir(folder):
        add(criterion("dialogues", "FAIL", "not run yet"))
        return out
    have = os.path.exists(os.path.join(folder, "provenance.json")) and glob.glob(
        os.path.join(folder, "experiment*.yaml"))
    add(criterion("provenance", "OK" if have else "FAIL",
                  "" if have else "provenance.json or experiment*.yaml missing"))
    plan = [(m, c, s, p, r) for m, c, s, p, r in run_pilot.planned(path)]
    recs = records(folder)
    missing = [k for k in plan if k not in recs]
    sim_failures = 0
    try:
        with open(os.path.join(folder, "simulator_errors.jsonl"), encoding="utf-8") as f:
            sim_failures = sum(1 for line in f if line.strip())
    except OSError:
        pass
    add(criterion("dialogues", "FAIL" if missing else "OK",
                  f"{len(plan) - len(missing)} of {len(plan)} saved"
                  + (f"; missing: {', '.join('/'.join(map(str, k[1:4])) for k in missing[:6])}"
                     + (" ..." if len(missing) > 6 else "") if missing else "")
                  + (f"; {sim_failures} dialogues the simulated user failed (S), run again on "
                     f"resume" if sim_failures else "")))
    rows, errors, incomplete, unreadable = [], [], [], 0
    for key, rec in recs.items():
        if rec is None:
            unreadable += 1
            continue
        lacking = [k for k in REQUIRED if k not in rec]
        if lacking or not rec.get("transcript") or not rec.get("outcome"):
            incomplete.append(f"{key[2]}/{key[1]}: {', '.join(lacking) or 'empty transcript or outcome'}")
        faulted = bool(rec.get("faults"))
        try:
            row = score_runs.add_adherence(score_runs.score(rec), rec)
        except Exception as exc:  # the scorer must read every record
            incomplete.append(f"{key[2]}/{key[1]}: score_runs: {type(exc).__name__}: {exc}")
            continue
        rows.append(row)
        planned_mech = (rec.get("expect") or {}).get("mechanisms")
        if planned_mech and "mechanisms_ok" not in row:
            incomplete.append(f"{key[2]}/{key[1]}: no mechanisms_ok")
        for exp_key, column in STRATEGY_COLUMNS.items():
            if (rec.get("expect") or {}).get(exp_key) is not None and column not in row:
                incomplete.append(f"{key[2]}/{key[1]}: no {column}")
        if not faulted and (rec.get("status") in SYSTEM_ERRORS or rec.get("outcome") == "error"
                            or row.get("skill_failures")):
            errors.append(f"{key[2]}/{key[1]}/{key[3]}: status {rec.get('status')}, outcome "
                          f"{rec.get('outcome')}, skill failures {row.get('skill_failures')}")
    add(criterion("system errors", "FAIL" if errors else "OK", "; ".join(errors[:6])))
    latency = score_runs.latency_summary(rows) if rows else []
    if rows and len(latency) < 2:
        incomplete.append("latency_summary is empty")
    if unreadable:
        incomplete.append(f"{unreadable} unreadable records")
    add(criterion("records", "FAIL" if incomplete else "OK", "; ".join(incomplete[:6])))
    failed_mech = [f"{r['scenario']}/{r['condition']}: {r.get('mechanisms_failed')}"
                   for r in rows if r.get("mechanisms_ok") is False]
    add(criterion("mechanisms", "LOOK" if failed_mech else ("OK" if any(
        "mechanisms_ok" in r for r in rows) else "n/a"), "; ".join(failed_mech[:6])))
    timeouts = sum(r.get("llm_timeouts") or 0 for r in rows)
    skipped = sum(r.get("llm_skipped") or 0 for r in rows)
    if faults_expected:
        add(criterion("calls cut", "n/a", f"llm_timeouts {timeouts}, llm_skipped {skipped} "
                      f"(failures injected)"))
    else:
        add(criterion("calls cut", "LOOK" if timeouts or skipped else "OK",
                      f"llm_timeouts {timeouts}, llm_skipped {skipped}"))
    faulted = [r for r in rows if recs.get((r["model"], r["condition"], r["scenario"],
                                            r["persona"], r["rep"]), {}).get("faults")]
    unhit = [f"{r['scenario']}/{r['condition']}: {r.get('faults_hit')} of "
             f"{r.get('faults_planned')}" for r in faulted
             if (r.get("faults_hit") or 0) < len({f.get("turn") for f in recs[(
                 r["model"], r["condition"], r["scenario"], r["persona"], r["rep"])]["faults"]})]
    add(criterion("faults", ("FAIL" if unhit else "OK") if faulted else "n/a",
                  "; ".join(unhit[:6])))
    add(judge_criterion(name, folder))
    calls = standin_calls(folder)
    versions = [e.get("model_version", "") for rec in recs.values() if rec
                for e in rec.get("user_log") or [] if "model_version" in e]
    real = [v for v in versions if not v.endswith("-pilot-standin")]
    add(criterion("stand-ins", "FAIL" if real else ("OK" if calls else "FAIL"),
                  f"{len(real)} simulated-user calls answered by another server" if real else
                  ("" if calls else "no call logged by the stand-in")))
    out["tokens"] = token_use(calls, len(rows))
    out["latency"] = latency
    return out


def judge_criterion(name: str, folder: str) -> dict:
    import judge_runs
    if name in run_pilot.E0B:
        e0 = os.path.join(folder, "e0")
        need = ["split.json", "key.json", "sheet_a.csv", "sheet_b.csv", "report.json"]
        lacking = [n for n in need if not os.path.exists(os.path.join(e0, n))]
        import run_e0b
        tags = [t for _, _, t in run_e0b.CLOUD_JUDGES + run_e0b.OPENAI_JUDGES]
        unlabelled = []
        if os.path.exists(os.path.join(e0, "key.json")):
            only = judge_runs.key_items(os.path.join(e0, "key.json"))
            runs = judge_runs.load_runs(folder)
            for tag in tags + [run_pilot.LOCAL_JUDGE[2]]:
                done = judge_runs.load_judgments(os.path.join(folder, f"judgments_{tag}.jsonl"))
                left = judge_runs.pending(runs, done, only)
                if left or any("error" in j for j in done.values()):
                    unlabelled.append(tag)
        detail = "; ".join(([f"missing {', '.join(lacking)}"] if lacking else [])
                           + ([f"not every sheet utterance labelled by {', '.join(unlabelled)}"]
                              if unlabelled else []))
        return criterion("judge", "FAIL" if lacking or unlabelled else "OK", detail)
    judge = run_pilot.judge_of(name)
    if not judge:
        return criterion("judge", "n/a", "the experiment has no judge")
    path = os.path.join(folder, f"judgments_{judge[2]}.jsonl")
    runs = judge_runs.load_runs(folder)
    done = judge_runs.load_judgments(path)
    left = judge_runs.pending(runs, done)
    errors = sum(1 for j in done.values() if "error" in j)
    ok = not left and not errors and done
    return criterion("judge", "OK" if ok else "FAIL",
                     f"{judge[0]}/{judge[1]}: {len(done)} labelled, {len(left)} left, "
                     f"{errors} errors")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=run_pilot.ROOT)
    parser.add_argument("--only", default="")
    parser.add_argument("--json", default=None)
    args = parser.parse_args()
    root = os.path.abspath(args.root)
    only = [n for n in args.only.split(",") if n]
    reports = [check_pilot(n, p, root) for n, p in run_pilot.pilot_files().items()
               if not only or n in only]
    failed = False
    for rep in reports:
        print(f"\n== {rep['name']}")
        for c in rep["criteria"]:
            failed |= c["verdict"] == "FAIL"
            print(f"  {c['verdict']:5s} {c['criterion']}" + (f": {c['detail']}" if c["detail"]
                                                              else ""))
        for line in rep.get("tokens") or []:
            print(f"  tokens  {line}")
    combined = os.path.join(root, "e0b_report.json")
    if not only or set(run_pilot.E0B) & set(only):
        print(f"\nE0b, the judge of both languages: "
              + ("results/pilot/e0b_report.json written" if os.path.exists(combined)
                 else "not combined yet (both E0b pilots must have their report)"))
    print("\nPASSED" if not failed else "\nNOT PASSED: fix the FAIL lines (program or pipeline) "
          "and run the affected pilots again; LOOK lines are read and attributed (C, P, S, M)")
    os.makedirs(root, exist_ok=True)
    with open(args.json or os.path.join(root, "check.json"), "w", encoding="utf-8") as f:
        json.dump({"passed": not failed, "pilots": reports}, f, indent=1)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
