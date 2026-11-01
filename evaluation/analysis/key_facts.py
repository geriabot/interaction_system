# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Whether an answer states the key facts it must (the author, decision 6b of the methodology
review): one matcher for the offline benchmark of grounded answers (offline/benchmarks/
bench_qa_answer.py, key_facts of its dataset) and the dialogues (score_runs.py, qa_facts of the
scenarios). A key fact is a short text; "|" separates its accepted wordings. A wording with
numbers is present when every number is in the answer (exactly, after normalization: 9:30 =
9.30 = 09:30, 0,85 = 0.85, 15:00 = 15, 6:30 pm = 18:30); one without numbers when at least half of
its words are (by their first five letters, without accents). When the check cannot tell (the
words but not the number, some of the words) the fact is "unsure": flagged for review, never
failed.
"""

import re
import unicodedata

# Words that carry no fact (the fact's words are matched by their first 5 letters)
_STOP = {"the", "and", "for", "are", "with", "you", "your", "can", "has", "have", "una", "las",
         "los", "del", "con", "que", "por", "para", "est", "esta", "está", "the", "its", "that",
         "this", "from", "every", "each", "only", "solo"}
_YES = {"yes", "yeah", "yep", "si", "sure", "claro"}
_NO = {"no", "nope"}
_NUMBER = re.compile(r"(\d+(?:[.,:]\d+)?)(\s*(?:a\.?\s?m\.?|p\.?\s?m\.?)(?![a-z]))?", re.I)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def numbers(text: str) -> set[str]:
    """The numbers of a text, normalized: 9:30 = 9.30 = 09:30, 0,85 = 0.85, 15:00 = 15,
    6:30 pm = 18:30."""
    out = set()
    for num, ampm in _NUMBER.findall(_plain(text)):
        parts = re.split(r"[.,:]", num)
        hour = int(parts[0])
        rest = [p for p in parts[1:] if p.strip("0")]
        if ampm:
            if "p" in ampm and hour < 12:
                hour += 12
            elif "a" in ampm and hour == 12:
                hour = 0
        out.add(":".join([str(hour)] + rest))
    return out


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", _plain(text))
            if len(w) >= 3 and w not in _STOP and not w.isdigit()]


def fact_status(fact: str, answer: str) -> str:
    """present | missing | unsure, for one key fact (its wordings separated by "|")."""
    said_numbers, said_words = numbers(answer), _words(answer)
    best = "missing"
    for wording in fact.split("|"):
        want_numbers, want_words = numbers(wording), _words(wording)
        found = sum(1 for w in want_words if any(s.startswith(w[:5]) for s in said_words))
        if want_numbers:
            if want_numbers <= said_numbers:
                return "present"
            if found:  # the words, not the number (spelled out?)
                best = "unsure"
            continue
        if want_words and 2 * found >= len(want_words):
            return "present"
        if found:
            best = "unsure"
    return best
