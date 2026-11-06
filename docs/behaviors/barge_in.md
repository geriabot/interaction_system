# Barge-in

> **Status:** experimental, not validated. Barge-in is implemented and usable, but it is not part
> of the evaluation of the system, so its behavior with people (false cuts, missed interruptions)
> has not been measured. It cannot run yet in the pixi environment, which has no camera driver and
> does not install the perception's dependencies; enabling it there is future work, and the steps
> are in [REAL_SESSIONS.md](../../evaluation/doc/REAL_SESSIONS.md#barge-in).

The user can interrupt the robot. When the person starts talking while the robot is speaking, the
robot stops and listens, and what the person says becomes the next turn of the conversation.

Shared by every behavior: the `Speak` node of the trees (`interaction_system_behaviors`), which
says every utterance of the robot. The mechanism is optional and visual. It needs a signal that
the user is speaking while the robot speaks, such as the lip activity detected by user perception
(`interaction_perception`, which processes a camera image live and stores no images).
[Index](README.md).

## How it works

| The robot is speaking and... | What happens |
|---|---|
| `/user_speaking` turns true (the user started talking) | `Speak` cancels the utterance at once, publishes `/barge_in` (the utterance and how long it had been spoken, in ms) and the tree goes on to `Listen`: what the user says is the next turn |
| Nothing publishes `/user_speaking` (no user perception) | The robot finishes every utterance, then listens |

The barge-in is visual, not acoustic: speech (simple_hri) does not listen while the robot
speaks, since it would hear the robot itself (an acoustic barge-in needs echo cancellation or a
headset). Without user perception, therefore, the person has to wait until the robot ends its
utterance.

## Edge cases

| Situation | What happens |
|---|---|
| The lip activity flickers while the robot speaks (the user smiles, nods) | The utterance is cut; the next `Listen` waits for a real utterance as usual (a silence is handled by [Silence](silence.md)) |
| The camera or the detector stops | `/user_speaking` stays false: the robot finishes its utterances, nothing else changes |
| The conversation is canceled while the robot speaks | Halting the tree cancels the utterance ([Spoken cancellation](cancellation.md), [Endings](endings.md)) |

## Parameters

The topic `Speak` reads is `user_speaking_topic` of the orchestrator (empty: no barge-in), and the
lip activity detector publishes on its `speaking_topic`; both are `/user_speaking` by default
([CONFIGURATION.md](../CONFIGURATION.md#interaction_orchestrator)). The topics and the `BargeIn`
message are in [ROS_INTERFACES.md](../ROS_INTERFACES.md#the-public-interface).
