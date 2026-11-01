# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Speech recognition errors injected on the simulated user's text, at a target word error rate.

Each word is, with probability wer, substituted (by a word that sounds alike, or a frequent
word), deleted, or followed by an inserted word, in the proportions of typical ASR errors
(substitutions dominate). Optionally the end of the sentence is cut (the recognizer closed the
phrase early). Deterministic for a given random generator.
"""

import random
import re
from dataclasses import dataclass

# Frequent short words a recognizer inserts or substitutes in
FILLERS = ["the", "a", "uh", "and", "to", "it", "is", "of", "in", "that"]

# Sound-alike substitutions (both directions)
CONFUSIONS = [
    ("two", "to"), ("four", "for"), ("eight", "ate"), ("tea", "the"), ("large", "large"),
    ("small", "smaller"), ("medium", "media"), ("yes", "yeah"), ("no", "know"),
    ("right", "write"), ("one", "won"), ("their", "there"), ("cream", "cream"),
    ("latte", "lot"), ("coffee", "copy"), ("steps", "stamps"), ("done", "dawn"),
    ("Paris", "pairs"), ("fifteen", "fifty"), ("thirteen", "thirty"),
]
_SOUNDALIKE = {}
for a, b in CONFUSIONS:
    if a != b:
        _SOUNDALIKE.setdefault(a.lower(), []).append(b)
        _SOUNDALIKE.setdefault(b.lower(), []).append(a)


@dataclass
class AsrNoise:
    wer: float = 0.1
    p_substitution: float = 0.6
    p_deletion: float = 0.25  # the rest are insertions
    p_cut_end: float = 0.0  # probability of losing the last words of the sentence

    def apply(self, text: str, rng: random.Random) -> str:
        words = re.findall(r"\S+", text)
        out: list[str] = []
        for word in words:
            if rng.random() >= self.wer:
                out.append(word)
                continue
            r = rng.random()
            if r < self.p_substitution:
                core = re.sub(r"\W", "", word).lower()
                options = _SOUNDALIKE.get(core) or FILLERS
                out.append(rng.choice(options))
            elif r < self.p_substitution + self.p_deletion:
                continue
            else:
                out.extend([word, rng.choice(FILLERS)])
        if out and self.p_cut_end > 0 and rng.random() < self.p_cut_end and len(out) > 3:
            out = out[: max(2, int(len(out) * rng.uniform(0.5, 0.8)))]
        return " ".join(out) if out else text
