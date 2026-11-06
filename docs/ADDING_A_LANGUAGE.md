# Adding a language

The interaction system speaks English and Spanish. The method itself does not depend on the
language: the code decides what the conversation does, and the model answers narrow questions in
the language of the conversation. Everything that does depend on the language lives in one
file per language, so adding a language means writing that file, choosing its voice and
checking it. This page lists every step, then what the evaluation needs if the new language is
to be evaluated too.

| Step | Needed | What |
|---|---|---|
| [1. The language file](#1-the-language-file) | Yes | `config/languages/<language>.yaml` of `interaction_intelligence`: phrases, commands, words, speech settings |
| [2. The language name](#2-the-language-name) | Yes | What the parameter `language` and the launch argument `language` say |
| [3. Speech](#3-speech) | Yes, with voice | The recognition language and a Piper (or MMS-TTS) voice, in the same file |
| [4. The prompts](#4-the-prompts) | Check | One English set for every language; the address rule may need a description of the language's formality |
| [5. The GUI](#5-the-gui) | No | It offers the languages with a file by itself |
| [6. The evaluation](#6-the-evaluation) | Only to evaluate the language | Scenarios, datasets, personas, panels |
| [7. Checking it](#7-checking-it) | Yes | The tests, then a conversation |

## 1. The language file

The files are in `interaction_system_core/interaction_intelligence/config/languages/`
(`english.yaml`, `spanish.yaml`), installed with the package. Copy `english.yaml` to
`<language>.yaml` (e.g. `french.yaml`) and translate it; its comments say what each key is for.
Every file has exactly the same keys as `english.yaml`, the reference: the loader refuses a
file with a key missing or unknown, an empty value, or a phrase whose `{placeholders}` differ from
the English one, and so does the test `test/test_languages.py`.

| Key | Type | What it is | Used by |
|---|---|---|---|
| `name` | text | The name of the language (`French`): the value of `language`, and what the prompts say (`{language}`) | Everything |
| `code` | text | ISO 639-1 code (`fr`): the launch argument `language:=fr` | `main.launch.py` |
| `aliases` | list | Other names accepted, in any case (`français`); may be empty | The loader |
| `speech` | mapping | `stt_language`, `piper_voice`, `piper_speaker`, `mms_voice` ([3. Speech](#3-speech)) | `main.launch.py` |
| `cancel_commands` | list | The exact stop commands ([below](#exact-stop-commands)) | The spoken cancellation |
| `yes_words`, `no_words` | lists | Yes and no, lowercase; only fallbacks ([below](#words-shared-across-languages)) | The dialogue frame; the spoken cancellation |
| `asr_noise_phrases` | list | Filler words and recurrent hallucinations of the recognizer ("thanks for watching") that are never a turn ([below](#words-shared-across-languages)) | The transcriptions |
| `ack_phrases` | list | Acknowledgments said after slot filling takes a value ("Noted.") | Slot filling |
| `filler_phrases` | list | Conversational fillers said while the reply is prepared, in the order of the ladder: five, one per step (2, 5, 9 and 14 s) and the one that repeats ("Hmm…", "One moment…"…) | [Fillers](behaviors/fillers.md) |
| `phrases` | mapping | Every fixed phrase: the fallback of each generated line, the silence prompts, the closings of the endings, the fixed parts of the dialogue behaviors ("Step {n} of {total}: {step}") | Every dialogue behavior and mechanism |

### Fixed phrases

The fixed phrases are said when the LLM fails, or are never generated at all (the closing lines,
the silence prompts), so they must read well on their own. Keep each
`{placeholder}`; the system fills it (`{points}`, `{expected}`, `{total}`, `{n}`, `{step}`,
`{options}`, `{scores}`, `{criterion}`, `{score}`). `questionnaire_or` is the word that joins the
last two options ("a, b or c"). Address the user as the prompts do: directly and informally.
`roleplay_intro` tells the user how to end the practice. Prefer a word that is not an exact stop
command ("finish", «terminar»): such a word ends the practice with its evaluation at once, while a
stop command first goes through the confirmation question. Both end with the evaluation of what
was done ([Role play](behaviors/roleplay.md)).

### Exact stop commands

The exact stop commands (`cancel_commands`) are the only requests to stop decided without the
LLM ([Spoken cancellation](behaviors/cancellation.md)), so they work when the LLM is down. Only
the whole utterance counts, after normalization (case, accents and punctuation removed,
whitespace collapsed), or one command repeated ("Stop, stop!"); anything else goes to the LLM.
List only words that are never an answer on their own in a conversation: not "no" (the answer to
a yes/no question), and not words that answer common questions (Spanish leaves out "alto" and
"salir" for this reason). A false detection costs a question (the confirmation always follows),
a missing command only the fast path (the LLM still decides). The exact commands of the
conversation's language are the only ones active.

### Words shared across languages

The yes/no words are only fallbacks, like the fixed phrases: the LLM decides whenever it answers.
They are used when a yes/no value comes back as text (a last-resort parsing fallback of the slot
values), and when the LLM fails to read the answer to "do you want to stop?": the whole answer,
normalized, must then be exactly one of these words of the conversation's language
([Spoken cancellation](behaviors/cancellation.md)).

A transcription made only of noise phrases (one or more) is dropped; one inside real words is
kept. List only what is never a turn on its own: not a goodbye like "see you next time", and not
an answer like "next week".

The yes/no words and the noise phrases of every file are used in every conversation: the yes/no
words read the LLM's output, which may use either language, and the recognizer can hallucinate in
a language other than the one it was set to. A word cannot be yes in one file and no in another
(the loader refuses it).

### What is not in the file

The prompts are not in the file ([4. The prompts](#4-the-prompts)), and neither are the operator's
overrides: `ack_phrases`, `filler_phrases`, `timeout_prompt` and `phrases.*` of the intelligence
(the GUI's Strategies page writes `phrases.*`) replace the file's values for one deployment
([CONFIGURATION.md](CONFIGURATION.md#interaction_intelligence)).

### Installing the file

With `--symlink-install`, a file added to the sources is found without installing it again (the
loader also looks next to the package's sources); otherwise build `interaction_intelligence`
again. `language` can also be the path of a language file outside the package.

## 2. The language name

The parameter `language` of the intelligence (`app_params.yaml`) and the launch argument
`language` of `main.launch.py` take the file's `name`, `code`, an alias or the file name, in any
case: `language: French`, `language:=fr`. The prompts receive the `name`. A language without a
file stops the intelligence and the launch at once, with the list of languages: there is no
fallback to English, because the fixed phrases would then be said in a language other than that
of the generated lines and the user's stop commands would not be recognized, which only a session
with a user would reveal.

## 3. Speech

`main.launch.py` starts simple_hri's nodes with the `speech` settings of the file
([CONFIGURATION.md](CONFIGURATION.md#simple_hris-speech-nodes)):

| Setting | simple_hri parameter | Choose |
|---|---|---|
| `stt_language` | `language` of the speech-to-text, and `lang_code` of `tts_piper` | The Whisper code of the language (`fr`) |
| `piper_voice` | `voice` of `tts_piper` | A voice of the Piper catalog (rhasspy/piper-voices on Hugging Face) for the language, e.g. `fr_FR-siwis-medium`; simple_hri downloads it the first time |
| `piper_speaker` | `speaker` of `tts_piper` | The speaker of a voice with several (its `.onnx.json`); `-1`: the voice's own |
| `mms_voice` | `lang_code` of `tts_mms` | The ISO 639-3 code of the MMS-TTS voice (`fra`), for `tts:=mms` |

Listen to the voice before choosing it: a question must sound like a question. The test panel and
`real_sessions.py check speak --voice` of the evaluation try a voice and its settings without
changing anything ([TEST_PANEL.md](../evaluation/doc/TEST_PANEL.md#voice-and-filler-delay)). A
robot with its own speech (`speech:=none`) must listen and speak in the language itself
([SPEECH.md](SPEECH.md#using-your-robots-own-speech)).

## 4. The prompts

The prompt templates (`config/prompts.yaml`, [PROMPTS.md](PROMPTS.md)) are one set, written in
English, for every language: each asks for the reply in `{language}`, and the understanding
questions return labels and JSON, which do not depend on the language. Nothing has to be
translated. Two places name a language:

- The shared address rule (`address_rule`) asks for the informal second person, described
  without examples (quoted examples were copied literally into every language), and the
  role-play templates give Spanish examples of gender-neutral wording. For a language with
  another system of formality, describe it there rather than quoting a word of it.
- The cancel detection (`detect_cancel_intent`) lists the English stop words and asks the model
  for their equivalents in `{language}`.

Changing the prompts changes what the evaluation measured, so phase 1 of the model choice
(`run_e0a.py phase-a`) is run again for the chosen model
([EXPERIMENTS.md](../evaluation/doc/EXPERIMENTS.md)).

## 5. The GUI

The web GUI asks the engine for its languages: the language selector of Configuration > Dialogue
lists every language with a file, and the Strategies page shows and edits the fixed phrases of the
selected language (its defaults are the file's). Nothing to change. Only the hint of the TTS tab
names the voices of English and Spanish.

## 6. The evaluation

Only needed to evaluate the system in the new language; the system runs without any of it. The
pieces that depend on the language, by folder of `evaluation/` (the code identifies a language by
its code, `en`, `es`, and the experiment files by its name):

| Piece | Where | What a new language needs |
|---|---|---|
| Scenarios | `experiments/scenarios/*_es.yaml` (the Spanish versions: same ids, same events, translated text and values) | A translated file per scenario set to run (e.g. `e4_fr.yaml`); events that rely on an exact stop command use one of the new language |
| Experiments | `experiments/*.yaml`, key `language:` (the name) | Experiment files that point to those scenarios |
| Personas | `experiments/personas.yaml` | Nothing: the descriptions are in English and the simulated user speaks the experiment's language |
| Phase 1 datasets of the model choice | `offline/datasets/phase_a/*.json` (each case has `language`: `en`/`es`), `offline/datasets/wording.json` | At least 25 cases per dataset in the new language, traps included; the code in `offline/core/phase_a.py` (`LANGUAGES`) and `offline/core/dataset_check.py` (the list of languages) |
| Wording checks | `offline/benchmarks/bench_wording.py` (per-language tables: plurals, the formal register) | Its tables for the language |
| Scoring | `analysis/key_facts.py` (stop words, yes/no, times), `analysis/strategy_checks.py` (number words), `analysis/score_runs.py` (articles) | The words of the language |
| Simulated speech | `simulation/interaction_sim/interaction_sim/asr_noise.py` (English sound-alike confusions and fillers) | A table of the language, if the noise experiment runs in it |
| Simulated user | `simulation/interaction_sim/interaction_sim/llm_user.py` (its fallback lines when its model fails are English) | Lines in the language |
| LLM-led baseline | `ablations/interaction_ablations/interaction_ablations/skills.py` (its fallback reply is English) | A line in the language, if the baseline runs in it |
| Test panel (the Tests tab of the control panel) | `real/campaigns.py` (`VOICES`), `scripts/test_panel.py` (sample question, tester card, ordinals), `scripts/test_panel_examples.yaml`, `scripts/sim_checks.py` (happy-path lines), `scripts/control_panel.py` | Their entries for the language |
| Real sessions | `real/sessions_es.yaml` (all the text the participants read), `real/make_cards.py` | A sessions file in the language |
| Review panel | `panels/review_panel.py` (the languages of the sheets) | The code of the language |
| Model choice | `scripts/run_e0a.py`, `scripts/run_e0b.py` (files per language) | Entries for the language |

## 7. Checking it

```sh
pixi run test     # the unit tests: test_languages.py checks every language file
```

Then, in a prepared terminal ([INDEX.md](../INDEX.md)):

```sh
# the whole stack in the language: the launch refuses a language without a file
ros2 launch interaction_system main.launch.py language:=fr
# conversations through the running system, with a scripted LLM (no model, no audio)
ROS_DOMAIN_ID=77 python -m pytest src/interaction_system/evaluation/simulation/interaction_sim/test
```

`test_spanish_model_failures.py` runs the system in Spanish with the model failing: the Spanish
fixed phrases are said and an exact Spanish stop command cancels without the model. A copy with
the new language checks the same for it. Finally, hold a conversation of each dialogue behavior
in the language and listen to the fixed phrases (stop the model to hear them: the fallbacks, the
closings).
