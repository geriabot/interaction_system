# interaction_perception: the perception of the user

The perception subsystem of the interaction system: what the robot perceives of the person it is
talking to, from a camera. It runs apart from the interaction system, which uses it only through a
topic (the lip activity, for barge-in, experimental) and a service (the state of the user, off by
default). It is optional: without it, the conversation only loses the visual barge-in. A
perception system that replaces these detectors only has to publish the same topic and serve the
same service. How it fits in the system:
[ARCHITECTURE.md](../../docs/ARCHITECTURE.md#speech-perception-and-embodiment).

> **The emotion detector is preliminary work, not validated.** It classifies each frame on its
> own (no smoothing over time) with a third-party image classifier trained on posed faces, which
> is unreliable with real faces, light and movement; its labels do not include `frustrated`,
> which two prompt templates react to; a facial expression is not a reliable sign of an emotion;
> and in the EU the AI Act restricts emotion recognition (e.g. in workplaces and education):
> check it before using the detector with people. It is not used in the evaluation. The lip
> activity detector (barge-in) is independent of it.

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

The emotion detector is preliminary: it runs, but it has not been validated, and the interaction
system does not ask for the state of the user by default (`user_state_service: ""`), so no
emotion reaches the prompts. Only two templates mention an emotion (`rephrase_explanation` and
`procedure_help_step` acknowledge frustration when the context flags it). What the detector does
now, and what it would need before it is used with people:

| Now | What it needs |
|---|---|
| Each frame is classified on its own, and the last label is published | A **temporal window**: aggregate the labels and confidences of the last seconds (a moving average or majority over a window, with hysteresis), and report an emotion only when it is stable, so that a single frame (a blink, a word being said, a moment of concentration) does not count |
| Every face in the image is classified, and their results overwrite each other | Follow **one person**: the one the robot is talking to (the largest or most central face, or the one whose lips move: the lip activity detector already finds the speaker), with a tracker so that the identity does not jump between faces |
| A third-party image classifier (`dima806/face_emotions_image_detection`) trained on posed faces | A model chosen and **validated** for spontaneous expressions in the robot's conditions (light, distance, angle, movement, older faces), with its licence checked; its accuracy measured on recordings of real conversations annotated by people |
| Seven expression labels (angry, disgust, fear, happy, neutral, sad, surprise); the context and two prompt templates speak of `frustrated`, which never comes | A **mapping** from the labels to the states that matter for a conversation (e.g. negative and confused vs positive vs neutral), agreed with the prompts and the tests |
| A facial expression is taken as an emotion | Expressions are an ambiguous signal of emotion: combine them with other cues (the words, the voice prosody, the course of the conversation), and use them to adapt the manner (slower, simpler, reassuring), never to state what the person feels |
| A debug window opens by default (`show_visualization: true`) | Off by default on a robot without a screen |
| Its use in prompts was a fixed instruction (adapt the tone to the emotion) | An evaluated effect: whether adapting to the detected state improves the conversation, measured against not adapting |
| — | **Legal**: in the EU, the AI Act restricts emotion recognition (prohibited in workplaces and education, with exceptions); check it, and the ethics and data-protection approvals, before any use with people |

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

The detectors need `cv_bridge` and their Python packages: `pip install -r requirements.txt`
(MediaPipe, OpenCV) in the environment that runs them; the emotion detector also needs
`transformers` and PyTorch (in the pixi environment and in simple_hri's requirements), and Pillow.
The MediaPipe face landmarker and the emotion classifier download themselves on the first run.

## Configuration

The emotion detector's parameters are in [`config/params.yaml`](config/params.yaml) and the lip
detector's in [`config/lip_activity_params.yaml`](config/lip_activity_params.yaml); every
parameter of the three nodes is in
[CONFIGURATION.md](../../docs/CONFIGURATION.md#user_state_server).
