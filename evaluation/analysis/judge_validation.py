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
    python3 judge_validation.py sheets <pilot results> [--n 150] [--language Spanish]
                                       [--flag-judges T1,T2] [--force]
        After the judges have labelled the dialogues (judge_runs.py): a sample of the robot
        utterances of the validation half, written as one annotation sheet per annotator (they see
        neither the judges' labels, nor the flag, nor the condition, nor each other's sheet).
        Design DESIGN (enriched_v1): the strata are flag x persona x condition, where the flag is
        1 when any of the flag judges (--flag-judges; by default every judge with labels, which in
        run_e0b.py are the local judges of the ladder) labels the utterance out of the frame or
        unsupported. Half of the sample (rounded down) goes to the flagged strata, the rest to the
        unflagged ones; within each half, equal allocation over persona x condition (a stratum
        smaller than its share gives the rest to the others of its half; what the flagged half
        cannot use goes to the unflagged half, and the reverse). Out-of-frame and unsupported
        utterances are rare, so a sample that ignored the judges would hold too few human
        positives; the large unflagged half keeps every utterance a non-zero inclusion
        probability, which the weighted statistics of `report` need. Its size: --n (150), raised
        by SIZE_STEP up to MAX_N until the expected human positives of each label reach
        MIN_POSITIVES, estimated on the development half (each stratum's allocation times the
        label's prevalence among the development utterances of the same stratum, by the flag
        judges, their mean); this only sizes the sample. The stratum and the inclusion probability
        of every utterance, and the size of every stratum, are stored in key.json. An existing
        key.json is never overwritten (the annotations made on it would be lost) unless --force.
    python3 judge_validation.py report <pilot results> --a sheet_a.csv --b sheet_b.csv
        Per judge, label and annotator, every utterance weighted by the inverse of its inclusion
        probability (Horvitz-Thompson): Cohen's kappa on the weighted 2x2 table (the DECISION
        statistic), the judge's sensitivity and specificity (the annotator as reference), each
        with a 95% interval from a stratified cluster bootstrap (DIALOGUES resampled within each
        persona x condition cell: the utterances of a dialogue are not independent); the
        unweighted kappa (reported only: under the enriched sample it favours the judges that
        defined the strata); the prevalence in the sample and the weighted (population) one.
        The decision of each judge and label (weighted kappa):
          kappa >= 0.8 with each annotator      use the judge
          0.67 <= kappa < 0.8 with each one     use it; conclusions on that measure are tentative
          kappa < 0.67 with either              do not use it as is: the fallback plan (below)
        If the annotators do not agree with each other (UNWEIGHTED kappa A-B < 0.67: the
        reliability of the guide; the weighted one is reported), the measure itself is not
        defined clearly enough: no judge is validated on it, the annotation guide is revised.
        A sample drawn with another design (key.json without "design": DESIGN) is refused.
        A label with fewer than MIN_POSITIVES human positives (an utterance marked out of the
        frame, or unsupported, by EITHER annotator: the looser count, so that a label both
        annotators rarely agree on is not declared rare) is "not validated": never used. A kappa
        that is not defined (one label only) is "not decidable": never used. The report stops if
        the judge's instructions changed after the sheets were made.
    python3 judge_validation.py combine <report.json of each language>... --order T1,T2 --out F
        One judge for every language: the first of the ladder that passes on both labels, both
        annotators and EVERY language, by the same statistic (the weighted kappa; else the best
        tentative one, else none); writes F (results/e0b/report.json). A report of another design
        is refused.

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
# The size of the sample: from --n, SIZE_STEP more at a time up to MAX_N, until the expected
# positives of every label reach MIN_POSITIVES (a rule fixed before the sheets: 2026-11-02)
SIZE_STEP, MAX_N = 50, 300
BOOTSTRAP = 2000
# The sampling design recorded in key.json and report.json. enriched_v1 (2026-11-05): strata
# flag x persona x condition, half of the sample to the utterances the local judges flag, the
# decision on the kappa weighted by the inverse inclusion probabilities. A sample or report of
# another design (the earlier persona x condition one has no "design") is never re-read as this one.
DESIGN = "enriched_v1"
# The statistic the decision rule and the ladder read, in each annotator's figures of a judge
DECISION_STATISTIC = "weighted_kappa"
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


