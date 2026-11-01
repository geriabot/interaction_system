# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The languages of the interaction system: every language-dependent resource lives in one YAML
file per language (config/languages/<name>.yaml): the fixed phrases (the fallbacks of the
generated lines, the silence prompts, the closings), the acknowledgments, the fillers, the exact
stop commands, the yes/no words, the noise phrases of the recognizer and the speech settings
(recognition language, voices). How to add a language: docs/ADDING_A_LANGUAGE.md.

The `language` parameter names the file by its `name` ("Spanish"), its `code` ("es"), an alias
("español") or the file name, in any case; it can also be the path of a language file outside
the package. A language without a file is an error (UnknownLanguageError), never a silent
fallback: the fixed phrases would be said in another language than the generated lines, and
the user's exact stop commands would not be recognized, which a session would only reveal with
a user in front of the robot. English is the reference: every file must have exactly its keys,
and each phrase the same {placeholders} as English's.
"""

import functools
import os
import re
import string
from dataclasses import dataclass
from pathlib import Path

import yaml

from .utils import normalize_text

REFERENCE = "english"

# The keys of a language file and their kind: the phrases (a mapping of strings, the keys of the
# reference file), the lists of strings, the speech settings
_TEXT = ("name", "code")
_LISTS = ("aliases", "cancel_commands", "yes_words", "no_words", "asr_noise_phrases",
          "ack_phrases", "filler_phrases")
# Lists that may be empty (a language with no other name)
_MAY_BE_EMPTY = ("aliases",)
SPEECH_KEYS = {"stt_language": str, "piper_voice": str, "piper_speaker": int, "mms_voice": str}
KEYS = set(_TEXT) | set(_LISTS) | {"speech", "phrases"}


class UnknownLanguageError(ValueError):
    """A language without a language file."""


class LanguageFileError(ValueError):
    """A language file that does not follow the schema of the reference (english.yaml)."""


@dataclass(frozen=True)
class Language:
    """The resources of one language, as read from its file."""

    name: str
    code: str
    aliases: tuple
    speech: dict
    cancel_commands: frozenset  # normalised
    yes_words: tuple
    no_words: tuple
    asr_noise_phrases: tuple  # normalised
    ack_phrases: tuple
    filler_phrases: tuple
    phrases: dict
    path: str

    def names(self) -> set[str]:
        """Every name by which the language is selected (lowercase)."""
        return {n.strip().lower() for n in (self.name, self.code, Path(self.path).stem,
                                            *self.aliases) if n and n.strip()}


def language_dirs() -> list[Path]:
    """Where the language files are: the installed package's share directory, then the
    directory next to the package's sources (unit tests, a workspace without the install)."""
    dirs = []
    try:
        from ament_index_python.packages import get_package_share_directory
        dirs.append(Path(get_package_share_directory("interaction_intelligence"))
                    / "config" / "languages")
    except Exception:  # not installed, or ament_index_python not available
        pass
    dirs.append(Path(__file__).resolve().parent.parent / "config" / "languages")
    return [d for d in dirs if d.is_dir()]


def language_files() -> dict[str, Path]:
    """The language files found, by file name (lowercase stem); the first directory wins."""
    out: dict[str, Path] = {}
    for d in language_dirs():
        for path in sorted(d.glob("*.yaml")):
            out.setdefault(path.stem.lower(), path)
    return out


def _placeholders(text: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(text) if field}


def _strings(data: dict, key: str, where: str) -> tuple:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(s, str) and s.strip() for s in value):
        raise LanguageFileError(f"{where}: '{key}' must be a list of non-empty strings")
    if not value and key not in _MAY_BE_EMPTY:
        raise LanguageFileError(f"{where}: '{key}' is empty")
    return tuple(s.strip() for s in value)


