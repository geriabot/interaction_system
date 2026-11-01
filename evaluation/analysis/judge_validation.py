# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
E0b, the validation of the judge (doc/JUDGE_VALIDATION.md): before the judge's labels are used in any
experiment, they are compared with those of two people on a sample of utterances, measure by
measure, and a rule decides whether the judge is used.

    python3 judge_validation.py split <results> [--dev 0.5]
        Optional: splits the dialogues of one folder into a development set (to adjust the judge's
        instructions) and a validation set (never looked at until the instructions are frozen).
        E0b splits its own dialogues this way (e0b_judge_dialogues*.yaml). Not needed when the
        instructions were adjusted on other dialogues.
    python3 judge_validation.py sheets <pilot results> [--n 150] [--language Spanish] [--force]
        After the judge has labelled the validation dialogues (judge_runs.py): a sample of their
        robot utterances, written as one annotation sheet per annotator (they do not see the
        judge's labels, the condition nor each other's sheet). The sample is stratified by
        persona x condition (judge-independent strata, equal allocation); the rare labels (out of
        the frame, unsupported, for any judge) are oversampled up to half of it. The inclusion
        probability of every utterance is stored in key.json (prevalence-weighted kappa). An
        existing key.json is never overwritten (the annotations made on it would be lost) unless
        --force.
    python3 judge_validation.py report <pilot results> --a sheet_a.csv --b sheet_b.csv
        Per judge, label and annotator: the prevalence of the label in the sample, the judge's
        sensitivity and specificity (the annotator as reference), Cohen's unweighted kappa (the
        decision statistic, pre-registered) with a 95% interval from a cluster bootstrap over
        DIALOGUES (the utterances of a dialogue are not independent), and the kappa weighted by
        the inverse inclusion probabilities (reported: the oversampling of rare labels undone).
        The decision of each judge and label:
          kappa >= 0.8 with each annotator      use the judge
          0.67 <= kappa < 0.8 with each one     use it; conclusions on that measure are tentative
          kappa < 0.67 with either              do not use it as is: the fallback plan (below)
        If the annotators do not agree with each other (kappa A-B < 0.67), the measure itself is
        not defined clearly enough: no judge is validated on it, the annotation guide is revised.
        A label with fewer than MIN_POSITIVES human positives (an utterance marked out of the
        frame, or unsupported, by EITHER annotator: the looser count, so that a label both
        annotators rarely agree on is not declared rare) is "not validated": never used. A kappa
        that is not defined (one label only) is "not decidable": never used. The report stops if
        the judge's instructions changed after the sheets were made.
    python3 judge_validation.py combine <report.json of each language>... --order T1,T2 --out F
        One judge for every language: the first of the ladder that passes on both labels, both
        annotators and EVERY language (else the best tentative one, else none); writes F
        (results/e0b/report.json).