def positive(lab: dict, measure: str) -> bool:
    return lab[measure] == POSITIVE[measure]


def flagged(x, judges=None) -> int:
    """1 when any of the flag judges (all the item's judges when None) labels the item positive
    on either label (out of the frame or unsupported), else 0."""
    labs = x[5]
    names = labs if judges is None else judges
    return int(any(positive(labs[j], m) for j in names if j in labs for m in MEASURES))


def cell_of(x) -> str:
    """The persona x condition cell of an item (a property of its dialogue)."""
    return f"{x[1].get('persona', '?')}|{x[1].get('condition', '?')}"


def stratum_of(x, judges=None) -> str:
    """The stratum of an item: flag x persona x condition, as '<flag>|<persona>|<condition>'."""
    return f"{flagged(x, judges)}|{cell_of(x)}"


def _cells(items: list, judges=None) -> dict:
    cells: dict[str, list] = defaultdict(list)
    for x in items:
        cells[stratum_of(x, judges)].append(x)
    return cells


def enriched_allocation(sizes: dict, n: int) -> dict:
    """{stratum: draws} for a sample of n over strata '<flag>|<persona>|<condition>' of the given
    sizes: n // 2 to the flagged strata, the rest to the unflagged ones, each half with equal
    allocation over its strata (_allocate: a stratum smaller than its share gives the rest to the
    others of its half). What one half cannot use (too few utterances) goes to the other."""
    half = {f: {k: v for k, v in sizes.items() if k.split("|", 1)[0] == f} for f in ("1", "0")}
    n = min(n, sum(sizes.values()))
    flag = _allocate(n // 2, half["1"])
    rest = _allocate(n - sum(flag.values()), half["0"])
    if sum(flag.values()) + sum(rest.values()) < n:   # the unflagged half is short: back to flagged
        flag = _allocate(n - sum(rest.values()), half["1"])
    return {**flag, **rest}


def stratified_sample(items: list, n: int, rng: random.Random, judges=None) -> list:
    """(item, stratum, inclusion probability) for the sample of design DESIGN: flag x persona x
    condition strata, enriched_allocation, a uniform draw within each stratum. Every item of a
    stratum has the same inclusion probability: drawn / size of the stratum. ValueError when a
    stratum gets no draw (its items would have a zero inclusion probability: n is too small)."""
    cells = _cells(items, judges)
    alloc = enriched_allocation({k: len(v) for k, v in cells.items()}, n)
    empty = sorted(k for k in cells if not alloc.get(k))
    if empty:
        raise ValueError(f"no draw in strata {empty}: their utterances would have a zero inclusion "
                         f"probability (n={n} is too small for {len(cells)} strata)")
    out = []
    for key in sorted(cells):
        group = cells[key]
        for x in rng.sample(group, alloc[key]):
            out.append((x, key, alloc[key] / len(group)))
    return out


def stratum_prevalence(development: list, judges=None) -> dict:
    """{stratum or flag class ('1', '0'): {measure: prevalence}} on the development half: the mean
    over the flag judges of the share of the stratum's utterances each labels positive. The flag
    is defined by the same judges, so an unflagged stratum has prevalence 0 by construction (the
    judges' misses cannot be seen there): the expected positives count the flagged strata only."""
    groups: dict[str, list] = defaultdict(list)
    for x in development:
        s = stratum_of(x, judges)
        groups[s].append(x)
        groups[s.split("|", 1)[0]].append(x)
    out = {}
    for k, xs in groups.items():
        labs = [x[5][j] for x in xs for j in (x[5] if judges is None else judges) if j in x[5]]
        if labs:
            out[k] = {m: sum(positive(lab, m) for lab in labs) / len(labs) for m in MEASURES}
    return out


def expected_positives(items: list, development: list, n: int, judges=None) -> dict:
    """Per measure, the human positives a sample of n of `items` is expected to hold: each
    stratum's allocation times the label's prevalence among the development utterances of the
    same stratum (or, with none there, of the same flag class), by the flag judges (their mean).
    It only sizes the sample. None for a measure when no development utterance informs it."""
    alloc = enriched_allocation({k: len(v) for k, v in _cells(items, judges).items()}, n)
    prev = stratum_prevalence(development, judges)
    out = {}
    for m in MEASURES:
        total, known = 0.0, False
        for k, drawn in alloc.items():
            p = prev.get(k) or prev.get(k.split("|", 1)[0])
            if p is not None:
                known = True
                total += drawn * p[m]
        out[m] = round(total, 1) if known else None
    return out


def sample_size(items: list, development: list, n: int, judges=None) -> tuple[int, dict]:
    """The size rule: n, then SIZE_STEP more at a time up to MAX_N (never more than there are),
    until every measure is expected to reach MIN_POSITIVES; without a development half, n."""
    n = min(n, len(items))
    expected = expected_positives(items, development, n, judges) if development else {}
    while (expected and any(v is not None and v < MIN_POSITIVES for v in expected.values())
           and n < min(MAX_N, len(items))):
        n = min(n + SIZE_STEP, MAX_N, len(items))
        expected = expected_positives(items, development, n, judges)
    return n, expected


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
    development = []  # the development half: it only sizes the sample
    judges = judge_files(args.results)
    if not judges:
        sys.exit("No judge's labels: run judge_runs.py on the dialogues first (one --tag per "
                 "candidate judge, e.g. gemini and openai)")
    labels = {name: judge_runs.load_judgments(path) for name, path in judges.items()}
    # The judges whose labels define the flag: the local judges of the ladder (run_e0b.py passes
    # them); by default every judge with labels now
    flag_judges = ([t for t in args.flag_judges.split(",") if t]
                   if getattr(args, "flag_judges", "") else sorted(judges))
    missing = [t for t in flag_judges if t not in judges]
    if missing:
        sys.exit(f"No labels of the flag judges {missing} in {args.results}: run judge_runs.py with "
                 f"those tags first")
    items = []
    for run, rec in judge_runs.load_runs(args.results):
        if args.language and rec.get("language", args.language) != args.language:
            continue
        into = development if validation is not None and run not in validation else items
        for i, text, history in judge_runs.utterances(rec):
            js = {name: lab.get((run, i)) for name, lab in labels.items()}
            if all(j and "error" not in j for j in js.values()):
                into.append((run, rec, i, text, history,
                             {name: judge_labels(j) for name, j in js.items()}))
    if not items:
        sys.exit("No judged utterance in the validation dialogues: run judge_runs.py on the "
                 "pilot first (with the frozen instructions)")
    n, expected = sample_size(items, development, args.n, flag_judges)
    rng = random.Random(args.seed)
    try:
        chosen = stratified_sample(items, n, rng, flag_judges)
    except ValueError as exc:
        sys.exit(f"The sample cannot cover every stratum: {exc}. Give a larger --n")
    sizes = {k: len(v) for k, v in _cells(items, flag_judges).items()}
    taken: dict[str, int] = defaultdict(int)
    for _, stratum, _ in chosen:
        taken[stratum] += 1
    # Grouped by dialogue, in the order said within each: the annotator reads one conversation at
    # a time; the order of the dialogues is random
    runs = sorted({x[0][0] for x in chosen})
    rng.shuffle(runs)
    rank = {run: k for k, run in enumerate(runs)}
    chosen.sort(key=lambda x: (rank[x[0][0]], x[0][2]))
    key = {"design": DESIGN, "prompt_hash": prompt_hash(), "language": args.language,
           "n": len(chosen), "judges": sorted(judges), "flag_judges": flag_judges,
           "seed": args.seed,
           "sampling": "strata flag x persona x condition of the validation half (flag: any flag "
                       "judge labels the utterance out of the frame or unsupported); n // 2 to "
                       "the flagged strata, the rest to the unflagged ones, equal allocation over "
                       "persona x condition within each half, leftovers to the other half; a "
                       "uniform draw within each stratum; inclusion probability = taken / size",
           "strata": {k: {"size": sizes[k], "taken": taken.get(k, 0)} for k in sorted(sizes)},
           "size_rule": {"start": args.n, "step": SIZE_STEP, "max": MAX_N,
                         "min_positives": MIN_POSITIVES, "chosen": n,
                         "expected_positives": expected,
                         "prevalence": "development half: the flag judges' mean in the same "
                                       "stratum (else the same flag class); 0 in the unflagged "
                                       "strata by construction; it only sizes the sample",
                         "development_utterances": len(development)},
           "items": {}}
    for name in ("a", "b"):
        with open(os.path.join(folder, f"sheet_{name}.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["id", "task", "material", "conversation_before", "robot_utterance",
                        "in_frame (1/0)", "unsupported (1/0)", "note"])
            for n, ((run, rec, i, text, history, _), _, _) in enumerate(chosen, 1):
                goal = rec.get("goal", {})
                w.writerow([n, goal.get("task_description", ""), judge_runs.material(goal),
                            history, text, "", "", ""])
    for n, ((run, rec, i, text, history, labs), stratum, prob) in enumerate(chosen, 1):
        key["items"][str(n)] = {"run": run, "index": i, "judges": labs,
                                "condition": rec.get("condition"), "model": rec.get("model"),
                                "persona": rec.get("persona"), "stratum": stratum,
                                "flag": int(stratum.split("|", 1)[0]),
                                "inclusion_probability": round(prob, 6)}
    with open(key_path, "w", encoding="utf-8") as f:
        json.dump(key, f, indent=1)
    flagged_n = sum(v for k, v in taken.items() if k.startswith("1|"))
    print(f"{len(chosen)} utterances, {flagged_n} flagged by {', '.join(flag_judges)} (size rule: "
          f"expected positives {expected or 'not estimated'}) -> "
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


def _percentile_interval(values: list) -> tuple:
    values = sorted(v for v in values if v is not None and v == v)
    return ((values[int(0.025 * len(values))], values[int(0.975 * len(values)) - 1]) if values
            else (float("nan"),) * 2)


def _kappa_table(pp: float, pn: float, np_: float, nn: float) -> float:
    """Cohen's kappa from a 2x2 table (rater 1 positive/negative x rater 2 positive/negative),
    counts or estimated totals; nan when it is not defined (one label only)."""
    total = pp + pn + np_ + nn
    if total <= 0:
        return float("nan")
    po = (pp + nn) / total
    p1, p2 = (pp + pn) / total, (pp + np_) / total
    pe = p1 * p2 + (1 - p1) * (1 - p2)
    return float("nan") if 1 - pe < 1e-12 else (po - pe) / (1 - pe)


def weighted_figures(judge_: list[int], human: list[int], w: list[float], measure: str) -> dict:
    """The judge (or a second annotator) against the human reference on the measure's positive
    label, every utterance weighted by w (the inverse of its inclusion probability): the 2x2 table
    of the population estimated with the Horvitz-Thompson weights, and from it Cohen's kappa, the
    sensitivity, the specificity and the reference's prevalence; beside them the unweighted kappa
    of the sample (`kappa`)."""
    pos = POSITIVE[measure]
    tp = fn = fp = tn = 0.0
    utp = ufn = ufp = utn = 0
    for j, h, wi in zip(judge_, human, w):
        if h == pos:
            if j == pos:
                tp += wi
                utp += 1
            else:
                fn += wi
                ufn += 1
        elif j == pos:
            fp += wi
            ufp += 1
        else:
            tn += wi
            utn += 1
    total = tp + fn + fp + tn
    return {"weighted_kappa": _kappa_table(tp, fp, fn, tn),
            "weighted_sensitivity": tp / (tp + fn) if tp + fn else None,
            "weighted_specificity": tn / (tn + fp) if tn + fp else None,
            "weighted_prevalence": (tp + fn) / total if total else None,
            "kappa": _kappa_table(utp, ufp, ufn, utn)}


def _row_figures(rows: list, measure: str) -> dict:
    """weighted_figures of rows (cell, dialogue, judge, human, weight)."""
    return weighted_figures([r[2] for r in rows], [r[3] for r in rows], [r[4] for r in rows],
                            measure)


def stratified_bootstrap(rows: list, seed: int, stat, n: int = BOOTSTRAP) -> dict:
    """{statistic: its 95% percentile interval} for the statistics stat(rows) returns, from a
    stratified cluster bootstrap. rows: (cell, dialogue, ...). Within each persona x condition
    cell (the part of the strata that belongs to the dialogue: the flag varies within a dialogue)
    as many dialogues as it holds are drawn with replacement, each with all its sampled
    utterances and their weights, and the statistics are computed again on every resample."""
    rng = random.Random(seed)
    cells: dict = defaultdict(lambda: defaultdict(list))
    for row in rows:
        cells[row[0]][row[1]].append(row)
    plan = [(sorted(d, key=str), d) for _, d in sorted(cells.items(), key=lambda c: str(c[0]))]
    draws: dict = defaultdict(list)
    for _ in range(n):
        s = []
        for keys, dialogues in plan:
            for _ in keys:
                s += dialogues[keys[rng.randrange(len(keys))]]
        for name, v in stat(s).items():
            draws[name].append(v)
    return {name: _percentile_interval(v) for name, v in draws.items()}


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
    """The lowest decision statistic (DECISION_STATISTIC, the weighted kappa) of a judge over its
    labels and annotators; nan when a label is missing or any of them is not decidable, not
    validated or the annotators disagree (never chosen)."""
    if set(res) != set(MEASURES):
        return float("nan")
    values = []
    for r in res.values():
        if r["decision"] in (NOT_DECIDABLE, NOT_VALIDATED, REVISE):
            return float("nan")
        values += [r["A"][DECISION_STATISTIC], r["B"][DECISION_STATISTIC]]
    return float("nan") if any(v is None or _nan(v) for v in values) else min(values)


def _r(x):
    return x if x is None or _nan(x) else round(x, 3)


def _f(x) -> str:
    return "n/a" if x is None else f"{x:.2f}"


def check_design(found, what: str) -> None:
    """Stops unless `found` is this design: a sample or report of another design is never re-read
    as if it were of this one (its inclusion probabilities and its statistic differ)."""
    if found != DESIGN:
        sys.exit(f"{what} was made with the sampling design "
                 f"{found or 'persona x condition (no design recorded)'}, not {DESIGN}: its "
                 f"inclusion probabilities and decision statistic are not those of {DESIGN}, so it "
                 f"is not read as such. Draw the sheets again with this version (sheets --force, "
                 f"only before anyone annotates), or report it with the version that drew it")


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


def _weights(key: dict, ids: list, key_path: str) -> dict:
    """{item id: inverse inclusion probability}: the stratum's size / taken (key.json "strata"),
    else 1 / the item's recorded inclusion probability."""
    strata = key.get("strata") or {}
    out = {}
    for i in ids:
        item = key["items"][i]
        s = strata.get(item.get("stratum")) or {}
        if s.get("taken"):
            out[i] = s["size"] / s["taken"]
        elif item.get("inclusion_probability"):
            out[i] = 1.0 / item["inclusion_probability"]
        else:
            sys.exit(f"Item {i} of {key_path} has no inclusion probability: the weighted "
                     f"statistics cannot be computed")
    return out


def report(args) -> None:
    """Per candidate judge and label, with each annotator (leaving out the utterances of a system
    of the judge's own family: a judge does not judge its own family): the weighted kappa (the
    decision statistic), sensitivity and specificity with their intervals, the unweighted kappa,
    the sample's and the weighted prevalence; between the annotators, the unweighted kappa (the
    reliability check) and the weighted one; the decision of each judge in this language (the one
    judge of every language is chosen by `combine`)."""
    folder = e0_dir(args.results)
    key_path = os.path.join(folder, "key.json")
    with open(key_path, encoding="utf-8") as f:
        key = json.load(f)
    check_design(key.get("design"), f"The sample {key_path}")
    if key["prompt_hash"] != prompt_hash():
        sys.exit("The judge's instructions changed after the sheets were made (key.json "
                 f"{key['prompt_hash']}, now {prompt_hash()}): this sample does not validate the "
                 "current instructions. Stopping")
    a, b = read_sheet(args.a), read_sheet(args.b)
    both = [i for i in key["items"] if i in a and i in b]
    run_of = {i: key["items"][i]["run"] for i in both}
    cell = {i: f"{key['items'][i].get('persona', '?')}|{key['items'][i].get('condition', '?')}"
            for i in both}
    weight = _weights(key, both, key_path)
    out = {"design": DESIGN, "decision_statistic": DECISION_STATISTIC,
           "prompt_hash": key["prompt_hash"], "language": key.get("language"), "judges": {},
           "annotators": {}, "seed": args.seed, "bootstrap": BOOTSTRAP,
           "bootstrap_unit": "dialogue, resampled within each persona x condition cell",
           "weights": "inverse inclusion probability (Horvitz-Thompson)",
           "min_positives": MIN_POSITIVES,
           "positives_rule": "marked by either annotator, counted in the sample (unweighted)"}
    lines = [f"E0b, validation of the judges (instructions {key['prompt_hash']}, "
             f"language {key.get('language') or 'all'}, {len(both)} utterances annotated by both; "
             f"design {DESIGN}: the decision reads the weighted kappa)", ""]
    for m in MEASURES:
        rows = [(cell[i], run_of[i], b[i][m], a[i][m], weight[i]) for i in both]
        fig = _row_figures(rows, m)
        ci = stratified_bootstrap(rows, args.seed, lambda s, m=m: _row_figures(s, m))
        k = fig["kappa"]
        positives = sum(1 for i in both if a[i][m] == POSITIVE[m] or b[i][m] == POSITIVE[m])
        prev_b = _row_figures([(c, d, x, y, w) for c, d, y, x, w in rows], m)["weighted_prevalence"]
        out["annotators"][m] = {
            "kappa": _r(k), "ci95": [_r(v) for v in ci["kappa"]],
            "weighted_kappa": _r(fig["weighted_kappa"]),
            "weighted_ci95": [_r(v) for v in ci["weighted_kappa"]],
            "positives_either": positives,
            "prevalence_a": _r(sum(1 for i in both if a[i][m] == POSITIVE[m]) / len(both)) if both else None,
            "prevalence_b": _r(sum(1 for i in both if b[i][m] == POSITIVE[m]) / len(both)) if both else None,
            "weighted_prevalence_a": _r(fig["weighted_prevalence"]),
            "weighted_prevalence_b": _r(prev_b)}
        note = ("   <- not decidable" if _nan(k) else
                "   <- the annotators do not agree: revise the guide" if k < TENTATIVE else "")
        note += (f"   <- {positives} human positives: not validated" if positives < MIN_POSITIVES
                 else "")
        lines.append(f"annotators A-B   {m:12s} kappa {k:.2f} [{ci['kappa'][0]:.2f}, "
                     f"{ci['kappa'][1]:.2f}]  weighted {fig['weighted_kappa']:.2f}  "
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
                rows.append((judge_labels(j)[m], a[i][m], b[i][m], run_of[i], weight[i], cell[i]))
            if not rows:
                continue
            jd, ha, hb, runs, ws, cells = (list(x) for x in zip(*rows))
            positives = sum(1 for x, y in zip(ha, hb) if POSITIVE[m] in (x, y))
            r = {"n": len(rows), "positives_either": positives,
                 "prevalence_judge": _r(sum(1 for x in jd if x == POSITIVE[m]) / len(jd)),
                 "weighted_prevalence_judge": _r(sum(w for x, w in zip(jd, ws) if x == POSITIVE[m])
                                                 / sum(ws))}
            for name, human in (("A", ha), ("B", hb)):
                pair = list(zip(cells, runs, jd, human, ws))
                fig = _row_figures(pair, m)
                ci = stratified_bootstrap(pair, args.seed, lambda s, m=m: _row_figures(s, m))
                r[name] = {"weighted_kappa": _r(fig["weighted_kappa"]),
                           "weighted_ci95": [_r(v) for v in ci["weighted_kappa"]],
                           "weighted_sensitivity": _r(fig["weighted_sensitivity"]),
                           "weighted_sensitivity_ci95": [_r(v) for v in ci["weighted_sensitivity"]],
                           "weighted_specificity": _r(fig["weighted_specificity"]),
                           "weighted_specificity_ci95": [_r(v) for v in ci["weighted_specificity"]],
                           "kappa": _r(fig["kappa"]), "ci95": [_r(v) for v in ci["kappa"]],
                           "prevalence": _r(sum(1 for v in human if v == POSITIVE[m]) / len(human)),
                           "weighted_prevalence": _r(fig["weighted_prevalence"])}
            r["decision"] = decide(r["A"][DECISION_STATISTIC], r["B"][DECISION_STATISTIC],
                                   out["annotators"][m]["kappa"], positives)
            res[m] = r
            text = []
            for name in ("A", "B"):
                x = r[name]
                text.append(f"{name} wkappa {_f(x['weighted_kappa'])} [{_f(x['weighted_ci95'][0])}, "
                            f"{_f(x['weighted_ci95'][1])}] sens {_f(x['weighted_sensitivity'])} "
                            f"spec {_f(x['weighted_specificity'])} (kappa {_f(x['kappa'])})")
            lines.append(f"judge {judge:8s} {m:12s} n={len(rows):4d}  " + "  ".join(text)
                         + f"  -> {r['decision']}")
        out["judges"][judge] = res
    worst = {j: worst_kappa(res) for j, res in out["judges"].items() if res}
    order, chosen, tentative = choose(worst, args.order.split(",") if args.order else [])
    out["worst"] = {j: _r(v) for j, v in worst.items()}
    out["order"], out["chosen_this_language"], out["tentative"] = order, chosen, tentative
    lines += ["", "Ladder (this language only, weighted kappa): "
              + " > ".join(f"{j} ({worst[j]:.2f})" for j in order),
              "Best in this language: " + (f"{chosen}{' (tentative)' if tentative else ''}"
                                           if chosen else "none"),
              "The judge of the experiments is chosen across the languages (combine)."]
    with open(os.path.join(folder, "report.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print("\n".join(lines))


def combine_reports(reports: dict, order: list[str]) -> dict:
    """One judge for every language: a judge's worst kappa is the lowest decision statistic (the
    weighted kappa) over the labels, the annotators and the languages (nan if it is missing in a
    language or not validated there). A report of another design stops it."""
    for lang, rep in sorted(reports.items()):
        check_design(rep.get("design"), f"The report of {lang}")
    judges = set().union(*(r.get("judges", {}).keys() for r in reports.values())) if reports else set()
    worst = {}
    for j in judges:
        values = [worst_kappa(r.get("judges", {}).get(j) or {}) for r in reports.values()]
        worst[j] = float("nan") if any(_nan(v) for v in values) else min(values)
    order, chosen, tentative = choose(worst, order)
    return {"design": DESIGN, "decision_statistic": DECISION_STATISTIC,
            "languages": sorted(reports), "order": order,
            "worst": {j: _r(v) for j, v in worst.items()}, "chosen": chosen,
            "tentative": tentative,
            "rule": "the first judge of the ladder with weighted kappa >= 0.8 on both labels, "
                    "both annotators and every language; else the best >= 0.67 (tentative); a "
                    "label/language not validated, not decidable or where the annotators "
                    "disagree (unweighted kappa < 0.67) validates no judge"}


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
    p.add_argument("--n", type=int, default=150,
                   help="Utterances in the sample to start from (per language; the size rule may "
                        "raise it up to MAX_N)")
    p.add_argument("--language", default="", help="Only the dialogues in this language")
    p.add_argument("--flag-judges", default="",
                   help="The judges (tags, comma-separated) whose labels define the flagged "
                        "strata: the local judges of the ladder (default: every judge with labels)")
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
