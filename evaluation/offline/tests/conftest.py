# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Pytest configuration: the offline evaluation package importable from its folder."""

import os
import sys

_OFFLINE = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _OFFLINE not in sys.path:
    sys.path.insert(0, _OFFLINE)
