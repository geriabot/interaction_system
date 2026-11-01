# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The language files (config/languages/*.yaml): every file has exactly the keys of the
reference (english.yaml) with non-empty values, the loader finds a language by its name, code,
alias or file name in any case and refuses an unknown language or a malformed file, and the
resources of the files reach the system (fixed phrases, yes/no words, noise phrases)."""

import os
import shutil

import pytest
import yaml

from interaction_intelligence import languages
from interaction_intelligence.dialog_frame import DialogFrame
from interaction_intelligence.languages import (
    KEYS, SPEECH_KEYS, LanguageFileError, UnknownLanguageError, available_languages,
    canonical_language, language_files, load_language)
from interaction_intelligence.llm_client import LLMDialogClient, default_phrases
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.utils import clean_user_input, local_cancel_check

_DIR = os.path.join(os.path.dirname(__file__), "..", "config", "languages")


def _raw(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _files():
    return sorted(os.path.join(_DIR, f) for f in os.listdir(_DIR) if f.endswith(".yaml"))


def test_english_and_spanish_ship_with_the_system():
    assert {"English", "Spanish"} <= {lang.name for lang in available_languages()}
    assert {"english", "spanish"} <= set(language_files())


@pytest.mark.parametrize("path", _files(), ids=os.path.basename)
def test_every_file_has_exactly_the_keys_of_the_reference(path):
    reference = _raw(os.path.join(_DIR, "english.yaml"))
    data = _raw(path)
    assert set(data) == set(reference) == KEYS
    assert set(data["phrases"]) == set(reference["phrases"])
    assert set(data["speech"]) == set(reference["speech"]) == set(SPEECH_KEYS)


@pytest.mark.parametrize("path", _files(), ids=os.path.basename)
def test_every_value_is_filled(path):
    data = _raw(path)
    for key in ("name", "code"):
        assert isinstance(data[key], str) and data[key].strip()
    for key, value in data["phrases"].items():
        assert isinstance(value, str) and value.strip(), key
    for key in ("cancel_commands", "yes_words", "no_words", "asr_noise_phrases", "ack_phrases",
                "filler_phrases"):
        assert data[key] and all(isinstance(s, str) and s.strip() for s in data[key]), key
    for key, kind in SPEECH_KEYS.items():
        assert isinstance(data["speech"][key], kind), key
    # Several fillers, so that none is said twice in a row; one line each
    assert len(data["filler_phrases"]) >= 2
    assert all("\n" not in p for p in data["filler_phrases"])


@pytest.mark.parametrize("path", _files(), ids=os.path.basename)
def test_every_file_loads_with_the_placeholders_of_english(path):
    lang = load_language(path)
    english = load_language("English")
    for key, text in lang.phrases.items():
        assert languages._placeholders(text) == languages._placeholders(english.phrases[key]), key


@pytest.mark.parametrize("path", _files(), ids=os.path.basename)
def test_no_yes_or_no_word_is_a_stop_command(path):
    # "no" answers the confirmation; it never stops the dialogue by itself
    lang = load_language(path)
    for word in lang.yes_words + lang.no_words:
        assert local_cancel_check(word, lang.name) is None, word


def test_names_and_selection():
    names = {lang.name for lang in available_languages()}
    assert len(names) == len(available_languages())  # one file per language
    for name in ("Spanish", "spanish", " SPANISH ", "es", "ES", "español", "castellano"):
        assert load_language(name).name == "Spanish", name
    for name in ("English", "english", "en", "inglés"):
        assert load_language(name).name == "English", name
    assert canonical_language("es") == "Spanish"
    assert canonical_language("Catalan") == "Catalan"  # no file: returned as it is


def test_an_unknown_language_is_an_error():
    with pytest.raises(UnknownLanguageError) as err:
        load_language("Klingon")
    assert "Klingon" in str(err.value) and "English" in str(err.value)
    assert "ADDING_A_LANGUAGE" in str(err.value)
    # The client and the phrase helpers refuse it too: no silent English fallback
    with pytest.raises(UnknownLanguageError):
        default_phrases("Klingon")
    with pytest.raises(UnknownLanguageError):
        LLMDialogClient("nlu", "nlg", lambda *a: None, PromptBuilder({}), language="Klingon")


def _broken(tmp_path, change):
    data = _raw(os.path.join(_DIR, "spanish.yaml"))
    change(data)
    path = tmp_path / "broken.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("change", [
    lambda d: d.pop("cancel_commands"),
    lambda d: d["phrases"].pop("close_error"),
    lambda d: d["phrases"].update({"extra_phrase": "x"}),
    lambda d: d["phrases"].update({"quiz_wrong": "No, era otra."}),  # {expected} lost
    lambda d: d["phrases"].update({"opening": ""}),
    lambda d: d.update({"filler_phrases": []}),
    lambda d: d["speech"].pop("piper_voice"),
    lambda d: d["speech"].update({"piper_speaker": "uno"}),
    lambda d: d.update({"cancel_commands": ["!"]}),
], ids=["no-cancel-commands", "phrase-missing", "phrase-extra", "placeholder-lost",
        "empty-phrase", "no-fillers", "speech-key-missing", "speaker-not-int",
        "punctuation-command"])
def test_a_malformed_file_is_refused(tmp_path, change):
    with pytest.raises(LanguageFileError):
        load_language(_broken(tmp_path, change))


def test_a_new_language_file_is_found(tmp_path, monkeypatch):
    # A copy of spanish.yaml as another language, in a directory of language files
    for f in _files():
        shutil.copy(f, tmp_path)
    data = _raw(os.path.join(_DIR, "spanish.yaml"))
    data.update({"name": "Testish", "code": "tt", "aliases": []})
    (tmp_path / "testish.yaml").write_text(yaml.safe_dump(data, allow_unicode=True),
                                           encoding="utf-8")
    monkeypatch.setattr(languages, "language_dirs", lambda: [tmp_path])
    languages.bool_words.cache_clear()
    try:
        assert load_language("tt").name == "Testish"
        assert "Testish" in languages.language_names()
    finally:
        languages.bool_words.cache_clear()


def test_the_client_says_the_phrases_of_its_language():
    client = LLMDialogClient("nlu", "nlg", lambda *a: None, PromptBuilder({}), language="es")
    assert client.language == "Spanish"
    assert client.phrase("cancel_ask") == load_language("Spanish").phrases["cancel_ask"]
    # Operator overrides on top of the language file
    client = LLMDialogClient("nlu", "nlg", lambda *a: None, PromptBuilder({}),
                             language="Spanish", phrase_overrides={"cancel_ask": "¿Paramos?"})
    assert client.phrase("cancel_ask") == "¿Paramos?"
    assert client.phrase("cancel_ok") == load_language("Spanish").phrases["cancel_ok"]


def test_yes_no_words_of_every_language_parse_a_bool():
    for text in ("yes", "Sí", "si", "vale", "OK", "true", "1"):
        assert DialogFrame._normalize(text, "bool") is True, text
    for text in ("no", "No", "nope", "false", "0"):
        assert DialogFrame._normalize(text, "bool") is False, text
    assert DialogFrame._normalize("maybe", "bool") is None


def test_noise_phrases_of_every_language_are_discarded():
    assert clean_user_input("Thanks for watching!") is None
    assert clean_user_input("Gracias por ver.") is None
    assert clean_user_input("Suscríbete") is None
    assert clean_user_input("Quiero un café") == "Quiero un café"