@functools.lru_cache(maxsize=None)
def _read(path: str) -> Language:
    """Read and check one language file against the reference."""
    where = os.path.basename(path)
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise LanguageFileError(f"{where}: not a mapping")
    missing, extra = KEYS - set(data), set(data) - KEYS
    if missing or extra:
        raise LanguageFileError(
            f"{where}: keys missing {sorted(missing)}, unknown {sorted(extra)}")
    for key in _TEXT:
        if not isinstance(data[key], str) or not data[key].strip():
            raise LanguageFileError(f"{where}: '{key}' must be a non-empty string")
    lists = {key: _strings(data, key, where) for key in _LISTS}

    speech = data["speech"]
    if not isinstance(speech, dict) or set(speech) != set(SPEECH_KEYS):
        raise LanguageFileError(f"{where}: 'speech' must have exactly {sorted(SPEECH_KEYS)}")
    for key, kind in SPEECH_KEYS.items():
        if not isinstance(speech[key], kind) or (kind is str and not speech[key].strip()):
            raise LanguageFileError(f"{where}: speech.{key} must be a {kind.__name__}")

    phrases = data["phrases"]
    if not isinstance(phrases, dict) or not all(
            isinstance(v, str) and v.strip() for v in phrases.values()):
        raise LanguageFileError(f"{where}: 'phrases' must map each key to a non-empty string")
    reference = _reference_phrases(path)
    if reference is not None:
        missing, extra = set(reference) - set(phrases), set(phrases) - set(reference)
        if missing or extra:
            raise LanguageFileError(
                f"{where}: phrases missing {sorted(missing)}, unknown {sorted(extra)} "
                f"(the keys of {REFERENCE}.yaml)")
        wrong = sorted(k for k in reference
                       if _placeholders(phrases[k]) != _placeholders(reference[k]))
        if wrong:
            raise LanguageFileError(
                f"{where}: the {{placeholders}} of {wrong} differ from {REFERENCE}.yaml")

    commands = frozenset(normalize_text(c) for c in lists["cancel_commands"])
    if "" in commands:
        raise LanguageFileError(f"{where}: a cancel command is only punctuation")
    return Language(
        name=data["name"].strip(),
        code=data["code"].strip(),
        aliases=lists["aliases"],
        speech=dict(speech),
        cancel_commands=commands,
        yes_words=tuple(w.lower() for w in lists["yes_words"]),
        no_words=tuple(w.lower() for w in lists["no_words"]),
        asr_noise_phrases=tuple(normalize_text(p) for p in lists["asr_noise_phrases"]),
        ack_phrases=lists["ack_phrases"],
        filler_phrases=lists["filler_phrases"],
        phrases={k: v for k, v in phrases.items()},
        path=path,
    )


def _reference_phrases(path: str) -> dict | None:
    """The phrases of the reference file (None when `path` is the reference itself)."""
    ref = language_files().get(REFERENCE)
    if ref is None:
        raise LanguageFileError(f"The reference language file {REFERENCE}.yaml is not found "
                                f"(searched: {[str(d) for d in language_dirs()]})")
    if os.path.realpath(ref) == os.path.realpath(path):
        return None
    return _read(str(ref)).phrases


def available_languages() -> list[Language]:
    """Every language with a file, by name."""
    return sorted((_read(str(p)) for p in language_files().values()), key=lambda lang: lang.name)


def language_names() -> list[str]:
    """The names of the languages with a file ("English", "Spanish", ...)."""
    return [lang.name for lang in available_languages()]


def load_language(language: str) -> Language:
    """The language named (name, code, alias or file name, any case; or the path of a language
    file). UnknownLanguageError when no file has that name; LanguageFileError when the file is
    wrong."""
    wanted = (language or "").strip()
    if wanted.endswith((".yaml", ".yml")) and os.path.isfile(wanted):
        return _read(os.path.abspath(wanted))
    key = wanted.lower()
    for lang in available_languages():
        if key in lang.names():
            return lang
    raise UnknownLanguageError(
        f"No language file for the language '{language}'. Languages: "
        f"{', '.join(language_names()) or 'none'} (files in "
        f"{', '.join(str(d) for d in language_dirs())}). To add one: docs/ADDING_A_LANGUAGE.md")


def canonical_language(language: str) -> str:
    """The name of a language as its file gives it ("es" -> "Spanish"); a language without a
    file is returned as it is."""
    try:
        return load_language(language).name
    except UnknownLanguageError:
        return (language or "").strip()


@functools.lru_cache(maxsize=1)
def bool_words() -> tuple[frozenset, frozenset]:
    """(yes, no): the yes/no words of every language file. A word that is yes in one file and
    no in another is an error."""
    yes, no = set(), set()
    for lang in available_languages():
        yes.update(lang.yes_words)
        no.update(lang.no_words)
    both = yes & no
    if both:
        raise LanguageFileError(
            f"Words that are both yes and no in the language files: {sorted(both)}")
    return frozenset(yes), frozenset(no)


@functools.lru_cache(maxsize=1)
def noise_pattern() -> re.Pattern:
    """The noise phrases of every language file, as one pattern on normalised text that a whole
    utterance made only of noise matches with fullmatch: one or more phrases, separated by
    spaces ("eh", "mm hmm", "gracias por ver"). A noise phrase inside real words does not make
    the utterance noise (clean_user_input)."""
    phrases = {p for lang in available_languages() for p in lang.asr_noise_phrases if p}
    if not phrases:
        return re.compile(r"(?!x)x")  # matches nothing
    one = "(?:" + "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True)) + ")"
    return re.compile(one + r"(?: " + one + r")*")
