# Copyright 2026 Álvaro Quintana Camacho
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Stateless helpers shared across the dialog package.

  - clean_user_input:   ASR validation and noise filtering.
  - normalize_text:     locale-independent text normalisation.
  - local_cancel_check: fast-path cancel-intent detection (no LLM).
  - local_yes_no:       the fallback reading of a yes/no answer when the model fails.
  - parse_loose_json:   tolerant JSON parsing for noisy LLM outputs.
"""

import json
import re
import unicodedata
from typing import Any

# Minimum number of recognisable words required to forward ASR output to the
# dialog system. One word is enough ("yes", "no", "tomorrow", ...).
_MIN_WORDS = 1

# Pure filler or hallucinated transcripts ("thanks for watching", "eh"): a transcript that is
# ONLY noise (one or more noise phrases, nothing else, after normalisation) is silently
# discarded; a noise phrase inside a real utterance ("Eh, para.", "No olvides la leche") never
# discards it (decided by the author, 2026-10-30). Short affirmations like "ok" are NOT noise:
# they are legitimate confirmations. The phrases are in the language files
# (config/languages/*.yaml, asr_noise_phrases), those of every language at once
# (languages.noise_pattern).

# The exact stop commands are in the language files too (cancel_commands): each dialogue uses
# those of its language (local_cancel_check). Any other request to stop is decided by the LLM
# (detect_cancel_intent).


def remove_accents(text: str) -> str:
    """Strip diacritics for locale-independent matching."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_text(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    text = remove_accents(text.lower().strip())
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_user_input(text: str) -> str | None:
    """
    Validate raw ASR output before passing it to the dialog system.

    Returns the original string if valid, or None if the input is empty, pure
    noise (the whole utterance is noise phrases: "Eh.", "Thanks for watching!"),
    or below the minimum word count. An utterance with a noise phrase inside
    real words ("Eh, para.") is kept unchanged. The original (non-normalised)
    string is returned so downstream NLU still sees accents and casing.
    """
    text = text.strip()
    if not text:
        return None

    normalized = normalize_text(text)
    if not normalized:
        return None

    from .languages import noise_pattern

    if noise_pattern().fullmatch(normalized):
        return None

    words = [w for w in normalized.split() if re.search(r"[a-z0-9]", w)]
    if len(words) < _MIN_WORDS:
        return None

    return text


def local_cancel_check(text: str, language: str = "English") -> bool | None:
    """
    Fast local cancel detection: only an utterance that is exactly a stop command
    of the dialogue's language (its language file, cancel_commands) is decided
    here, without the LLM.

    The utterance is normalised (case, accents and punctuation removed,
    whitespace collapsed) and must be one command, possibly repeated: "¡Para!",
    "Para, para." and "Stop, stop!" are commands; "Para mí un café", "No, para
    el martes" and "cancel please" are not (the LLM decides them).

    Returns:
        True   → definite cancel (an exact command).
        None   → anything else: the LLM decides. A request to stop has too many
                 forms ("para, no quiero seguir", "let's drop this") for a word
                 list: deciding "no cancel" from the absence of a keyword missed
                 most of them, and every one in Spanish.

    Strict mode (the user is answering a closed-choice question) does not change
    this check: it changes the question to the LLM, which then accepts only an
    explicit request to stop. Deciding "no cancel" locally in strict mode made a
    closed-choice slot impossible to cancel in any form but an exact English
    command ("Para, déjalo" or "Wait, stop" never cancelled).

    Detection order is deliberate: an exact cancel command wins over any other
    interpretation, also in strict mode.

    language: the dialogue's language (name, code or alias); an unknown one
    raises languages.UnknownLanguageError.
    """
    from .languages import load_language

    commands = load_language(language).cancel_commands
    normalized = normalize_text(text)
    if normalized in commands:
        return True
    # One command said several times ("para para", "stop stop stop")
    for command in commands:
        n = len(command.split())
        words = normalized.split()
        if len(words) > n and len(words) % n == 0 and all(
                " ".join(words[i:i + n]) == command for i in range(0, len(words), n)):
            return True
    return None


