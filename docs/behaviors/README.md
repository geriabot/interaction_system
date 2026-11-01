# The dialogue behaviors and their shared mechanisms

One page per dialogue behavior (strategy) and per mechanism shared by all of them. Each page says
what the behavior or mechanism does, what it needs, its Behavior Tree, how it understands each
user turn, what it does in every eventuality (silence, a request to repeat, a turn not
understood, a cancellation, a failure of the LLM or of speech, a limit reached) and what it
returns. The questions to the LLM are in [PROMPTS.md](../PROMPTS.md), and adding a behavior in
[ADDING_DIALOGUE_BEHAVIORS.md](../ADDING_DIALOGUE_BEHAVIORS.md).

| Strategies | Shared mechanisms |
|---|---|
| [Slot filling](slot_filling.md) (`slot_filling`) | [Silence](silence.md): waiting for the user without giving up too early |
| [Explanation](explanation.md) (`explanation`) | [Spoken cancellation](cancellation.md): "stop, forget it", confirmed |
| [Quiz](quiz.md) (`quiz`) | [Recovery and communication breakdown](no_progress.md): bounded repairs |
| [Procedure](procedure.md) (`procedure`) | [Endings and outcome](endings.md): every conversation closes with a line and a reason |
| [Role play](roleplay.md) (`roleplay`) | [Barge-in](barge_in.md) *(experimental, not validated)*: the user interrupts the robot, which stops and listens |
| [Grounded question answering](grounded_qa.md) (`grounded_qa`) | [Conversational fillers](fillers.md): "Hmm…, let me think" when the reply takes more than two seconds |
| [Standardized questionnaire](questionnaire.md) (`questionnaire`) | |

## What every behavior shares

Every behavior follows the same skeleton of turns
([ARCHITECTURE.md](../ARCHITECTURE.md#the-action-layer-the-dialogue-behaviors)): the tree of the
strategy decides what follows each turn, the LLM only answers narrow questions (understand an
utterance, phrase a reply), and every limit is a counter in the tree. Every conversation ends with
a closing line and an **outcome** with its reason, returned in the `DialogTask` result
([Endings and outcome](endings.md)).

## How to read the figures

The figures are drawn from the XML of the trees (`interaction_system_behaviors/behavior_tree_xml`)
by `interaction_system_behaviors/scripts/bt_diagrams.py`, with Graphviz. Run it after changing a
tree, so that the figures stay in sync with the trees:

```bash
python3 interaction_system_behaviors/scripts/bt_diagrams.py   # writes media/bt/*.svg
```

| Shape | Node |
|---|---|
| Grey, rounded | Control: `→` Sequence (runs its children in order, fails at the first that fails), `?` Fallback (tries its children in order until one succeeds), `↻` Repeat (until a child fails), if / then / else, and switch (the branch whose value matches the variable; *otherwise* when none does) |
| Blue, rounded | A question to the interaction intelligence (`AskIntelligence`), named by its skill; it updates the state of the dialogue and writes the reply to say and a label |
| Purple, rounded | Speech: `Speak` (with barge-in), `Listen`, and `WithFillers` (says the conversational fillers while its child prepares the reply) |
| Yellow | A condition on the blackboard (`if ...`), or `fail` |
| White | A blackboard update (`Script`): counters, `done` |
| Green, bold | A subtree: those of the behavior's own file are expanded below it (with their parameters); the shared ones of `common.xml` are a single box, `shared: ...`, drawn in the mechanism's document |

All the shared subtrees side by side:

![The shared subtrees of common.xml](../../media/bt/common.svg)
