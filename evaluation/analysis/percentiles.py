# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The one percentile of the evaluation (latencies of phase A, of the dialogues, of E0a's
decision): linear interpolation between the closest ranks, numpy's default ("linear", type 7 of
Hyndman and Fan). Imported by analysis/score_runs.py, scripts/run_e0a.py and
offline/core/phase_a.py and gate.py, so that a p95 means the same everywhere.
"""


def percentile(values, q: float) -> float | None:
    """The q-quantile (0 <= q <= 1) of the values; None when there are none."""
    values = sorted(values)
    if not values:
        return None
    k = (len(values) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)
