# Barge-in

> **Status: experimental (not validated).** Implemented and usable, but not part of the
> evaluation of the system: its behavior with people (false cuts, missed interruptions) has not
> been measured. With the pixi environment it cannot run yet (no camera driver, and the
> perception's dependencies are not installed): future work, with the steps to enable it in
> [REAL_SESSIONS.md](../../evaluation/doc/REAL_SESSIONS.md#barge-in).

**The user can interrupt the robot.** When the person starts talking while the robot is speaking,
the robot stops and listens: what the person says is the next turn of the conversation.

Shared by every strategy: the `Speak` node of the trees (`interaction_system_behaviors`), which
says every utterance of the robot. It is optional and visual: it needs a signal that the user is
speaking while the robot speaks, such as the lip activity of the perception of the user
(`interaction_perception`, a camera, processed live, no images stored). [Index](README.md).

## How it works

| The robot is speaking and... | What happens |
|---|---|
| `/user_speaking` turns true (the user started talking) | `Speak` cancels the utterance at once, publishes `/barge_in` (the utterance and how long it had been spoken, in ms) and the tree goes on to `Listen`: what the user says is the next turn |
| Nothing publishes `/user_speaking` (no perception of the user) | The robot finishes every utterance, then listens |

The barge-in is visual, not acoustic: the speech capability does not listen while the robot
speaks, since it would hear the robot itself (an acoustic barge-in needs echo cancellation or a
headset). So, without the perception of the user, the person has to wait until the robot ends its
utterance.

## Eventualities

| Situation | What happens |
|---|---|
| The lip activity flickers while the robot speaks (the user smiles, nods) | The utterance is cut; the next `Listen` waits for a real utterance as usual (a silence is handled by [Silence](silence.md)) |
| The camera or the detector stops | `/user_speaking` stays false: the robot finishes its utterances, nothing else changes |
| The conversation is cancelled while the robot speaks | Halting the tree cancels the utterance ([Spoken cancellation](cancellation.md), [Endings](endings.md)) |

## Parameters

The topic `Speak` reads is `user_speaking_topic` of the orchestrator (empty: no barge-in), and the
lip activity detector publishes on its `speaking_topic`; both are `/user_speaking` by default
([CONFIGURATION.md](../CONFIGURATION.md#interaction_orchestrator)). The topics and the `BargeIn`
message are in [ROS_INTERFACES.md](../ROS_INTERFACES.md#the-public-interface).
