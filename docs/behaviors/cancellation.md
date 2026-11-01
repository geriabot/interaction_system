# Spoken cancellation

The user can stop a conversation at any moment by saying so ("stop", "cancel", "forget it").
The robot asks for confirmation first, so that an accidental "stop" does not lose the work, and
only a yes stops: any other answer resumes the conversation where it was, and never reaches the
strategy as if it answered the strategy's own question. Finishing is not cancelling: a user who says they have
nothing more to ask or add ("that will be all", "no more questions") has reached the normal end
of the task, not abandoned it.

Shared by every strategy: subtree `CancelCheck` of `common.xml`, skill `cancel_check`
(`interaction_intelligence/skills/common.py`). [Index](README.md).

## How it works

Every user turn goes first to `CancelCheck`, before the strategy:

1. **Detection.** A local check first: an utterance that is exactly one of the exact stop
   commands of the conversation's language, or one of them repeated, is a cancel request without
   asking the LLM; any other utterance goes to the LLM (`detect_cancel_intent`, one short
   question per turn), which chooses among three answers: **cancel** (a request to abandon the
   task), **finished** (the user says they have finished or have nothing more to ask or add: the
   normal end of the task) and **none**. Only "cancel" is a cancel request; "finished" and "none"
   go to the strategy like any other turn (in grounded question answering, its own closing). Why
   three (decided by the author, 2026-10-31): with only "cancel or not", Qwen 3 8B took "No, I
   think that's all" for a cancel request in 11 of 13 closings of grounded question answering,
   although the question said it was not; to "Do you want to stop?" the user answered "No, I
   have no more questions", read as "no", and the conversation looped to the turn limit. A
   negative rule had been ignored; an answer of its own for that case is easier to follow. The
   commands are in the language file (`cancel_commands`,
   [ADDING_A_LANGUAGE.md](../ADDING_A_LANGUAGE.md)), and only the whole utterance counts, after
   normalisation (case, accents and punctuation removed, whitespace collapsed):

   | Language | Exact stop commands | Examples that are not (the LLM decides) |
   |---|---|---|
   | English | "stop", "exit", "cancel", "quit", "abort" | "cancel please", "the hospital stop" |
   | Spanish | "para", "para ya", "para por favor", "por favor para", "basta", "basta ya", "ya basta", "cancela", "cancelar", "cancélalo", "cancela por favor", "déjalo", "déjalo ya", "dejémoslo", "detente" | "Para mí un café.", "No, para el martes.", "Para llevar.", "No." |

   So "Para.", "¡Para, para!" and "Déjalo." stop, also when the model is down. Left out on purpose
   in Spanish: "no" (an answer), "alto" and "salir" (answers to a question), "olvídalo" (may drop
   only the last value) and "terminar" (the way the role play asks to end the practice, which is
   evaluated: [role play](roleplay.md)). Until 2026-10-30 the list was English only, so with the
   model down a Spanish user could not stop the conversation (found by the injector of model
   failures). A request to
   stop has too many forms ("para, no quiero seguir", "let's drop this") for a word list: until
   2026-10-29 an utterance without a cancel-like English word was taken as no request without
   asking the LLM, which missed most requests and every one in Spanish. While slot filling asks for a slot with allowed
   values (*strict* mode, `DialogueMode.strict_cancel`), cancel-like words are ordinary answers
   ("the hospital stop", "para llevar"), so the LLM is told to accept only an explicit request to
   stop the whole conversation. Until 2026-10-30 strict mode did not ask the LLM at all: only an
   exact English command cancelled, so a Spanish user could not stop while choosing an option
   ("Para, déjalo") and neither could "Wait, stop".