The measures: in_frame (the utterance stays in the frame of the task) and unsupported (it states
at least one fact that neither the task nor its material supports), the judge's (judge_runs.py).
Everything is written in <pilot results>/e0/, with the hash of the judge's instructions and the
model version, so that the instructions validated are the ones used later.
"""

import argparse
import csv
import glob
import hashlib
import json
import math
import os
import random
import sys
from collections import defaultdict

import judge_runs

MEASURES = ("in_frame", "unsupported")
USE, TENTATIVE = 0.8, 0.67
# The label each measure is about (its rare, positive value): out of the frame, unsupported
POSITIVE = {"in_frame": 0, "unsupported": 1}
# Fewer human positives than this (by either annotator) in a label and language: not validated
MIN_POSITIVES = 30
BOOTSTRAP = 2000
NOT_DECIDABLE = "NOT DECIDABLE: a kappa is not defined (one label only)"
NOT_VALIDATED = f"NOT VALIDATED: fewer than {MIN_POSITIVES} human positives"
REVISE = "REVISE THE GUIDE: the annotators do not agree with each other"


def e0_dir(results: str) -> str:
    path = os.path.join(results, "e0")
    os.makedirs(path, exist_ok=True)
    return path


def prompt_hash() -> str:
    return hashlib.sha256(judge_runs.PROMPT.encode("utf-8")).hexdigest()[:16]


def split(args) -> None:
    """Dialogues, not utterances: the utterances of one dialogue are not independent."""
    runs = [run for run, _ in judge_runs.load_runs(args.results)]
    rng = random.Random(args.seed)
    rng.shuffle(runs)
    cut = int(round(len(runs) * args.dev))
    sets = {"development": sorted(runs[:cut]), "validation": sorted(runs[cut:]),
            "seed": args.seed}
    path = os.path.join(e0_dir(args.results), "split.json")
    if os.path.exists(path) and not args.force:
        sys.exit(f"{path} exists: the split is made once (--force to redo it before any annotation)")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sets, f, indent=1)
    print(f"{len(sets['development'])} dialogues to adjust the judge, {len(sets['validation'])} "
          f"kept for the validation -> {path}")


def judge_files(results: str) -> dict:
    """{judge name: its labels file}: judgments_<tag>.jsonl of every candidate judge (or the
    single judgments.jsonl, named "judge")."""
    found = {os.path.basename(p)[len("judgments_"):-len(".jsonl")]: p
             for p in glob.glob(os.path.join(results, "judgments_*.jsonl"))}
    plain = os.path.join(results, "judgments.jsonl")
    if not found and os.path.exists(plain):
        found = {"judge": plain}
    return found


FAMILIES = {"openai": "openai", "gpt": "openai", "gemini": "google", "google": "google",
            "gemma": "google", "qwen": "qwen", "mistral": "mistral", "llama": "meta",
            "anthropic": "anthropic", "claude": "anthropic", "deepseek": "deepseek", "phi": "microsoft"}


def family(name: str) -> str:
    """The family of a judge (its tag, e.g. mistral_small, gemma3_27b, gemini, openai) or of a
    system model (qwen2.5:14b, openai/<model>, gemini/<model>): a judge never labels the
    utterances of a system of its own family."""
    name = name.lower().split("/")[-1] if "/" in name and name.split("/")[0] in ("ollama",) else name.lower()
    for prefix in sorted(FAMILIES, key=len, reverse=True):
        if name.startswith(prefix) or name.split("/")[0] == prefix:
            return FAMILIES[prefix]
    return "unknown"


def is_local(model: str) -> bool:
    """A system model served locally (Ollama: no provider/ prefix)."""
    return "/" not in (model or "")


def judge_labels(j: dict) -> dict:
    return {"in_frame": int(bool(j["in_frame"])), "unsupported": int(bool(j["unsupported_claims"]))}


def _allocate(total: int, sizes: dict) -> dict:
    """An equal allocation of `total` draws over the cells (water-filling: a cell smaller than
    its share gives the rest to the others), deterministic (cells in sorted order)."""
    alloc = dict.fromkeys(sizes, 0)
    remaining = min(total, sum(sizes.values()))
    open_ = sorted(k for k, n in sizes.items() if n > 0)
    while remaining > 0 and open_:
        share = max(1, remaining // len(open_))
        for k in open_:
            give = min(share, sizes[k] - alloc[k], remaining)
            alloc[k] += give
            remaining -= give
            if not remaining:
                break
        open_ = [k for k in open_ if alloc[k] < sizes[k]]
    return alloc


def stratified_sample(items: list, n: int, rng: random.Random) -> list:
    """(item, stratum, pool, inclusion probability) for the sample: up to n // 2 from the rare
    pool (any judge gave a rare label), the rest from the common pool, each pool split in
    persona x condition strata with equal allocation. Every item of a (pool, stratum) cell has the
    same inclusion probability: drawn / size of the cell."""
    def stratum(x):
        return f"{x[1].get('persona', '?')}|{x[1].get('condition', '?')}"

    def rare(x):
        return any(lab["in_frame"] == 0 or lab["unsupported"] == 1 for lab in x[5].values())

    cells: dict[tuple, list] = defaultdict(list)
    for x in items:
        cells[("rare" if rare(x) else "common", stratum(x))].append(x)
    n_rare = min(n // 2, sum(len(v) for k, v in cells.items() if k[0] == "rare"))
    alloc = _allocate(n_rare, {k: len(v) for k, v in cells.items() if k[0] == "rare"})
    n_common = n - sum(alloc.values())
    alloc.update(_allocate(n_common, {k: len(v) for k, v in cells.items() if k[0] == "common"}))
    left = n - sum(alloc.values())
    if left > 0:  # the common pool is short: more rare ones
        alloc.update(_allocate(left + sum(v for k, v in alloc.items() if k[0] == "rare"),
                               {k: len(v) for k, v in cells.items() if k[0] == "rare"}))
    out = []
    for key in sorted(cells):
        group = cells[key]
        for x in rng.sample(group, alloc.get(key, 0)):
            out.append((x, key[1], key[0], alloc[key] / len(group)))
    return out


def sheets(args) -> None:
    folder = e0_dir(args.results)
    key_path = os.path.join(folder, "key.json")
    if os.path.exists(key_path) and not args.force:
        sys.exit(f"{key_path} exists: the sheets are made once (the annotations made on them would "
                 f"be lost). --force to make them again, only before anyone annotates")
    if os.path.exists(key_path):
        print(f"WARNING: overwriting {key_path} and the sheets (--force): any annotation made on "
              f"the old sheets no longer matches them")
    # With a split (split.json), only its validation dialogues; without it, every dialogue of the
    # folder (when the instructions were adjusted on other dialogues)
    split_path = os.path.join(folder, "split.json")
    validation = None
    if os.path.exists(split_path):
        with open(split_path, encoding="utf-8") as f:
            validation = set(json.load(f)["validation"])
    judges = judge_files(args.results)
    if not judges:
        sys.exit("No judge's labels: run judge_runs.py on the dialogues first (one --tag per "
                 "candidate judge, e.g. gemini and openai)")
    labels = {name: judge_runs.load_judgments(path) for name, path in judges.items()}
    items = []
    for run, rec in judge_runs.load_runs(args.results):
        if validation is not None and run not in validation:
            continue
        if args.language and rec.get("language", args.language) != args.language:
            continue
        for i, text, history in judge_runs.utterances(rec):
            js = {name: lab.get((run, i)) for name, lab in labels.items()}
            if all(j and "error" not in j for j in js.values()):
                items.append((run, rec, i, text, history,
                              {name: judge_labels(j) for name, j in js.items()}))
    if not items:
        sys.exit("No judged utterance in the validation dialogues: run judge_runs.py on the "
                 "pilot first (with the frozen instructions)")
    rng = random.Random(args.seed)
    chosen = stratified_sample(items, args.n, rng)
    rng.shuffle(chosen)
    key = {"prompt_hash": prompt_hash(), "language": args.language, "n": len(chosen),
           "judges": sorted(judges), "seed": args.seed,
           "sampling": "persona x condition strata, equal allocation; rare labels (any judge) "
                       "oversampled up to half; inclusion probability per item",
           "rare_oversampled": sum(1 for c in chosen if c[2] == "rare"), "items": {}}
    for name in ("a", "b"):
        with open(os.path.join(folder, f"sheet_{name}.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["id", "task", "material", "conversation_before", "robot_utterance",
                        "in_frame (1/0)", "unsupported (1/0)", "note"])
            for n, ((run, rec, i, text, history, _), _, _, _) in enumerate(chosen, 1):
                goal = rec.get("goal", {})
                w.writerow([n, goal.get("task_description", ""), judge_runs.material(goal),
                            history, text, "", "", ""])
    for n, ((run, rec, i, text, history, labs), stratum, pool, prob) in enumerate(chosen, 1):
        key["items"][str(n)] = {"run": run, "index": i, "judges": labs,
                                "condition": rec.get("condition"), "model": rec.get("model"),
                                "persona": rec.get("persona"), "stratum": stratum,
                                "pool": pool, "inclusion_probability": round(prob, 6)}
    with open(key_path, "w", encoding="utf-8") as f:
        json.dump(key, f, indent=1)
    print(f"{len(chosen)} utterances ({key['rare_oversampled']} with a rare judge label) -> "
          f"{folder}/sheet_a.csv and sheet_b.csv, one per annotator; the key (judge's labels) "
          f"in key.json, not for the annotators")


def read_sheet(path: str) -> dict:
    out = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                out[row["id"]] = {"in_frame": int(row["in_frame (1/0)"]),
                                  "unsupported": int(row["unsupported (1/0)"])}
            except (ValueError, KeyError):
                continue
    return out


def weighted_kappa(a: list[int], b: list[int], w: list[float]) -> float:
    """Cohen's kappa with each item weighted (by the inverse of its inclusion probability: the
    agreement on the population the sample was drawn from)."""
    total = sum(w)
    if not total:
        return float("nan")
    po = sum(wi for x, y, wi in zip(a, b, w) if x == y) / total
    pa = sum(wi * x for x, wi in zip(a, w)) / total
    pb = sum(wi * y for y, wi in zip(b, w)) / total
    pe = pa * pb + (1 - pa) * (1 - pb)
    return float("nan") if pe == 1 else (po - pe) / (1 - pe)


def interval(pairs: list, seed: int, clusters: list | None = None, n: int = BOOTSTRAP) -> tuple:
    """The 95% bootstrap interval of kappa. clusters: the dialogue of each pair: whole dialogues
    are resampled (their utterances are not independent); without them, the pairs."""
    rng = random.Random(seed)
    groups: dict = defaultdict(list)
    for k, pair in enumerate(pairs):
        groups[clusters[k] if clusters else k].append(pair)
    keys = sorted(groups, key=str)
    values = []
    for _ in range(n):
        s = [p for _ in keys for p in groups[keys[rng.randrange(len(keys))]]]
        values.append(judge_runs.kappa([x for x, _ in s], [y for _, y in s]))
    values = sorted(v for v in values if v == v)
    return ((values[int(0.025 * len(values))], values[int(0.975 * len(values)) - 1]) if values
            else (float("nan"),) * 2)


def _nan(x) -> bool:
    return isinstance(x, float) and math.isnan(x)


def decide(k_a: float, k_b: float, k_ab: float, positives: int | None = None) -> str:
    """The decision on one judge and label. A kappa that is not defined (nan) is never USE."""
    if _nan(k_ab):
        return NOT_DECIDABLE
    if k_ab < TENTATIVE:
        return REVISE
    if positives is not None and positives < MIN_POSITIVES:
        return NOT_VALIDATED
    if _nan(k_a) or _nan(k_b):
        return NOT_DECIDABLE
    low = min(k_a, k_b)
    if low >= USE:
        return "USE the judge"
    if low >= TENTATIVE:
        return "USE, tentative conclusions"
    return "DO NOT USE AS IS: fallback plan"


def worst_kappa(res: dict) -> float:
    """The lowest kappa of a judge over its labels and annotators; nan when a label is missing or
    any of them is not decidable, not validated or the annotators disagree (never chosen)."""
    if set(res) != set(MEASURES):
        return float("nan")
    values = []
    for r in res.values():
        if r["decision"] in (NOT_DECIDABLE, NOT_VALIDATED, REVISE):
            return float("nan")
        values += [r["A"]["kappa"], r["B"]["kappa"]]
    return float("nan") if any(_nan(v) for v in values) else min(values)


def agreement_figures(judge_: list[int], human: list[int], measure: str) -> dict:
    """Sensitivity and specificity of the judge on the measure's positive label, the human as
    reference."""
    pos = POSITIVE[measure]
    tp = sum(1 for j, h in zip(judge_, human) if h == pos and j == pos)
    fn = sum(1 for j, h in zip(judge_, human) if h == pos and j != pos)
    tn = sum(1 for j, h in zip(judge_, human) if h != pos and j != pos)
    fp = sum(1 for j, h in zip(judge_, human) if h != pos and j == pos)
    return {"sensitivity": round(tp / (tp + fn), 3) if tp + fn else None,
            "specificity": round(tn / (tn + fp), 3) if tn + fp else None}


def _r(x):
    return x if x is None or _nan(x) else round(x, 3)


def choose(worst: dict, order: list[str]) -> tuple:
    """(order, chosen, tentative): the ladder (fixed before the results): the first judge, in
    order, whose worst kappa is >= 0.8; if none, the best with >= 0.67 (tentative); else none.
    A judge with a worst kappa that is not defined is never chosen."""
    order = [j for j in order if j in worst] + sorted(j for j in worst if j not in order)
    chosen = next((j for j in order if not _nan(worst[j]) and worst[j] >= USE), None)
    tentative = chosen is None and any(not _nan(worst[j]) and worst[j] >= TENTATIVE
                                       for j in order)
    if tentative:
        chosen = max((j for j in order if not _nan(worst[j]) and worst[j] >= TENTATIVE),
                     key=worst.get)
    return order, chosen, tentative


def report(args) -> None:
    """Per candidate judge and label: kappa with each annotator (leaving out the utterances of a
    system of the judge's own family: a judge does not judge its own family) and between the
    annotators, with the sample's prevalence, the judge's sensitivity and specificity and the
    weighted kappa; the decision of each judge in this language (the one judge of every language
    is chosen by `combine`)."""
    folder = e0_dir(args.results)
    with open(os.path.join(folder, "key.json"), encoding="utf-8") as f:
        key = json.load(f)
    if key["prompt_hash"] != prompt_hash():
        sys.exit("The judge's instructions changed after the sheets were made (key.json "
                 f"{key['prompt_hash']}, now {prompt_hash()}): this sample does not validate the "
                 "current instructions. Stopping")
    a, b = read_sheet(args.a), read_sheet(args.b)
    both = [i for i in key["items"] if i in a and i in b]
    run_of = {i: key["items"][i]["run"] for i in both}
    weight = {i: 1.0 / (key["items"][i].get("inclusion_probability") or 1.0) for i in both}
    out = {"prompt_hash": key["prompt_hash"], "language": key.get("language"), "judges": {},
           "annotators": {}, "seed": args.seed, "bootstrap": BOOTSTRAP,
           "bootstrap_unit": "dialogue", "min_positives": MIN_POSITIVES,
           "positives_rule": "marked by either annotator"}
    lines = [f"E0b, validation of the judges (instructions {key['prompt_hash']}, "
             f"language {key.get('language') or 'all'}, {len(both)} utterances annotated by both)", ""]
    for m in MEASURES:
        ha, hb = [a[i][m] for i in both], [b[i][m] for i in both]
        k = judge_runs.kappa(ha, hb)
        lo, hi = interval(list(zip(ha, hb)), args.seed, [run_of[i] for i in both])
        positives = sum(1 for i in both if a[i][m] == POSITIVE[m] or b[i][m] == POSITIVE[m])
        out["annotators"][m] = {"kappa": _r(k), "ci95": [_r(lo), _r(hi)],
                                "weighted_kappa": _r(weighted_kappa(ha, hb, [weight[i] for i in both])),
                                "positives_either": positives,
                                "prevalence_a": _r(sum(1 for x in ha if x == POSITIVE[m]) / len(ha)) if ha else None,
                                "prevalence_b": _r(sum(1 for x in hb if x == POSITIVE[m]) / len(hb)) if hb else None}
        note = ("   <- not decidable" if _nan(k) else
                "   <- the annotators do not agree: revise the guide" if k < TENTATIVE else "")
        note += (f"   <- {positives} human positives: not validated" if positives < MIN_POSITIVES
                 else "")
        lines.append(f"annotators A-B   {m:12s} kappa {k:.2f} [{lo:.2f}, {hi:.2f}]  "
                     f"positives {positives}{note}")
    lines.append("")
    # Every judge with labels now (judges of the ladder can be added after the sheets were made:
    # the people annotate once, each new judge only labels the same utterances)
    current = {name: judge_runs.load_judgments(path) for name, path in judge_files(args.results).items()}
    out["error_rate"] = {}
    for judge, labels in sorted(current.items()):
        errors, asked = judge_runs.error_rate(labels)
        out["error_rate"][judge] = {"errors": errors, "asked": asked,
                                    "rate": round(errors / asked, 4) if asked else None}
        if errors:
            lines.append(f"judge {judge}: {errors}/{asked} utterances without an answer")
        res = {}
        for m in MEASURES:
            rows = []
            for i in both:
                item = key["items"][i]
                j = labels.get((item["run"], item["index"]))
                if j is None or "error" in j or family(item.get("model") or "") == family(judge):
                    continue
                rows.append((judge_labels(j)[m], a[i][m], b[i][m], run_of[i], weight[i]))
            if not rows:
                continue
            jd, ha, hb, runs, ws = (list(x) for x in zip(*rows))
            positives = sum(1 for x, y in zip(ha, hb) if POSITIVE[m] in (x, y))
            r = {"n": len(rows), "positives_either": positives,
                 "prevalence_judge": _r(sum(1 for x in jd if x == POSITIVE[m]) / len(jd))}
            for name, x, y in (("A", jd, ha), ("B", jd, hb)):
                k = judge_runs.kappa(x, y)
                lo, hi = interval(list(zip(x, y)), args.seed, runs)
                r[name] = {"kappa": _r(k), "ci95": [_r(lo), _r(hi)],
                           "weighted_kappa": _r(weighted_kappa(x, y, ws)),
                           "prevalence": _r(sum(1 for v in y if v == POSITIVE[m]) / len(y)),
                           **agreement_figures(x, y, m)}
            r["decision"] = decide(r["A"]["kappa"], r["B"]["kappa"],
                                   out["annotators"][m]["kappa"], positives)
            res[m] = r
            lines.append(f"judge {judge:8s} {m:12s} n={len(rows):4d}  "
                         f"A {r['A']['kappa']:.2f} [{r['A']['ci95'][0]:.2f}, {r['A']['ci95'][1]:.2f}]"
                         f" sens {r['A']['sensitivity']} spec {r['A']['specificity']}  "
                         f"B {r['B']['kappa']:.2f} [{r['B']['ci95'][0]:.2f}, {r['B']['ci95'][1]:.2f}]"
                         f" sens {r['B']['sensitivity']} spec {r['B']['specificity']}"
                         f"  -> {r['decision']}")
        out["judges"][judge] = res
    worst = {j: worst_kappa(res) for j, res in out["judges"].items() if res}
    order, chosen, tentative = choose(worst, args.order.split(",") if args.order else [])
    out["worst"] = {j: _r(v) for j, v in worst.items()}
    out["order"], out["chosen_this_language"], out["tentative"] = order, chosen, tentative
    lines += ["", "Ladder (this language only): " + " > ".join(f"{j} ({worst[j]:.2f})" for j in order),
              "Best in this language: " + (f"{chosen}{' (tentative)' if tentative else ''}"
                                           if chosen else "none"),
              "The judge of the experiments is chosen across the languages (combine)."]
    with open(os.path.join(folder, "report.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print("\n".join(lines))


def combine_reports(reports: dict, order: list[str]) -> dict:
    """One judge for every language: a judge's worst kappa is the lowest over the labels, the
    annotators and the languages (nan if it is missing in a language or not validated there)."""
    judges = set().union(*(r.get("judges", {}).keys() for r in reports.values())) if reports else set()
    worst = {}
    for j in judges:
        values = [worst_kappa(r.get("judges", {}).get(j) or {}) for r in reports.values()]
        worst[j] = float("nan") if any(_nan(v) for v in values) else min(values)
    order, chosen, tentative = choose(worst, order)
    return {"languages": sorted(reports), "order": order,
            "worst": {j: _r(v) for j, v in worst.items()}, "chosen": chosen,
            "tentative": tentative,
            "rule": "the first judge of the ladder with kappa >= 0.8 on both labels, both "
                    "annotators and every language; else the best >= 0.67 (tentative); a "
                    "label/language not validated, not decidable or where the annotators "
                    "disagree validates no judge"}


def combine(args) -> None:
    reports = {}
    for path in args.reports:
        with open(path, encoding="utf-8") as f:
            rep = json.load(f)
        reports[rep.get("language") or os.path.basename(os.path.dirname(os.path.dirname(path)))] = rep
    out = combine_reports(reports, args.order.split(",") if args.order else [])
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print("Ladder (every language): " + " > ".join(f"{j} ({out['worst'][j]})" for j in out["order"]))
    print("Chosen judge: " + (f"{out['chosen']}{' (tentative conclusions)' if out['tentative'] else ''}"
                              if out["chosen"] else "none: the fallback plan") + f" -> {args.out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("split")
    p.add_argument("results")
    p.add_argument("--dev", type=float, default=0.5, help="Share of dialogues to adjust the judge")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("sheets")
    p.add_argument("results")
    p.add_argument("--n", type=int, default=150, help="Utterances in the sample (per language)")
    p.add_argument("--language", default="", help="Only the dialogues in this language")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--force", action="store_true",
                   help="Make the sheets again over an existing key.json (annotations are lost)")
    p = sub.add_parser("report")
    p.add_argument("results")
    p.add_argument("--a", required=True, help="The sheet of annotator A, filled in")
    p.add_argument("--b", required=True, help="The sheet of annotator B, filled in")
    p.add_argument("--order", default="",
                   help="The ladder of candidate judges (their tags, comma-separated), fixed before "
                        "the results: the first that reaches 0.8 is chosen")
    p.add_argument("--seed", type=int, default=7)
    p = sub.add_parser("combine")
    p.add_argument("reports", nargs="+", help="The report.json of each language (<results>/e0/)")
    p.add_argument("--order", default="", help="The ladder (tags, comma-separated)")
    p.add_argument("--out", required=True, help="The combined decision (results/e0b/report.json)")
    args = parser.parse_args()
    {"split": split, "sheets": sheets, "report": report, "combine": combine}[args.command](args)


if __name__ == "__main__":
    main()
