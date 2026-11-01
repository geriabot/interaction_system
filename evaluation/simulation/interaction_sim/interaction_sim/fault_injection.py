# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The injector of model failures, inside the interaction intelligence (E3).

Loaded by the intelligence as one more of its skill_modules (it registers no skill): its import
wraps LLMDialogClient with the switch at $INTERACTION_SIM_FAULTS (llm_faults.install). The
experiment runner adds it, and sets the variable, for the conditions whose scenarios plan model
failures (`faults`); the method's code is not modified, and without a planned failure every call
goes to the model unchanged. See llm_faults.py.
"""

from .llm_faults import install

install()
