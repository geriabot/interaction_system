# The questions to the LLM

This page describes the questions the interaction system asks the LLM: the template of each,
the skill that asks it, how its answer is handled, and how to change them. Every question is
built from a template of `interaction_system_core/interaction_intelligence/config/prompts.yaml`
(parameters `prompts.*` of `/interaction_intelligence`) by `PromptBuilder`, asked by
`LLMDialogClient` and answered by the interaction system's interpretation server, which builds
the same prompt with `PromptBuilder` from the arguments that the client sends
([ARCHITECTURE.md](ARCHITECTURE.md#the-interpretation-server)). The trees never write prompts: they ask the
intelligence a skill, and the skill asks the questions below
([ARCHITECTURE.md](ARCHITECTURE.md#the-interaction-intelligence)). Which model answers each
question is in [MODELS.md](MODELS.md).

## Understanding and generation

- **Understanding** questions are classifications or extractions. They are asked with a JSON
  schema (enforced by the provider; with Ollama, at temperature 0, so the same
  utterance always gets the same answer), are parsed tolerantly, validated (the slot exists, the
  value has the slot's type, is allowed and within range), retried once on an unusable answer, and
  fall back to a safe answer.
- **Generation** questions ask for one utterance. They are asked without a schema (at the model's
  temperature), are retried when the answer is too short, and fall back to a fixed phrase of the
  conversation's language (the `phrases` of its language file,
  [ADDING_A_LANGUAGE.md](ADDING_A_LANGUAGE.md); editable in the GUI's Strategies page, or as
  `phrases.*` parameters).

No example in a template comes from a scenario of the evaluation
(`evaluation/experiments/scenarios/`), so that no scenario is answered from an example: the
examples use situations that no scenario uses. Check a new example, or a new scenario, against the
other side ([evaluation/doc/EXPERIMENTS.md](../evaluation/doc/EXPERIMENTS.md#rules-for-every-campaign)).

## The templates and the skills that ask them

| Template | Kind | Asked by |
|---|---|---|
| `choose_dialogue_behavior` | Understanding | The choice of the dialogue behavior, among the offered ones ([ARCHITECTURE.md](ARCHITECTURE.md#the-choice-of-the-dialogue-behavior)) |
| `classify_task_mode` | Understanding | Not used by the system: the original classifier between slot filling and explanation, kept for the offline benchmark that compares it with the choice above |
| `create_frame`, `audit_frame` | Understanding | `slot_filling.prepare`, when the request has no schema: design the slots from the task, then check their allowed values against the resources |
| `opening`, `opening_direct`, `resume_opening` | Generation | `slot_filling.opening`, `.opening_direct` (`skip_intro`), `.resume_opening` |
| `extract_slots` | Understanding | `slot_filling.update`: what the turn does (`act`: `values`, `question`, `skip`, `repeat`, `other`) and the slots it gives or corrects, with list operations |
| `generate_response` | Generation | `slot_filling.next_question`: acknowledge, say why a value was rejected, ask for what is missing |
| `ask_confirmation` | Generation | `slot_filling.confirmation_question`: read the frame back, saying which slots are left blank (unanswered) |
| `classify_intent` | Understanding | `slot_filling.classify_confirmation`: `confirms`, `corrects` (with the corrections), `question`, `unclear` |
| `response_to_intent`, `answer_confirmation_question` | Generation | `slot_filling.confirmed`, `.correction_reply`; `.answer_question` |
| `extract_key_points` | Understanding | When the dialogue begins, if the request has no key points: list them from the task and the material, marking the critical ones |
| `explanation_opening`, `explanation_opening_direct` | Generation | `explanation.open`, `.open_direct` (end before the check: the teach-back question is the fixed phrase `teach_back`) |
| `judge_teach_back` | Understanding | `explanation.check`: what the reply is and the status of every key point in it |
| `repair_explanation`, `rephrase_explanation` | Generation | `explanation.repair`: only the points missing, incomplete or wrong (`points`), or the whole topic in another way (`all`); both end before the check (`teach_back_again`) |
| `ask_missing_points` | Generation | `explanation.ask_missing`: after a partial teach-back with progress, confirms what was right and asks only for the critical points still missing, without giving them away |
| `targeted_question` | Generation | `explanation.targeted`: the fallback, a direct question whose answer is the most critical point not yet verified, without giving the answer away |
| `answer_explanation_question`, `understanding_confirmed` | Generation | `explanation.answer` (then the last check again), `.confirmed`. The closing after the last failed check (`.give_up`) uses fixed phrases and the critical points themselves |
| `quiz_opening`, `quiz_next_question`, `quiz_rephrase_question`, `quiz_summary` | Generation | `quiz.open`, `.next_question`, `.repeat` (the question in simpler words, from the second time; never revealing the answer), `.summary` |
| `evaluate_quiz_answer` | Understanding | `quiz.evaluate`: whether the reply is an answer, whether it is correct, and short feedback |
| `procedure_opening`, `procedure_step` | Generation | `procedure.open`; `procedure_step` for every presentation of a step (first, next, back), from that step only, without the context. `procedure.repeat` uses fixed phrases and no LLM |
| `classify_step_turn` | Understanding | `procedure.classify`: `done`, `question`, `problem`, `repeat`, `back`, `skip`, `unclear` |
| `procedure_answer_question`, `procedure_help_step`, `procedure_remind`, `procedure_summary` | Generation | `procedure.answer`, `.help`, `.remind`, `.advance` (after the last step) |
| `roleplay_intro`, `roleplay_reply`, `roleplay_coach` | Generation | `roleplay.open`; the character's lines (`.open`, `.open_direct`, `.reply`, and `.repeat` from the second request to repeat); `.coach` |
| `classify_roleplay_turn` | Understanding | `roleplay.classify`: `continue`, `repeat`, `coach`, `end`, `scene_end` (no valid answer: `unclear`, a request to repeat) |
| `evaluate_roleplay`, `roleplay_feedback` | Understanding | `roleplay.finish`: one question per criterion (score 1 to 5, the numbers of the user's lines as evidence), then the closing, the highlight and the advice |
| `qa_opening` | Generation | `grounded_qa.open`: the greeting and the invitation to ask, with no facts |
| `answer_from_material` | Understanding | `grounded_qa.answer`: what the person does, what a request is and, if it is answerable, the answer and the sentences that support it |
| `verify_finished` | Understanding | `grounded_qa.answer`: before a closing, whether the person said they want nothing more |
| `verify_answer` | Understanding | `grounded_qa.answer`: whether every claim of the drafted answer is supported by the material; an answer that is not is replaced by the abstention |
| `map_scale_answer` | Understanding | `questionnaire.record`: the option the answer clearly chooses, or `ambiguous`, `meaning`, `refuses`, `repeat`, `other`. The items, the options, the clarifications and the closing are read without the LLM |
| `detect_cancel_intent`, `classify_cancel_reply` | Understanding | `cancel_check`: the cancel request, and the answer to the confirmation |
| `ask_cancel_confirmation`, `cancel_confirmed_response` | Generation | `cancel_check`: a short yes/no question whether the user wants to stop, and the closing after a confirmed cancellation |

Notes on some of the templates:

- `extract_slots`: the act `skip` means that the user cannot or will not give the value asked.
- `generate_response`: the acknowledgment phrase itself is added by the code. Only when the slot
  it asks for is a list (`list_str`) does the code add `list_slot_rule`, which says that several
  values may be given and invites more items.
- `judge_teach_back`: the reply is `restates`, `question`, `declines`, `not_understood` or
  `other`. Each key point is `correct`, `incomplete` (with the part said; handled as missing,
  never as wrong), `wrong` (with what was said) or `missing`. The schema requires one entry per
  key point, because small models renumbered a shorter list.
- `repair_explanation`: the points are reworded and address the person; the part of an
  incomplete point that was said is confirmed, never corrected.
- `evaluate_quiz_answer`: `kind` is `answer`, or `repeat` when the user asks to repeat or
  clarify, or did not hear or understand the question; "I don't know" is an answer. If the LLM
  fails every retry, the reply is right only when it equals the expected answer, after
  normalization.
- `roleplay_reply`: a line that repeats an earlier question is asked again once.
- `answer_from_material`: the LLM first says what the person does (`act`: `asks`, `finished` or
  `reacts`). Only `asks` can be answered, and the code enforces it: `finished` becomes `done` and
  `reacts` becomes `comment`. It then says what a request is (`answerable`, `not_in_material`,
  `off_task`, `repeat`, `other`) and, if it is answerable, gives the answer with only facts of the
  material. A check that repeats the last answer back is a reaction only when its value matches;
  with another value the person has misunderstood, and the turn is a request to repeat, so that
  the robot gives the answer again instead of letting the mistake pass (models of three families
  answered a wrong check with only the invitation to ask more). Its limit: a wrong check followed
  by a comment ("Seven o'clock? Oh dear, that's late." after 17:00) is still read as a reaction by
  the models tried, whatever the wording; the plain check is corrected.
- `verify_answer`: an immediate inference (17:00 is in the afternoon) and an answer with only the
  part asked for count as supported.
- `detect_cancel_intent` is asked for every utterance that is not exactly a stop command of the
  conversation's language (`cancel_commands` of its language file), and is given the task
  description. The LLM first says what the words are about (`conversation` or `task`); words
  about the task, such as "cancel my card", are never a cancel request, and the code enforces it.
  It then chooses `cancel`, `finished` (nothing more to ask or add: the normal end, not a cancel
  request) or `none`.
- `classify_cancel_reply` reads the answer to the confirmation: `yes`, `no`, `finished` (a normal
  end, where the strategy lets the user end it) or `other` (the answer does not say whether to
  stop). Only `yes` stops; `no` and `other` both resume.
- `ask_cancel_confirmation`: "yes" means stopping, as `classify_cancel_reply` reads it, and the
  question does not explain how to answer it. After a `no`, the robot says the fixed phrase
  `cancel_no` and repeats its last line, without the `cancel_no` of an earlier denial.

The skills `begin`, `silence`, `closing`, `result`, `quiz.repeat` (the first time: the question
itself), `quiz.unanswered` and `finish`, and every operation on the frame, ask no question: the
prompts while the user is silent, the closing lines of the endings, the acknowledgment of a slot
left blank and of a question left unanswered are fixed phrases of the conversation's language
(`timeout`, `silence_ready`, `close_user_silent`, `close_breakdown`, `close_turn_limit`,
`close_error`, `cancel_no`, `slot_skipped`, `quiz_unanswered`; editable in the GUI's Strategies
page or as `phrases.*`), so that they never depend on the LLM.

## Placeholders, context and caching

Templates use Python `str.format` placeholders (`{task}`, `{user_text}`, `{history}`...). A literal
brace must be doubled (`{{`, `}}`). `{language}` (the parameter `language`) is available in every
template, and so is `{address}`. The node validates every template at startup and refuses to start
with a malformed one.

Two entries of `prompts.yaml` are not questions but shared rules that `PromptBuilder` inserts
into the questions, so that what does not depend on the LLM is decided by the code:

- `address_rule` fills `{address}`: how every spoken line addresses the user. The person the robot
  talks to is the user of the task (the customer, the patient, the student...), so the line speaks
  to them directly, never about them in the third person or by their role, and in the informal
  register (the familiar form of address where the language has one; English is unaffected). The
  rule describes the register and quotes no phrase or word: with quoted examples, models copied
  them literally (a question for the user's name in tasks that ask for none, a Spanish pronoun in
  English lines), and a test keeps it free of quotes. Every template that writes a line the user hears has it among its rules (the
  openings, the next question, the read-back and the closings, the quiz feedback, the procedure,
  the explanation, the grounded answers, the role play, the cancellation question and its
  closing). It is the same text in every turn, so it is part of the cached prefix. The LLM-led
  baseline of the ablations gets the same rule.
- `list_slot_rule` is appended to `generate_response` only when the slot asked for is a list,
  because small models applied a list instruction to single-value slots too ("you can tell me
  several options" when asking for a name).

The offline benchmark `wording` measures these properties of the spoken lines, such as the slot
name echoed in the third person ("what is the customer's name?") or a mixed register
([evaluation/offline/README.md](../evaluation/offline/README.md)).

Templates that use the context get the `[CONTEXT]` block after their fixed instructions: the
domain, the resources that are not hidden (a role play's scene is hidden), and the user's emotion
only when `user_state_service` is set (off by default) and the emotion is notable (not neutral,
confident enough and recent). Each template puts its fixed instructions first and the data of the
turn last, so that the leading part is the same across turns: providers that cache
prompt prefixes (Ollama, OpenAI) reuse it. Keep
new templates in that order.

## Changing the questions

- Edit `prompts.yaml` and restart the intelligence (no rebuild with `--symlink-install`), or
  pass another file: `interaction_system.launch.py prompts_file:=/path/to/prompts.yaml`.
- Override some templates with `prompts.<name>` parameters under `interaction_intelligence` in
  your copy of `app_params.yaml` (launch argument `params_file`, applied after `prompts_file`):
  only those change.
- In the GUI, the Prompts page shows the defaults (read from the installed `prompts.yaml`) and
  sends only the valid, changed templates as parameters when it starts the stack (the shared rules
  are listed separately, under "Shared rules").
- The language of the conversation is the parameter `language` (or `main.launch.py
  language:=es`): the templates ask for replies in that language, and the fixed phrases follow it.
  The templates are written in English for every language: `{language}` is the name of the
  language file (`English`, `Spanish`). The shared address rule names the informal register
  without examples; the role-play templates give Spanish examples of gender-neutral wording. A
  language with another system of formality or gender may need its own description there. That is a change of the prompts, after
  which phase 1 of the model choice is run again
  ([ADDING_A_LANGUAGE.md](ADDING_A_LANGUAGE.md#4-the-prompts)).

The prompts and the raw answers are not stored. The skills asked, their labels and their times are
published on `/interaction/skill_trace`, and the conversation's transcript is written to
`conversation_log_path`.
