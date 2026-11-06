# interaction_perception: user perception

This package is the perception subsystem of the interaction system: what the robot perceives,
from a camera, of the person it is talking to. It runs separately from the interaction system,
which uses it only through a topic (the lip activity, for barge-in, which is experimental) and a
service (the state of the user, off by default). It is optional: without it, the conversation only
loses the visual barge-in. A perception system that replaces these detectors only has to publish
the same topic and serve the same service.
[ARCHITECTURE.md](../../docs/ARCHITECTURE.md#speech-perception-and-embodiment) shows how it fits
in the system.

> The emotion detector is preliminary work and has not been validated. It classifies each frame
> on its own, without smoothing over time, with a third-party image classifier trained on posed
> faces, which is unreliable with real faces, light and movement. Its labels do not include
> `frustrated`, which two prompt templates react to, and a facial expression is not a reliable
> sign of an emotion. In the EU, the AI Act restricts emotion recognition (e.g. in workplaces and
> education), so check it before using the detector with people. The detector is not used in the
> evaluation. The lip activity detector (barge-in) is independent of it.

## Nodes

| Node (ros2 run name) | Description |
| --- | --- |
| `user_state_server` | Keeps the latest estimates of the detectors and serves them on `/interaction_perception/get_user_state`. |
| `emotion_detector` | Preliminary (see above). Detects the faces (MediaPipe) and classifies the expression of each (a Hugging Face image classifier); publishes JSON. |
| `lip_activity_detector` | Estimates whether the user is speaking from the dynamics of the lips (MediaPipe FaceLandmarker). Its node is named `lip_activity_detector_node`. |

The lip activity detector classifies speaking from the *variance* of the changes of the lip gap:
speech oscillates fast, while yawns and smiles move monotonically. It needs a continuous onset
before it reports speaking, and a continuous closed mouth before it stops.

## The emotion detector: current limitations and future work

The emotion detector runs, but the interaction system does not ask for the state of the user by
default (`user_state_service: ""`), so no emotion reaches the prompts. Only two templates mention
an emotion: `rephrase_explanation` and `procedure_help_step` acknowledge frustration when the
context flags it. Before the detector is used with people, it needs the following:

1. A temporal window. Each frame is now classified on its own, and the last label is published.
   The detector should aggregate the labels and confidences of the last seconds (a moving average
   or a majority over a window, with hysteresis) and report an emotion only when it is stable, so
   that a single frame (a blink, a word being said, a moment of concentration) does not count.
2. One person. Every face in the image is now classified, and their results overwrite each
   other. The detector should follow the person the robot is talking to (the largest or most
   central face, or the one whose lips move, since the lip activity detector already finds the
   speaker), with a tracker so that the identity does not jump between faces.
3. A validated model. The current classifier (`dima806/face_emotions_image_detection`) is a
   third-party model trained on posed faces. The model should be chosen and validated for
   spontaneous expressions in the robot's conditions (light, distance, angle, movement, older
   faces), with its license checked and its accuracy measured on recordings of real conversations
   annotated by people.
4. A mapping of the labels. The classifier has seven expression labels (angry, disgust, fear,
   happy, neutral, sad, surprise), while the context and two prompt templates refer to
   `frustrated`, which never comes. The labels should be mapped to the states that matter for a
   conversation (e.g. negative and confused vs positive vs neutral), in agreement with the prompts
   and the tests.
5. Other cues. A facial expression is now taken as an emotion, but expressions are an ambiguous
   signal of emotion. They should be combined with other cues (the words, the voice prosody, the
   course of the conversation) and used to adapt the manner (slower, simpler, reassuring), never
   to state what the person feels.
6. No debug window. The node and `config/params.yaml` default to a debug window
   (`show_visualization: true`). `interaction_perception.launch.py` turns it off
   (`visualization:=false`), but `emotion_detector.launch.py` does not; it should be off by
   default everywhere on a robot without a screen.
7. An evaluated effect. In the prompts, the emotion was used as a fixed instruction (adapt the
   tone to the emotion). Whether adapting to the detected state improves the conversation should
   be measured against not adapting.
8. Legal checks. In the EU, the AI Act restricts emotion recognition (it is prohibited in
   workplaces and education, with exceptions). Check it, and obtain the ethics and data-protection
   approvals, before any use with people.

The lip activity detector (barge-in) is independent of all this, and so is the user-state
service, which also reports `speaking`.

## Interfaces

The service, the topics and the `UserState` message are in
[ROS_INTERFACES.md](../../docs/ROS_INTERFACES.md#the-perception-of-the-user).

## Usage

```bash
ros2 launch interaction_perception interaction_perception.launch.py image_topic:=/camera/color/image_raw
ros2 launch interaction_perception interaction_perception.launch.py lips:=false visualization:=true
ros2 service call /interaction_perception/get_user_state interaction_perception_msgs/srv/GetUserState
```

The arguments of the launch files are in
[LAUNCH_ARGUMENTS.md](../../docs/LAUNCH_ARGUMENTS.md#the-perception-of-the-user). A camera
publishing on the configured image topic must be running.

The detectors need `cv_bridge` and their Python packages, installed with
`pip install -r requirements.txt` in the environment that runs them: MediaPipe and OpenCV, and,
for the emotion detector only, `transformers`, PyTorch and Pillow (`transformers` and PyTorch are
also in the pixi environment and in simple_hri's requirements). The MediaPipe face landmarker and
the emotion classifier download themselves on the first run.

## Configuration

The emotion detector's parameters are in [`config/params.yaml`](config/params.yaml) and the lip
detector's in [`config/lip_activity_params.yaml`](config/lip_activity_params.yaml); every
parameter of the three nodes is in
[CONFIGURATION.md](../../docs/CONFIGURATION.md#user_state_server).
