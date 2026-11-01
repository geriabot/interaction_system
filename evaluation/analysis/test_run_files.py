# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The dialogue records of a results folder (score_runs.run_files) are runs/<model>/<condition>/
*.json only: the runtime files the runner leaves in the same folder (the switch of the fault
injector and the marker of the call recorder) are hidden, so they are never read as records (they
were not, and broke triage.py; found in the debugging of 2026-10-31)."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import score_runs  # noqa: E402

RUNNER = os.path.join(os.path.dirname(__file__), "..", "scripts", "run_experiment.py")


def test_only_the_dialogue_records_are_read(tmp_path):
    run_dir = tmp_path / "runs" / "qwen3_8b" / "bounded"
    run_dir.mkdir(parents=True)
    (run_dir / "sf_coffee__cooperative__r1.json").write_text(json.dumps({"success": True}))
    (run_dir / ".llm_faults.json").write_text(json.dumps({"seq": 1, "turn": 2, "faults": []}))
    (run_dir / ".model_calls.json").write_text(json.dumps({"seq": 1, "turn": 2}))
    files = score_runs.run_files(str(tmp_path))
    assert [os.path.basename(f) for f in files] == ["sf_coffee__cooperative__r1.json"]


def test_the_runner_writes_its_runtime_files_hidden():
    with open(RUNNER, encoding="utf-8") as f:
        src = f.read()
    assert 'os.path.join(out_dir, ".llm_faults.json")' in src
    assert 'os.path.join(out_dir, ".model_calls.json")' in src