2. **Confirmation.** The robot asks whether the user wants to stop (a generated short yes/no
   question, `ask_cancel_confirmation`, in which "yes" means stopping; it does not explain how to
   answer it, and it addresses the user directly and informally, like every spoken line:
   [PROMPTS.md](../PROMPTS.md#placeholders-context-and-caching); fallback "Are you sure you want
   to cancel?") and remembers what it had asked before (its last utterance).
3. **The answer to the confirmation** (`classify_cancel_reply`):

| Answer | Label | The robot | The conversation |
|---|---|---|---|
| Yes | `confirmed` | A brief generated closing (`cancel_confirmed_response`; fallback "Alright, cancelled.") | Ends: outcome `cancelled`, reason `Cancelled by the user` |
| No | `denied` | "OK, let's continue." (`cancel_no`) and what it had asked before | Goes on |
| Finished: they have nothing more to ask or add ("No, I have no more questions"), in a strategy whose end the user decides (grounded question answering) | `finished` | The strategy's own closing (`DialogueMode.on_finished`; grounded QA: "Alright. Have a good day!") | Ends normally: the strategy's own outcome (`completed`), not a cancellation |
| Finished, in a strategy whose end the user does not decide | `denied` | As for no | Goes on |
| Anything else ("No, I want to finish the boat", the value it had asked for, "can you repeat?") | `denied` | As for no | Goes on: the robot asks its question again (a value said there is said once more) |

Only a yes stops, and every other answer resumes (decided by the author, 2026-11-01). Until then
an answer that was not a yes or a no went to the strategy "so that the turn is not lost"; in the
sixth debugging round "No, I want the plumber to come" was read as "something else" (the model
took the words after the "no" as content: Mistral in 29 of its 65 confirmation questions, Llama
in 98, also in earlier rounds) and the strategy recorded it as a value or as skipping the item.
Rewording the prompt helped every model but not enough in Spanish; with this rule what the model
answers matters only as yes or not yes. The cost: a value said in answer to "do you want to
stop?" is asked again, one turn more.

When the user did not ask to stop, the label is `none` and the strategy handles the turn.

After a denial the robot resumes with its last utterance without the "OK, let's continue." of an
earlier denial: until 2026-10-31 each denial added one more ("OK, let's continue. OK, let's
continue. Is there anything else...?"; found in 22 dialogues of the second debugging round, with
every model and almost every strategy).

The model reads the answer to the confirmation. Only when it gives no usable answer (an error,
no answer in time, a call not made because the reply's budget is spent, an answer that is not
yes, no, finished or other), a **fallback** reads it: the whole answer, normalised as the stop commands, is
exactly one of the language's `yes_words` ("Yes.", "Sí.", "Vale.") → yes; exactly one of its
`no_words` ("No.") → no; anything else → something else, as before ("Yes, but first the size"
does not stop: the robot resumes). The word lists are not the method: they are a fallback, like the
fixed phrases said when the model fails (decided by the author, 2026-10-30;
`utils.local_yes_no`). Before, a failed reading always withdrew the cancellation, so while the
model was down nobody could stop the conversation.

**With the model known to be down, an exact stop command ends the conversation at once**
(decided by the author, 2026-10-30). "Down": the last call to the model in this conversation
failed (an error, no answer in time, a call not made because the budget was spent, an
understanding answer that is not JSON, a generated line too short to say) and none has answered
since. Then an utterance that is exactly a stop command of the language (step 1) gets no
confirmation question: the robot says the fixed cancel closing of the language (`cancel_ok`,
"Alright, cancelled.") and the outcome is `cancelled` by the user (label `confirmed`, so the
measures count it as a confirmed cancellation without a question, as the `no_cancel_confirmation`
ablation's). Why: the confirmation exists to avoid stopping by mistake; an exact command is
unambiguous, and with the model down the answer to the question could not be read anyway. While
the model works nothing changes (an exact command gets the question, read by the model); the
fallback above covers a model that fails during the confirmation. Where it is tracked: the LLM
client records whether its last call gave a usable answer (`model_status`), and the intelligence
keeps it in the state of the dialogue (`model_down`), the JSON object the tree sends with every
request, because the intelligence keeps no state between requests and builds a client per
request; a request without a call to the model leaves it as it was.

**A strategy's own closing.** When a strategy has something to give for what was done, a
confirmed cancellation (either way) closes with it instead of the generic closing
(`DialogueMode.on_cancel`): the role play gives the evaluation of the exchanges so far when the
user said at least one line of the scene ([Role play](roleplay.md)). The outcome stays
`cancelled`; what the strategy recorded (the scores) is in `final_frame_json`.

The requester can also cancel the goal of `/dialog/execute_task`: the conversation stops at once,
without a closing line, outcome `cancelled`, reason `Cancelled`.

**What the words are about comes first (decided by the author, 2026-11-01).** The detector is
given the task description (not the robot's last line) and first decides whether the words are
about this conversation (stopping it, leaving it) or about something of the task (an answer, a
value, a step done), even when a word such as "cancel" appears: "I need to cancel my card" in a bank
task and "I've pressed the cancel button" in a procedure are about the task, and words about the
task are never a cancel request (enforced by the code, whatever the rest of the answer says): they
go to the strategy without a question. The former rule "universal markers, always cancel"
contradicted this and is gone. A hesitant wish to drop the conversation ("maybe I should just
forget about it") is "cancel": the robot confirms it anyway. When the user denies a cancellation,
the robot resumes with what it had asked, and the same words are not asked about again (a safety
net for a false alarm the detector did not catch). Passing the turn that triggered the question to
the strategy was tried and dropped: a retracted real request ("forget it", then "no") would reach
the strategy and could be read as "skip this item".

## Tree

![CancelCheck](../../media/bt/common/CancelCheck.svg)

## Eventualities

| Situation | What happens |
|---|---|
| The LLM fails while detecting the request | No cancellation: the strategy handles the turn. An exact stop command of the language is decided without the LLM: it asks for confirmation (with the fixed phrase), or, when the model was already down before that turn, ends the conversation at once |
| The model is known to be down (its last call failed) and the user says an exact stop command | No question: the fixed cancel closing (`cancel_ok`), outcome `cancelled` (above) |
| The LLM fails while classifying the answer to the confirmation | The fallback reads it: exactly a yes word of the language confirms, exactly a no word resumes, anything else is "something else" (nothing stops: the robot resumes). Until 2026-10-30 every failed reading withdrew it, so while the model stayed down a cancellation could not be confirmed |
| The user says a cancel-like word while answering a slot with allowed values | Strict mode: the LLM takes only an explicit request to stop as one; an answer that names an option is not |
| The intelligence does not answer | A failure of the system: outcome `error` ([Endings](endings.md)) |

Ablations of this mechanism (detection by keywords only or by the LLM only, no confirmation) are
in `evaluation/ablations` ([evaluation/README.md](../../evaluation/README.md#ablations)).
