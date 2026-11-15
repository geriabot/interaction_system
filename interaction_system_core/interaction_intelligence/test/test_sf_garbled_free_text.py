# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""Slot filling: the extraction prompt tells the model that a line that does not make sense
gives no value, not even to a free-text slot (a garbled line was taken as a name)."""

import os

import yaml

_PROMPTS = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")


def test_a_garbled_line_gives_no_value_to_a_free_text_slot():
    with open(_PROMPTS) as f:
        prompts = yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]
    text = " ".join(prompts["extract_slots"].split())
    assert "gives NO value, not even to a free-text slot" in text
    assert 'act "other"' in text
