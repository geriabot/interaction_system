# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The conversational fillers: the fixed phrases per language and the skill that gives them to
the tree (one per line). When they are said is the tree's (WithFillers), tested in the system
tests (evaluation/simulation/interaction_sim/test/test_dialogues.py)."""

from interaction_intelligence.dialog_context import DialogContext
import pytest

from interaction_intelligence.languages import UnknownLanguageError, available_languages
from interaction_intelligence.llm_client import default_filler_phrases
from interaction_intelligence.skills import SkillEnv, SkillInput, get_skill


def test_every_language_has_fillers():
    for phrases in (lang.filler_phrases for lang in available_languages()):
        # The ladder: one phrase per step (2, 5, 9 and 14 s) and the one that repeats; one line each
        assert len(phrases) == 5
        assert len(set(phrases)) == len(phrases)
        assert all(p.strip() and "\n" not in p for p in phrases)


def test_default_fillers_follow_the_language():
    # In the order of the ladder (decided by the author, 2026-11-01)
    assert default_filler_phrases("Spanish") == (
        "Mmm…", "Un momento…", "Déjame pensarlo un momento…", "Ya casi lo tengo…",
        "Sigo con ello, un segundito…")
    assert default_filler_phrases("English") == (
        "Hmm…", "One moment…", "Let me think about it for a moment…", "Almost there…",
        "Still on it, just a second…")
    # A language without a language file is an error, never another language's phrases
    with pytest.raises(UnknownLanguageError):
        default_filler_phrases("Klingon")


def _ask(phrases):
    env = SkillEnv(llm=None, ctx=DialogContext(), filler_phrases=phrases)
    state = {"mode": "slot_filling"}
    result = get_skill("fillers")(env, state, SkillInput())
    return result, state


def test_fillers_skill_gives_one_phrase_per_line_without_the_llm():
    result, state = _ask(("Hmm…", " One moment… ", "", "Almost there…"))
    assert result.reply.split("\n") == ["Hmm…", "One moment…", "Almost there…"]  # in order
    assert result.label == "fillers"
    assert state == {"mode": "slot_filling"}  # the state of the dialogue is not touched


def test_fillers_skill_without_phrases():
    # No phrases (e.g. filler_phrases set to blanks): the tree has none to say
    result, _ = _ask(())
    assert result.reply == ""