def local_yes_no(text: str, language: str = "English") -> str | None:
    """
    The fallback reading of a yes/no answer (the reply to "do you want to stop?")
    when the model that reads it failed: "yes" when the whole utterance,
    normalised as the cancel commands, is exactly one of the language's
    yes_words ("Sí.", "Yes!"), "no" when it is exactly one of its no_words,
    None otherwise ("Yes, but first...", "I think so": the caller's own safe
    default). The model decides the reply whenever it answers: the word lists
    are not the method, only a fallback, like the fixed phrases said when the
    model fails (decided by the author, 2026-10-30).
    """
    from .languages import load_language

    lang = load_language(language)
    normalized = normalize_text(text)
    if not normalized:
        return None
    if normalized in {normalize_text(w) for w in lang.yes_words}:
        return "yes"
    if normalized in {normalize_text(w) for w in lang.no_words}:
        return "no"
    return None


def local_step_done(text: str, language: str = "English") -> bool:
    """
    Whether the whole utterance, normalised as the cancel commands, is exactly one
    of the language's step_done_words ("Done.", "Ready", "Hecho."): a report that
    the step is done, never a request to stop (DialogueMode.done_words_are_answers).
    """
    from .languages import load_language

    normalized = normalize_text(text)
    return bool(normalized) and normalized in load_language(language).step_done_words


def local_starts_with_yes(text: str, language: str = "English") -> bool:
    """
    Whether a reply opens with a yes: its first clause (up to the first comma, full stop,
    exclamation or semicolon), normalised, is one of the language's yes_words ("Yes, that's
    correct.", "Sí, es eso."). "Yes thank you" without a pause, or "Okay let's stop", is not.
    """
    from .languages import load_language

    first = normalize_text(re.split(r"[,.!;]", text or "", maxsplit=1)[0])
    return bool(first) and first in {normalize_text(w) for w in load_language(language).yes_words}


def local_names_answer(text: str, answer: str, language: str = "English") -> bool:
    """
    Whether a reply names the whole expected answer of a quiz question, word for word ("A young
    to is all a tadpole." for "A tadpole"; leading trailing_words of the answer, such as an
    article, are not required), with none of the language's negation_words or hedge_words in it
    and no negative contraction ("not a tadpole", "a frog or a tadpole", "I wouldn't say a
    tadpole"). A recognizer that garbles the words around the answer leaves the answer itself
    intact, while the model, given only this line, may read the garbled words as a wrong answer.
    The check depends on these word lists: it only silences the rule (the model's grade stands),
    so a word listed that does not deny the answer costs nothing but the rule's help.
    """
    from .languages import load_language

    lang = load_language(language)
    expected = normalize_text(answer).split()
    while expected and expected[0] in lang.trailing_words:
        expected = expected[1:]
    said = normalize_text(text).split()
    if not expected or not said:
        return False
    line = f" {' '.join(said)} "
    blockers = set(lang.negation_words) | {normalize_text(w) for w in lang.hedge_words}
    if any(b and f" {b} " in line for b in blockers) or re.search(r"\w+n['\u2019]t\b", text or ""):
        return False
    n = len(expected)
    return any(said[i:i + n] == expected for i in range(len(said) - n + 1))


def cancel_intent_of(answer: dict) -> bool:
    """Whether the model's reading of an utterance (detect_cancel_intent) is a cancel
    request: {"request": "cancel"} when "about" is not "task"; "finished" (the normal end of the
    task) and "none" are not. The former boolean form {"cancel": true} is still read."""
    if not isinstance(answer, dict):
        return False
    if answer.get("about") == "task":
        # Words about the task (an answer, a step done, "cancel my card"), not about this
        # conversation: never a cancel request, whatever "request" says (author, 2026-11-01)
        return False
    if "request" in answer:
        return answer.get("request") == "cancel"
    return answer.get("cancel", False) is True


