# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The numbers said in a line, in digits or in words (English and Spanish), without the LLM.

Used by the quiz to grade an answer whose expected value is a number: a small model gave "forty"
for "how many hours are there in two days?" and "Mars has one moon" (two) as right. Words are
read as a speech recognizer writes them: "forty-eight", "cuarenta y ocho", "ciento veinte",
"two thousand and five", "veintiún". Two values that cannot form one number are two numbers
("nineteen sixty" is 19 and 60). The articles "a", "un", "una" and the word "one" are WEAK: a
number only when the line has no other one ("Mars has one moon"), or before a multiplier ("a
hundred", "un millón").
"""

import re
import unicodedata

_EN_UNITS = {
    "zero": 0, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_EN_MULT = {"hundred": 100, "thousand": 1000, "million": 1000000}
_EN_WEAK = {"a": 1, "an": 1, "one": 1}
_EN_JOIN = {"and"}

_ES_UNITS = {
    "cero": 0, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
    "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiuno": 21, "veintiun": 21,
    "veintiuna": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
    "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29, "treinta": 30,
    "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80,
    "noventa": 90, "cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200,
    "trescientos": 300, "trescientas": 300, "cuatrocientos": 400, "cuatrocientas": 400,
    "quinientos": 500, "quinientas": 500, "seiscientos": 600, "seiscientas": 600,
    "setecientos": 700, "setecientas": 700, "ochocientos": 800, "ochocientas": 800,
    "novecientos": 900, "novecientas": 900,
}
_ES_MULT = {"mil": 1000, "millon": 1000000, "millones": 1000000}
_ES_WEAK = {"un": 1, "una": 1}
_ES_JOIN = {"y"}

_VOCAB = {
    "English": (_EN_UNITS, _EN_MULT, _EN_WEAK, _EN_JOIN, True),
    "Spanish": (_ES_UNITS, _ES_MULT, _ES_WEAK, _ES_JOIN, False),
}

_TOKEN = re.compile(r"\d+(?:[.,]\d+)*|[^\W\d_]+")


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _digits(token: str) -> float:
    """'48' -> 48; '3,5' / '3.5' -> 3.5; '1.000' / '1,000' (groups of three) -> 1000."""
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", token):
        return float(re.sub(r"[.,]", "", token))
    return float(token.replace(",", "."))


def _fits(current: int, value: int) -> bool:
    """Whether a value under 100 can extend the number being read: "forty" + "eight", "ciento"
    + "veinte", but not "nineteen" + "sixty" or "dos" + "tres"."""
    if current == 0:
        return True
    low = current % 100
    if value >= 100:
        return low == 0 and current % 1000 < value
    if low == 0:
        return True
    return low % 10 == 0 and low >= 20 and value < 10


def spoken_numbers(text: str, language: str = "English") -> tuple[list[float], list[float]]:
    """The numbers of a line: (strong, weak). The weak ones are the lone articles and "one"."""
    units, mult, weak_words, join, hundred_mult = _VOCAB.get(language, _VOCAB["English"])
    strong: list[float] = []
    weak: list[float] = []
    current, total, reading = 0, 0, False

    def flush():
        nonlocal current, total, reading
        if reading:
            strong.append(float(total + current))
        current, total, reading = 0, 0, False

    tokens = _TOKEN.findall(_fold(text).replace("-", " "))
    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok[0].isdigit():
            flush()
            strong.append(_digits(tok))
        elif tok in weak_words:
            if nxt in mult or (hundred_mult and nxt == "hundred"):
                flush()
                current, reading = 1, True  # "a hundred", "un millón"
            elif reading and _fits(current, 1):
                current += 1  # "twenty one", "treinta y un"
            else:
                flush()
                weak.append(1.0)
        elif tok in units:
            value = units[tok]
            if reading and not _fits(current, value):
                flush()
            current += value
            reading = True
        elif tok in mult or (hundred_mult and tok == "hundred"):
            if tok == "hundred":
                current = max(current, 1) * 100
            else:
                total += max(current, 1) * mult[tok]
                current = 0
            reading = True
        elif tok in join and reading and (nxt in units or nxt in weak_words):
            continue
        else:
            flush()
    flush()
    return strong, weak


def grade_number(question: str, expected: str, given: str, language: str = "English"):
    """True or False when an answer whose expected value is a number can be graded without the
    LLM, else None (the LLM decides). The expected answer must be one strong number. The
    numbers that the question itself says are not part of the answer ("two days" in "how many
    hours are there in two days?"); the answer is graded when exactly one other number remains:
    "forty" is wrong for 48, "Mars has one moon" is wrong for 2. Several candidates ("forty-eight
    or fifty"), or none ("I don't know"), are left to the LLM."""
    exp_strong, _ = spoken_numbers(expected, language)
    if len(set(exp_strong)) != 1:
        return None
    target = exp_strong[0]
    q_strong, q_weak = spoken_numbers(question, language)
    echoed = set(q_strong) | set(q_weak)
    strong, weak = spoken_numbers(given, language)
    values = {v for v in strong if v not in echoed}
    if not values and not strong:
        values = {v for v in weak if v not in echoed}
    if len(values) != 1:
        return None
    return abs(values.pop() - target) < 1e-9
