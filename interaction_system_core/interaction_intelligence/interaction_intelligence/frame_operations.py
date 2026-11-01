# Copyright 2026 Álvaro Quintana Camacho
# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Operations on a DialogFrame from what the NLU understood: the deterministic part of slot
filling (set a scalar, add to / remove from / replace a list, bring a conditional slot into
scope).
"""

import logging
from dataclasses import dataclass
from enum import Enum

from .dialog_frame import DialogFrame

_log = logging.getLogger("FrameOperations")


class OpKind(Enum):
    SET = "set"
    ADD = "add"
    REMOVE = "remove"
    REPLACE = "replace"


@dataclass
class Operation:
    slot: str
    kind: OpKind
    value: object  # str for SET, list[str] for ADD/REMOVE/REPLACE


def extractions_to_operations(
    frame: DialogFrame, extractions: list[dict], default_list_op: OpKind
) -> list[Operation]:
    """Convert validated extractions (with _add/_remove/_replace sentinels) to Operations."""
    ops: list[Operation] = []
    for extraction in extractions:
        slot = extraction.get("slot")
        value = extraction.get("value")
        if not slot or value is None:
            continue
        if frame.slot_type(slot) != "list_str":
            ops.append(Operation(slot, OpKind.SET, value))
            continue
        if extraction.get("_remove"):
            ops.append(Operation(slot, OpKind.REMOVE, value))
        elif extraction.get("_replace"):
            ops.append(Operation(slot, OpKind.REPLACE, value))
        elif extraction.get("_add"):
            ops.append(Operation(slot, OpKind.ADD, value))
        else:
            ops.append(Operation(slot, default_list_op, value))
    return ops


def apply_operations(frame: DialogFrame, operations: list[Operation]) -> int:
    """
    Apply operations in three stages:
      1. Auto-promote conditional parents when a child op targets a slot whose parent
         condition is not yet met: prevents the frame silently dropping child corrections
         because the parent isn't in scope.
      2. Batch all scalar SETs into a single try_update for atomic child reset.
      3. Apply list operations in order (REMOVE before ADD matters).
    Returns the number of changes applied.
    """
    if not operations or not frame:
        return 0

    promoted: dict = {}
    for op in operations:
        # Only equality conditions can be promoted; a numeric range has no single parent
        # value to set.
        promotion = frame.promotion_for(op.slot)
        if promotion is None:
            continue
        parent_slot, required_value = promotion
        if any(o.slot == parent_slot for o in operations):
            continue
        promoted[parent_slot] = required_value

    applied = 0

    scalar_updates: dict = dict(promoted)
    for op in operations:
        if op.kind == OpKind.SET:
            scalar_updates[op.slot] = op.value
    if scalar_updates:
        changed = frame.try_update(scalar_updates)
        applied += len(changed)
        if promoted:
            _log.info(f"Auto-promoted parent(s) {list(promoted.keys())}.")

    for op in operations:
        if op.kind == OpKind.SET:
            continue
        items = op.value if isinstance(op.value, list) else [op.value]
        if op.kind == OpKind.ADD:
            if frame.add_to_list(op.slot, items):
                applied += 1
        elif op.kind == OpKind.REMOVE:
            if frame.remove_from_list(op.slot, items):
                applied += 1
        elif op.kind == OpKind.REPLACE:
            if frame.replace_list(op.slot, items):
                applied += 1
    return applied


def corrections_to_extractions(corrections: dict) -> list[dict]:
    """Bridge the output of classify_intent to the extraction format of apply_operations."""
    extractions: list[dict] = []
    for slot, val in corrections.items():
        ex: dict = {"slot": slot, "value": val}
        if isinstance(val, dict):
            for kind in ("remove", "replace", "add"):
                if val.get(f"_{kind}"):
                    ex["value"] = val.get("items", [])
                    ex[f"_{kind}"] = True
                    break
        extractions.append(ex)
    return extractions
