# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
A simulated user driven by an LLM, for the experiments.

The user has a persona (how they talk and behave) and a goal (what they want, which is also the
ground truth of the scenario: the order to place, the answers to give to a quiz, how they get on
with each step of a procedure...). It only sees what the robot says, never the robot's
internals, and answers with one short spoken line, as a person would to a robot.

Events planned by the scenario are applied by code, not left to the LLM, so that every
condition gets the same perturbations at the same turn: stay silent, ask to cancel (and then
confirm or deny), barge in. ASR noise (asr_noise.py) is applied to what the user says.

The LLM (fixed for an experiment, of a different family from the system's) is an Ollama model
or a cloud model (provider "gemini" or "openai", with its dated version pinned), asked with a
seed per turn so that a run can be repeated as far as the provider allows. Cloud calls are paid:
they are refused unless allow_cloud is set (run_experiment.py --allow-cloud). The API keys are
read from the environment: GEMINI_API_KEY (or GOOGLE_API_KEY) and OPENAI_API_KEY. Every call is
logged with its token usage.

The simulated user only speaks: its prompt holds the persona, the goal, its goal items as a script
to follow (nothing to declare; provisional, 2026-11-01) and the conversation. What each of its lines states (the goal items, whether it asks the robot
for something) is marked after the dialogue by a local annotator, for the adherence check and the
grounded-QA scoring (decided by the author, 2026-10-31: declaring while playing the person loaded
the model that plays it; the declarations of earlier runs, `revealed`, are still read).
"""

import random
from dataclasses import dataclass, field

from .asr_noise import AsrNoise
from .cloud_llm import ask_json, check_provider
from .goal_items import ANSWER_FORMAT, parse_line, script_block, validate_goal_items
from .harness import UserTurn

PROMPT = """You play a person talking to a social robot, to test the robot. Stay in character.

WHO YOU ARE
{persona}

WHAT YOU WANT FROM THIS CONVERSATION
{goal}

HOW TO ANSWER
- Say only what this person would say aloud next: one short spoken line (usually 1 sentence,
  at most 25 words), ALWAYS in {language} (whatever the persona or the robot), with no stage
  directions, quotes or emojis.
- When the robot asks you to say back or sum up what it explained, say back in that one line
  everything your goal tells you to say (up to 45 words), not one point at a time.
- Answer what the robot just said. Give information only when it is asked or natural.
- Do what your goal says, when it says it and in its order (e.g. "first say X, and in your next
  answer change it to Y": the next answer changes it). If your goal is to do something in a
  conversation (ask, propose, negotiate), do it yourself: do not only answer the other side's
  questions.
- A change your goal plans ("change it to Y", "when it corrects you, say it correctly") is for
  good: once it has happened, say only the new version from then on, never the first one again.
- Do not ask again what the robot has already answered, declined or said it does not know,
  unless your goal says so: go on with the rest of your goal, or end when it is done.
- Do not invent facts beyond your goal; if asked something your goal does not cover, answer
  like the persona would (e.g. "I don't mind", "no idea").
- When your goal is reached and the robot has closed the conversation, or the robot says
  goodbye, answer with action "end".

{items}THE CONVERSATION SO FAR
{transcript}

THE ROBOT JUST SAID
"{robot}"

{answer}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["say", "end"]},
        "text": {"type": "string"},
    },
    "required": ["action", "text"],
}

@dataclass
class UserEvent:
    """Something the user does at a given user turn (1-based), decided by the scenario."""

    turn: int
    kind: str  # silence | cancel | text | detour | barge_in
    text: str = ""  # cancel / text / detour: what the user says
    barge_in_after: float = 0.5
    # detour: what the line tries, as the detours of the participants' cards (off_task: an
    # unrelated question; decline: a request the robot should decline; not_allowed: a value the
    # task does not allow)
    detour: str = ""

    def __post_init__(self):
        if self.kind not in ("silence", "cancel", "text", "detour", "barge_in"):
            raise ValueError(f"Unknown user event kind '{self.kind}'")
        if self.kind == "detour" and (
                not self.text or self.detour not in ("off_task", "decline", "not_allowed")):
            raise ValueError("A detour event needs its text and detour: off_task, decline or "
                             "not_allowed")


