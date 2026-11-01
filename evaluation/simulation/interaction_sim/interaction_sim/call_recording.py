# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The recorder of the calls of the system's model, inside the interaction intelligence (the
debugging before E0a: evaluation/doc/DEBUGGING.md).

Loaded by the intelligence as one more of its skill_modules (it registers no skill; load it after
the modules that do, so that their skills are named in the record): its import wraps
LLMDialogClient's call to the gateway and the registered skills with the marker at
$INTERACTION_SIM_CALL_LOG (call_log.install). run_experiment.py adds it, and sets the variable,
for the experiment files with `record_model_calls: true`; the method's code is not modified,
and every call goes to the model unchanged. See call_log.py.
"""

from .call_log import install

install()
