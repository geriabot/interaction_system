# The speech capability

This page explains how the interaction system listens and speaks, and how to replace the speech
capability with the robot's own. The interaction system does not record or play sound itself: its
trees ask a speech capability to listen (`Listen`) and to speak (`Speak`), as any robot behavior
uses a capability. The capability is [simple_hri](https://github.com/rodperex/simple_hri), a
separate package fetched by `thirdparty.repos`, which the trees use through its actions:

| | simple_hri |
|---|---|
| Interface | Actions, used on demand: `/stt_action` (`Listen`) and `/tts_action` (`Say`); the transcriptions are also published on `/listened_text` |
| Speech-to-text | `faster_whisper` (faster-whisper with Silero voice activity detection, `stt_faster_whisper`; the default) or `whisper` (Whisper with WebRTC voice activity detection, `stt_whisper`) |
| Text-to-speech | `piper` (Piper voices, `tts_piper`; the default) or `mms` (MMS-TTS, `tts_mms`); the utterance is synthesized, then played |
| Barge-in, experimental (not validated) | Optional and visual only ([Barge-in](behaviors/barge_in.md)) |
| Audio devices | The computer running the nodes; simple_hri can forward the synthesized audio to another machine (its "audio forwarding") |
| Signals for the embodiment | `/is_speaking`, published by the interaction system's `Speak` and `WithFillers` nodes, not by simple_hri |

A `Listen` goal waits for the user, records until silence and transcribes; a `Say` goal finishes
when the playback really ends. Both speech-to-text engines listen only during a request. The
robot does not listen while it speaks, so barge-in relies on `/user_speaking`, the lip activity
from user perception when it runs; without it, the robot finishes each utterance. Because
`/is_speaking` comes from the interaction system's nodes, the embodiment does not depend on the
speech package.

`main.launch.py` starts simple_hri's speech-to-text and text-to-speech nodes with the language of
the conversation, the engines, the Whisper model and the microphone given by its arguments
`language`, `stt`, `tts`, `whisper_model` and `input_device`
([LAUNCH_ARGUMENTS.md](LAUNCH_ARGUMENTS.md#the-whole-stack-interaction_system-mainlaunchpy)).
[CONFIGURATION.md](CONFIGURATION.md#simple_hris-speech-nodes) lists the parameters it gives each
node, including the voice of each language. The recognition language and the voices of a
language are the `speech` settings of its language file
([ADDING_A_LANGUAGE.md](ADDING_A_LANGUAGE.md)), so using another Piper voice for a language means
changing that file. To use other simple_hri backends or options (a cloud service, the audio
forwarding), start simple_hri with its own `simple_hri.launch.py` and the interaction system with
`main.launch.py speech:=none` ([LAUNCH_ARGUMENTS.md](LAUNCH_ARGUMENTS.md#simple_hri)).
[INSTALLATION.md](INSTALLATION.md#models) explains where the speech models are downloaded.

## How the trees use it

- `Listen` waits for the user's next utterance, at most `wait_timeout` seconds. It sends
  `/stt_action` goals until an utterance comes (a transcription that is only noise does not count,
  and the node listens again). The transcription goes to the interaction intelligence, which drops
  the noise (Whisper hallucinations such as subtitle credits, fillers) and publishes the rest on
  `/interaction/user_utterance`, where `Listen` takes the first one after it started. When
  `wait_timeout` passes with no utterance, the tree handles the silence: it prompts that the robot
  is waiting, and says a closing line after `max_silence` seconds ([silence](behaviors/silence.md)).
- `Speak` says a text with `/tts_action`, at most `tts_timeout` seconds. It publishes the text
  on `/robot_utterance` first, and `/is_speaking` (latched `std_msgs/Bool`) is true while the robot
  speaks, for the embodiment (gestures, lights). If the user starts speaking while the robot speaks
  (`/user_speaking` true), it cancels the utterance and publishes `/barge_in`; what the user says is
  the next turn. While a reply is prepared, `WithFillers` says the conversational fillers with the
  same action, publishing each on `/robot_filler` instead
  ([Conversational fillers](behaviors/fillers.md)). A speech capability that is not available,
  or an utterance that does not end in time, does not stop the conversation: the node goes on
  with a warning.
- Halting a tree (a canceled conversation) cancels the utterance and the listening.

Text typed instead of spoken (the GUI's composer, a test) is published on `/transcription`, which
the intelligence also listens to (`transcription_topics`).

## Using your robot's own speech

Start the interaction system without speech nodes (`main.launch.py speech:=none`, or
or `interaction_system.launch.py`) and let your robot serve simple_hri's interface:
the actions `/stt_action` (`simple_hri_interfaces/action/Listen`) and `/tts_action`
(`simple_hri_interfaces/action/Say`), and the transcriptions on a topic of the intelligence's
`transcription_topics` (`/listened_text` by default).

The simulation of the evaluation does exactly this: `interaction_sim`'s harness serves simple_hri's
actions and plays the user ([evaluation/README.md](../evaluation/README.md)).

## Testing the speech alone

```sh
ros2 action send_goal /tts_action simple_hri_interfaces/action/Say "{text: 'Hello, I am a robot'}"
ros2 action send_goal /stt_action simple_hri_interfaces/action/Listen "{max_wait: 10.0}"   # then speak
```