def stop_request_of(answer: dict) -> str:
    """The model's reading of an utterance (detect_cancel_intent) as "cancel", "finished" or
    "none", with the rule of cancel_intent_of: words about the task are never a request; the
    former boolean form {"cancel": true} is read as "cancel"."""
    if not isinstance(answer, dict) or answer.get("about") == "task":
        return "none"
    if "request" in answer:
        request = answer.get("request")
        return request if request in ("cancel", "finished") else "none"
    return "cancel" if answer.get("cancel", False) is True else "none"


def strip_leading(text: str, phrase: str) -> str:
    """text without any leading repetitions of phrase (compared without case or surrounding
    spaces): "OK, let's continue. OK, let's continue. Anything else?" -> "Anything else?"."""
    text, phrase = (text or "").strip(), (phrase or "").strip()
    while phrase and text.lower().startswith(phrase.lower()):
        text = text[len(phrase):].strip()
    return text


# English function words that are not words of the other languages of the system: a line said
# in another language with several of them, making up a fair share of its words, is in English
_ENGLISH_FUNCTION_WORDS = frozenset(
    "the you your is are and to of it that said but can this with for what was have be "
    "didn't don't you're it's isn't only".split())


def in_english_by_mistake(text: str, language: str) -> bool:
    """Whether a line meant to be said in language (not English) came out in English: at least
    three English function words, a quarter of its words or more. The prompts are written in
    English, and a small model asked for another language sometimes answers in English (E3
    simulated twin, Spanish: the explanation said again after a wrong teach-back, "You said you
    can take a game for two weeks, but...")."""
    if not text or (language or "English").lower().startswith("english"):
        return False
    words = re.findall(r"[^\W\d_]+(?:['’][^\W\d_]+)?", text.lower().replace("’", "'"))
    english = sum(w in _ENGLISH_FUNCTION_WORDS for w in words)
    return english >= 3 and english * 4 >= len(words)


def spoken_ask(text: str) -> str:
    """What a spoken line asks for: its question sentence, or its last sentence
    when it asks without a question mark ("...but first tell me X.")."""
    end = text.find("?")
    if end < 0:
        sentences = [s for s in re.split(r"[.!;]", text) if s.strip()]
        return sentences[-1].strip(" ¿¡").lower() if sentences else ""
    start = max(text.rfind(c, 0, end) for c in ".!;:") + 1
    return text[start : end + 1].strip(" ¿¡").lower()


def to_snake_case(name: str) -> str:
    """Normalise a slot name to snake_case.
    Handles spaces, hyphens, CamelCase and non-alphanumeric characters."""
    name = re.sub(r"[\s\-]+", "_", name.strip())
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    name = name.lower()
    name = re.sub(r"[^\w]", "", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "slot"


def parse_loose_json(raw: Any) -> Any:
    """
    Tolerant JSON parser used for LLM outputs.

    Strips markdown fences, locates the outermost container (object or array),
    removes trailing commas, then delegates to json.loads. Returns whatever
    json.loads produces (dict or list). Raises ValueError on failure.
    """
    if isinstance(raw, (dict, list)):
        return raw

    text = re.sub(r"```json|```", "", str(raw)).strip()

    # Find the outermost container — prefer whichever appears first.
    obj_start = text.find("{")
    arr_start = text.find("[")
    candidates = [s for s in (obj_start, arr_start) if s != -1]
    if not candidates:
        raise ValueError("No JSON object or array found in payload")
    start = min(candidates)

    if text[start] == "{":
        end_char = "}"
    else:
        end_char = "]"
    end = text.rfind(end_char)
    if end == -1 or end < start:
        raise ValueError(f"Unterminated JSON {end_char}")

    candidate = re.sub(r",\s*([\]}])", r"\1", text[start : end + 1])
    return json.loads(candidate)
