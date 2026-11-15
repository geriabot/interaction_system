# Adding a dialogue behavior

This page explains how to add a dialogue behavior (a conversation strategy) without changing the
existing code. A dialogue behavior is a Behavior Tree that holds the control (its phases, counters
and limits), the skills of the interaction intelligence that it asks (the narrow questions to the
LLM and the operations on its data), and an entry in the catalog. The skills are loaded from your own Python
module, the tree from your own package, and the catalog and the runtime are configuration files
([CONFIGURATION.md](CONFIGURATION.md#configuration-files)).

The example below adds `survey`: the robot asks a list of yes/no questions and records the
answers.

## 1. The skills

A Python module (in your own ament_python package) registers the dialogue behavior's mode and its
skills:

```python
# my_survey/skills.py
from interaction_intelligence.skills import (
    DialogueMode, SkillEnv, SkillInput, SkillResult, register_mode, skill,
)
from interaction_intelligence.utils import parse_loose_json


def _init(env: SkillEnv, state: dict) -> None:
    """Called by `begin`: prepare the state from the request; raise ValueError if it lacks
    what the dialogue behavior needs."""
    for resource in state["resources"]:
        items = parse_loose_json(resource["content"])
        if isinstance(items, list) and items:
            state.update({"questions": [str(q) for q in items], "idx": 0, "answers": []})
            return
    raise ValueError("The survey requires a resource with a JSON list of questions.")


def _outcome(s: dict) -> tuple[str, str]:
    """How the survey ended by itself: "completed", "partial" (with the reason) or "failed"."""
    if not s.get("completed"):
        return "failed", "The survey did not reach its end"
    return "completed", ""


register_mode("survey", DialogueMode(
    init=_init,
    phase=lambda s: "done" if s.get("completed") else f"question_{s['idx'] + 1}",
    data=lambda s: {"answers": s["answers"]},
    succeeded=lambda s: bool(s.get("completed")) and not s.get("user_cancelled"),
    outcome=_outcome,
))


@skill("survey.ask")
def ask(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    return SkillResult(reply=state["questions"][state["idx"]])


ANSWER = {"type": "object", "required": ["answer"],
          "properties": {"answer": {"type": "string", "enum": ["yes", "no", "unclear"]}}}


@skill("survey.record")
def record(env: SkillEnv, state: dict, inp: SkillInput) -> SkillResult:
    """Understand the answer and move on. label: "next", "last", or "unclear" (neither yes nor
    no: nothing recorded, the tree asks again)."""
    question = state["questions"][state["idx"]]
    prompt = (f'The robot asked: "{question}". The user answered: "{inp.user_text}". '
              'Output JSON {"answer": "yes" | "no" | "unclear"}.')
    raw = env.llm._call_nlu(prompt, ANSWER, max_tokens=20)  # understanding: schema, temperature 0
    try:
        answer = parse_loose_json(raw).get("answer") if raw else "unclear"
    except ValueError:
        answer = "unclear"  # the deterministic fallback
    if answer not in ("yes", "no"):
        return SkillResult(label="unclear")
    state["answers"].append({"question": question, "yes": answer == "yes"})
    state["idx"] += 1
    if state["idx"] >= len(state["questions"]):
        state["completed"] = True
        return SkillResult(reply="Thank you, that is all.", label="last")
    return SkillResult(label="next")
```

The contract of a skill: `skill(env, state, inp) -> SkillResult`.

- `state` is the state of the conversation (a JSON-serializable dict). Modify it in place: it
  goes back to the tree's blackboard. `begin` fills it with `task`, `mode`, `domain`, `resources`,
  `skip_intro`, `max_turns`, `wait_timeout` and the flags `completed`, `user_cancelled`,
  `awaiting_cancel`, then calls your mode's `init`.
- `inp` carries `user_text`, `option` (a word), `number` (an int) and `args` (a dict), as the tree
  passes them.
- `env.llm` is the conversation's `LLMDialogClient` (its question methods, and `_call_nlu(prompt,
  schema, max_tokens)` / `_call_nlg(prompt, max_tokens)` for your own prompts), `env.ctx` the
  context of the conversation (domain, resources, the user's emotion, when turned on).
- Return the reply to say (if any) and a label for the tree to branch on. Raise `ValueError` with
  the reason when the skill cannot run: the tree fails with it.
- Ask narrow questions with a JSON schema for understanding, and keep the decisions in the tree.
  A turn that is not understood should come back as its own label (here `unclear`), so that the
  tree counts it as a turn without progress.
- `env.llm.phrase(key)` gives a fixed phrase of the conversation's language (with the operator's
  overrides), for lines that must not depend on the LLM.

The `DialogueMode` tells the system:

| Field | What |
|---|---|
| `init` | Prepare the state from the request (called by `begin`) |
| `phase`, `data` | The phase and the data of the conversation (the feedback and the result) |
| `succeeded` | Whether it succeeded (used when there is no `outcome`) |
| `outcome` | `(outcome, reason)` when the dialogue behavior itself ended the conversation: `completed`, `partial` (an element left unfinished; the reason says which) or `failed` |
| `strict_cancel` | Whether a cancel request must be explicit (e.g. while the user answers a closed-choice question) |
| `cancel_stage` | One sentence for the cancel detector on where the behavior is (slot filling: nothing has been booked, ordered or sent yet), so that dropping what the conversation is preparing ("cancel the booking") is read as a request to stop; empty by default |
| `expected_answers` | The answers the robot expects now (the options of the slot or item asked for, the quiz answer): a reply whose every word is in one of them, or that names a whole answer of two or more words in a sentence, is that answer, never a request to stop, and the LLM is not asked; empty by default |
| `yes_is_an_answer` | Whether the question pending now is answered yes or no (slot filling's read-back of the data): a reply that opens with one of the language's `yes_words` is then that answer, and the LLM is not asked whether it is a request to stop. False by default |
| `done_words_are_answers` | Whether a reply that is exactly one of the language's `step_done_words` ("Done.", "Ready.") answers the current element and is never read as a request to stop, without asking the model (procedure). False by default |
| `finishing_is_an_answer` | Whether the user saying they have finished ("Done", "I'm done", "That's all") is a normal reply to the current element, handled by the behavior's own turn (procedure, explanation, role play, slot filling). When false and without `on_finished` (quiz, questionnaire), it is a request to stop and is asked about. False by default |
| `silence_grace` | Seconds the user may stay silent on the current element without being prompted (e.g. a procedure step that takes time; 0 by default) ([silence](behaviors/silence.md)) |
| `on_cancel` | Optional: the closing line when the user confirms a cancellation and the dialogue behavior has something to give for what was done (the role play: its evaluation); `""` for the generic closing. With its own closing the outcome is the dialogue behavior's own; with the generic one, `cancelled` |
| `on_finished` | Optional: the closing line when the user, asked whether they want to stop, says instead that they have finished (grounded question answering). It records the normal end, with the dialogue behavior's own outcome |
| `pending` | The element the user is expected to answer now, as the robot asks it (the slot's question, the quiz question, the item with its options, the step): what the robot repeats after a silence and resumes with after a denied request to stop. `""` by default: the robot's last utterance |
| `on_turn_limit` | Optional: what the dialogue behavior says for what was done when the conversation reaches `max_turns` before its own end (the quiz: the score so far; the procedure: how the steps went; the role play: its evaluation), said before the fixed closing line; `""` for the fixed line alone. The ending stays `turn_limit` |
| `stop_is_finishing` | With `on_finished`: whether a request to stop is the normal end now (grounded question answering: once a question within the task got its reply). Then a request to stop gives `on_finished`'s closing without a confirmation; otherwise it is asked about, and a confirmed stop is a cancellation. True by default |

The shared endings (`cancelled`, `user_silent`, `breakdown`, `turn_limit`, `error`) are decided by
the shared subtrees, not by `outcome` ([Endings and outcome](behaviors/endings.md)).

Load the module in the intelligence with the parameter `skill_modules` (in your copy of
`app_params.yaml`, given with the launch argument `params_file`):

```yaml
interaction_intelligence:
  ros__parameters:
    skill_modules: ["my_survey.skills"]
```

## 2. The tree

The tree includes the shared subtrees (`common.xml` of `interaction_system_behaviors`: copy it next
to your tree, or include it by its path). Its main tree calls the dialogue loop shared by every
behavior, `DialogueLoop`, and the file defines only the two parts that are the behavior's own:
`StrategyOpening` (the start of the conversation: `Begin`, then the first reply) and
`StrategyTurn` (the reply to a user turn):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<root BTCPP_format="4" main_tree_to_execute="Survey">
  <include path="common.xml"/>

  <BehaviorTree ID="Survey">
    <SubTree ID="DialogueLoop" _autoremap="true"/>
  </BehaviorTree>

  <BehaviorTree ID="StrategyOpening">
    <Sequence>
      <SubTree ID="Begin" _autoremap="true"/>
      <AskIntelligence skill="survey.ask"/>
    </Sequence>
  </BehaviorTree>

  <BehaviorTree ID="StrategyTurn">
    <Sequence>
      <AskIntelligence skill="survey.record" user_text="{user_text}"/>
      <Switch2 variable="{label}" case_1="last" case_2="unclear">
        <Script code="done := true"/>
        <!-- Not understood: ask again, or close after max_stalled turns without progress -->
        <IfThenElse>
          <SubTree ID="NoProgress" _autoremap="true"/>
          <AskIntelligence skill="survey.ask"/>
          <SubTree ID="EndDialogue" _autoremap="true" reason="breakdown"/>
        </IfThenElse>
        <AskIntelligence skill="survey.ask"/>
      </Switch2>
    </Sequence>
  </BehaviorTree>
</root>
```

`DialogueLoop` (in `common.xml`) says the opening, with fillers while it is prepared and its own
time budget, and then runs one user turn per cycle until the dialogue is done:

```
listen -> (spoken cancellation | StrategyTurn) -> count progress -> turn limit -> speak
          (a silence: UserSilent)
```

and concludes with the outcome. The loop does not start when the opening already ended the
dialogue (`done` set by `StrategyOpening`, as slot filling does when the frame cannot be designed).
Subtrees are resolved by their ID when the tree is built from its file (BehaviorTree.CPP's
`createTreeFromFile`): a tree given as text cannot resolve a relative `include`.

The loop is the same for every behavior, and with it the new behavior gets every shared
mechanism ([docs/behaviors](behaviors/README.md)):

- `Begin` builds the state and keeps the closing line for a failure of the system and the
  conversational fillers;
- `WithFillers`, around the opening and around the reply to each turn (everything that asks the
  intelligence before the next `Speak`), says a filler when the reply takes more than
  `filler_after_s` ([Conversational fillers](behaviors/fillers.md));
- `ListenToUser`, `UserSilent` and `CancelCheck` handle the silences and the spoken
  cancellation, and `TurnLimit`, after `CountProgress` and before the reply is said, the turn
  limit (on the last turn allowed its closing line replaces the reply);
- `CountProgress` after the dialogue behavior's turn, and `NoProgress` as the condition of an
  `IfThenElse` in every branch of a turn without progress (then: the recovery; else:
  `EndDialogue` with `reason="breakdown"`), give the exit on a communication breakdown;
- `Conclude` says the closing line if the conversation ended on a failure, and asks the
  outcome ([Endings and outcome](behaviors/endings.md)).

Every branch that ends the conversation by itself sets `done := true` after a reply that closes
it (here the last answer's "Thank you, that is all."), so that no conversation ends in silence.
The nodes available are `AskIntelligence`, `Speak`, `Listen` and `WithFillers` (their ports are
in the `TreeNodesModel` of `common.xml`) and the BehaviorTree.CPP built-ins. The blackboard keys the
orchestrator writes (the request, the limits, the counters) are listed in
[ARCHITECTURE.md](ARCHITECTURE.md#the-blackboard).

## 3. The runner and the catalog

Add a runner to a copy of `interaction_behaviors_config.yaml` (launch argument `config_file`):

```yaml
behaviors:
  # ... the existing ones
  - name: "interaction_survey"
    behavior_file: "behavior_tree_xml/survey.xml"
    control_period_ms: 50
    package_name: "my_survey"
```

and the behavior to a copy of `interaction_orchestrator_params.yaml` (launch argument
`catalog_file`):

```yaml
    dialogue_behaviors:
      behaviors: ["Explanation", "Quiz", "Procedure", "Roleplay", "GroundedQA", "Questionnaire",
                  "TopicChat", "SlotFilling", "Survey"]
      Survey:
        behavior_id: 8       # unique: 0 to 7 are taken
        mode: "survey"
        needs: [""]          # or a need the intelligence can check (see selection.py)
        explanation: "The robot asks the user a list of yes/no questions and records the answers."
        node_name: "interaction_survey"
```

The fields are described in [CONFIGURATION.md](CONFIGURATION.md#the-catalog-of-dialogue-behaviors).

A request with `dialog_mode: survey` runs it; without `dialog_mode`, the intelligence may choose it
from its explanation, whose first sentence should say who speaks and why, as the others do. A new kind of *need* (a resource the behavior requires) is added in
`interaction_intelligence/selection.py` (`NEEDS`, and the wording of the refusal in `MISSING`); a
need that is not there is never provided, so every request for the behavior is then refused as
`not_feasible`.

## 4. Test it

- Unit-test the skills with a fake `env.llm` (as `interaction_intelligence/test` does), without
  ROS.
- Run whole conversations through the system with the simulation harness
  (`evaluation/simulation/interaction_sim`): `DialogueHarness.run(goal, ScriptedUser([...]))`
  plays the user and the speech package; with `llm_client_factory` the intelligence can use a
  deterministic stand-in for the LLM ([evaluation/README.md](../evaluation/README.md)).
- Watch the tree in Groot2 (port 5555 of the active runner).
- Draw the tree for the documentation. This command writes one SVG per tree file (the folder of
  the trees must hold `common.xml` too):

  ```sh
  python3 interaction_system_behaviors/scripts/bt_diagrams.py --xml <your behavior_tree_xml> --out <folder>
  ```

  For a behavior of this repository, run it without arguments and add its page to
  [docs/behaviors](behaviors/README.md).
