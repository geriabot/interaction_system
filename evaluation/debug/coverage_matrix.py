# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The coverage matrix of layer 1 of the debugging before E0a (debug/coverage.yaml): checks that
every test it names exists, and prints the matrix with the rows that are not covered in a
language and do not say why (`layer1`). No ROS.

    python3 debug/coverage_matrix.py           # the matrix and the gaps; status 1 on an error
    python3 debug/coverage_matrix.py --gaps    # only the gaps

Running the tests themselves is doc/DEBUGGING.md, "Layer 1".
"""

import argparse
import ast
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
MATRIX = os.path.join(HERE, "coverage.yaml")
LANGUAGES = ("en", "es")


def test_names(path: str) -> set[str]:
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    return {n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name.startswith("test_")}


def check(matrix: dict, repo: str = REPO) -> tuple[list[str], list[dict]]:
    """(errors, rows): errors are tests or aliases that do not exist and malformed rows; each
    row gets `covered` {language: [tests]} and `gap` [languages without a test or a reason]."""
    errors, names = [], {}
    for alias, spec in matrix["files"].items():
        path = os.path.join(repo, spec["path"])
        if not os.path.exists(path):
            errors.append(f"{alias}: no file {spec['path']}")
            names[alias] = set()
        else:
            names[alias] = test_names(path)
        unknown = set(spec.get("languages") or []) - set(LANGUAGES)
        if unknown or not spec.get("languages"):
            errors.append(f"{alias}: languages must be some of {LANGUAGES}")
    rows, seen = [], set()
    for m in matrix["mechanisms"]:
        if m["id"] in seen:
            errors.append(f"{m['id']}: duplicated id")
        seen.add(m["id"])
        for key in ("name", "expected"):
            if not m.get(key):
                errors.append(f"{m['id']}: no {key}")
        covered = {lang: [] for lang in LANGUAGES}
        for test in m.get("tests") or []:
            alias, sep, name = test.partition("::")
            if not sep or alias not in names:
                errors.append(f"{m['id']}: unknown file alias in {test}")
                continue
            if name not in names[alias]:
                errors.append(f"{m['id']}: no test {name} in {matrix['files'][alias]['path']}")
                continue
            for lang in matrix["files"][alias]["languages"]:
                covered[lang].append(test)
        gap = [lang for lang in LANGUAGES if not covered[lang] and not m.get("layer1")]
        rows.append({**m, "covered": covered, "gap": gap})
    return errors, rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gaps", action="store_true", help="Only the rows with a gap")
    args = parser.parse_args(argv)
    with open(MATRIX, encoding="utf-8") as f:
        matrix = yaml.safe_load(f)
    errors, rows = check(matrix)
    for row in rows:
        if args.gaps and not row["gap"]:
            continue
        cells = " ".join(f"{lang}:{len(row['covered'][lang])}" for lang in LANGUAGES)
        note = f"  GAP in {','.join(row['gap'])}" if row["gap"] else ""
        why = f"  (layer1: {row['layer1']})" if row.get("layer1") else ""
        print(f"{row['id']:14s} {cells:10s} {row['name']}{note}{why}")
    gaps = [r["id"] for r in rows if r["gap"]]
    print(f"\n{len(rows)} rows; gaps: {', '.join(gaps) if gaps else 'none'}")
    for e in errors:
        print(f"ERROR {e}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
