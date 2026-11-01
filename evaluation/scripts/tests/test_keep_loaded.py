# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""--keep-loaded of run_experiment.py: on a shared GPU, runs one after the other leave the system
model loaded, so that a job waiting for free GPU memory does not take the gap between them
(decided 2026-10-31, for the debugging on the shared GPU server)."""

import os
import subprocess
import sys

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "run_experiment.py")


def test_the_option_exists():
    out = subprocess.run([sys.executable, SCRIPT, "--help"], capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    assert "--keep-loaded" in out.stdout


def test_the_unload_at_the_end_is_skipped_with_it():
    with open(SCRIPT, encoding="utf-8") as f:
        src = f.read()
    i = src.index("ollama_models.unload_all(ollama_base")
    guard = src.rfind("if ", 0, i)
    assert src[guard:i].startswith("if not args.keep_loaded and (")