@dataclass
class LlmUser:
    """User policy for DialogueHarness.run (a callable)."""

    persona: str
    goal: str
    model: str
    provider: str = "ollama"  # ollama | gemini | openai
    host: str = "http://localhost:11434"  # the Ollama server
    allow_cloud: bool = False  # a cloud provider is paid: it must be allowed explicitly
    language: str = "English"
    seed: int = 0
    temperature: float | None = 0.7  # None: the provider's default (OpenAI reasoning models)
    events: list = field(default_factory=list)  # list[UserEvent]
    noise: AsrNoise | None = None
    max_turns: int = 30
    on_gpu: bool = True  # False: the simulator runs on the server's CPU (num_gpu 0)
    goal_items: list = field(default_factory=list)  # [{id, value[, then]}]: see goal_items.py
    log: list = field(default_factory=list)  # every call: prompt, answer, applied noise

    def __post_init__(self):
        check_provider(self.provider, self.allow_cloud)
        self.goal_items = validate_goal_items(self.goal_items)
        self._turn = 0
        self._ended = False
        self._rng = random.Random(self.seed)

    def __call__(self, robot_utterance: str, transcript: list[dict]):
        if self._ended:
            return None
        if self._turn >= self.max_turns:
            if not self.log or self.log[-1].get("event") != "turn_limit":
                self.log.append({"turn": self._turn, "event": "turn_limit"})
            return None
        self._turn += 1
        event = next((e for e in self.events if e.turn == self._turn), None)
        if event is not None and event.kind == "silence":
            self.log.append({"turn": self._turn, "event": "silence"})
            return UserTurn(None)
        if event is not None and event.kind in ("cancel", "text", "detour"):
            entry = {"turn": self._turn, "event": event.kind, "text": event.text}
            if event.kind == "detour":
                entry["detour"] = event.detour
            self.log.append(entry)
            return UserTurn(event.text)

        text, action = self._ask_llm(robot_utterance, transcript)
        if action == "end":
            self._ended = True
            return None
        spoken = self.noise.apply(text, self._rng) if self.noise else text
        if spoken != text:
            self.log[-1]["heard"] = spoken
        turn = UserTurn(spoken)
        if event is not None and event.kind == "barge_in":
            turn.barge_in_after = event.barge_in_after
        return turn

    def _ask_llm(self, robot: str, transcript: list[dict]) -> tuple[str, str]:
        lines = [
            f"{'Robot' if t['role'] == 'robot' else 'You'}: {t['text']}"
            for t in transcript[:-1]  # the last one is what the robot just said
        ] or ["(the robot speaks first)"]
        prompt = PROMPT.format(
            persona=self.persona.strip(), goal=self.goal.strip(), language=self.language,
            transcript="\n".join(lines), robot=robot,
            items=script_block(self.goal_items), answer=ANSWER_FORMAT,
        )
        seed = self.seed * 1000 + self._turn
        entry = {"turn": self._turn, "robot": robot}
        max_tokens = 120 if self.provider == "ollama" else 200
        try:
            raw, meta = ask_json(
                self.provider, self.model, prompt, SCHEMA,
                temperature=self.temperature, seed=seed, max_tokens=max_tokens,
                host=self.host, on_gpu=self.on_gpu)
            entry.update(meta)
            answer = parse_line(raw)
            text = str(answer.get("text", "")).strip()
            action = answer.get("action", "say")
        except Exception as exc:
            # Logged: the scoring counts the dialogues with simulator errors
            entry["error"] = f"{type(exc).__name__}: {exc}"
            text, action = "Sorry, could you repeat that?", "say"
        if action == "say" and not text:
            text = "Sorry?"
        entry.update({"action": action, "text": text})
        self.log.append(entry)
        return text, action
