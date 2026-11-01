# Copyright 2026 Álvaro Quintana Camacho
# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
InteractionIntelligence: the node that asks the LLM what the interaction system needs.

The orchestrator asks it which dialogue behavior fits a request; the Behavior Trees ask it
their skills (understand an utterance, phrase a reply, apply what was understood to the data
of the dialogue). It builds each question from the prompt templates, sends it to the LLM
gateway (ros2_llm_manager, an external service), parses the answer and falls back to a safe
deterministic answer when the model fails. It keeps no state of the dialogue between calls.

Services:
  /interaction/ask_intelligence              (interaction_system_interfaces/srv/AskIntelligence)
  /interaction/ask_intelligence_for_behavior (interaction_system_interfaces/srv/AskForDialogueBehavior)

Topics consumed:
  /listened_text, /transcription (String) — what the user says: simple_hri's
                          transcriptions and typed text; cleaned and republished.
  /robot_utterance        (String) — what the robot says (transcript).
  /robot_filler           (String) — the conversational fillers it says (transcript, marked).
  /interaction/user_turn  (String) — the user utterances taken as turns (transcript).

Topics published:
  /interaction/user_utterance (String) — the transcriptions that are not noise.
  /interaction/skill_trace    (String) — JSON per skill asked: dialogue, skill, label,
                                         success, time (s), and its calls to the model that
                                         timed out or were not made (llm_timeouts,
                                         llm_skipped); for diagnosis and evaluation.

Downstream clients:
  /interaction_perception/get_user_state (GetUserState service, optional) — the state of
                         the user (emotion) for the context of the questions
  /llm/inference         (LlmInference)
  /llm/session/create    (CreateSession service)
  /llm/session/delete    (DeleteSession service)
"""

import importlib
import json
import logging
import string
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

import rclpy
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.time import Time
from std_msgs.msg import String

from ros2_llm_interfaces.action import LlmInference
from ros2_llm_interfaces.srv import CreateSession, DeleteSession
from interaction_system_interfaces.srv import AskForDialogueBehavior, AskIntelligence
from interaction_perception_msgs.srv import GetUserState

from .dialog_context import DialogContext, Resource, UserState
from .languages import LanguageFileError, UnknownLanguageError, load_language
from .llm_client import (CallLimits, LLMDialogClient, default_ack_phrases,
                         default_filler_phrases, default_timeout_prompt)
from .prompt_builder import PromptBuilder
from .selection import choose_behavior
from .skills import SkillEnv, SkillInput, get_mode, get_skill
from .skills.common import parse_resources
from .utils import clean_user_input

_LOGGERS = (
    "InteractionIntelligence",
    "LLMDialogClient",
    "SlotFillingSkills",
    "FrameOperations",
    "PromptBuilder",
)


class InteractionIntelligence(Node):

    def __init__(self):
        super().__init__(
            "interaction_intelligence",
            automatically_declare_parameters_from_overrides=True,
        )
        self._declare_parameters()
        self._read_parameters()
        self._configure_logging()
        self._templates = self._load_prompt_templates()
        for module in self.get_parameter("skill_modules").value or []:
            if module.strip():
                importlib.import_module(module.strip())
                self.get_logger().info(f"Skills loaded from {module.strip()}")
        self._make_llm = self._llm_factory()

        self._lock = threading.Lock()
        self._sessions: dict[str, tuple[str, str]] = {}  # dialogue_id -> (nlu, nlg)
        self._finished: set[str] = set()  # dialogues ended: their LLM calls are cancelled
        self._transcript_dialogue: str | None = None

        self._cb = ReentrantCallbackGroup()
        self._llm_create = self.create_client(
            CreateSession, "/llm/session/create", callback_group=self._cb
        )
        self._llm_delete = self.create_client(
            DeleteSession, "/llm/session/delete", callback_group=self._cb
        )
        self._llm_action = ActionClient(
            self, LlmInference, "/llm/inference", callback_group=self._cb
        )

        self.create_service(
            AskIntelligence, "/interaction/ask_intelligence", self._on_ask, callback_group=self._cb
        )
        self.create_service(
            AskForDialogueBehavior,
            "/interaction/ask_intelligence_for_behavior",
            self._on_ask_behavior,
            callback_group=self._cb,
        )

        self._utterance_pub = self.create_publisher(String, "/interaction/user_utterance", 10)
        self._trace_pub = self.create_publisher(String, "/interaction/skill_trace", 50)
        for topic in self._transcription_topics:
            self.create_subscription(
                String, topic, self._on_transcription, 10, callback_group=self._cb
            )
        self._user_state_client = (
            self.create_client(GetUserState, self._user_state_service, callback_group=self._cb)
            if self._user_state_service else None
        )
        self.create_subscription(
            String, "/robot_utterance", self._on_robot_utterance, 10, callback_group=self._cb
        )
        self.create_subscription(
            String, "/robot_filler", self._on_robot_filler, 10, callback_group=self._cb
        )
        self.create_subscription(
            String, "/interaction/user_turn", self._on_user_turn, 10, callback_group=self._cb
        )

        self._log("Interaction intelligence ready.")

    # ==== PARAMETERS ====

    def _declare_parameters(self) -> None:
        defaults: dict[str, object] = {
            "transcription_topics": ["/listened_text", "/transcription"],
            # The perception subsystem's service; "" to run without it
            "user_state_service": "",
            # Longest wait for one call to the LLM (s); the time budget of the reply, sent by
            # the tree with each request (budget_s), can make it shorter
            "llm_timeout": 15.0,
            "llm_provider": "",
            "llm_model": "",
            "language": "English",
            "history_max_turns": 200,
            "ack_phrases": [""],  # Empty = built-in phrases for `language`; set a list to override
            "ack_enabled": True,  # False = never prefix replies with an acknowledgment
            # Conversational fillers said while a reply is prepared; empty = built-in phrases
            # for `language` (when they are said is a parameter of the orchestrator)
            "filler_phrases": [""],
            "timeout_prompt": "",
            "silent_mode": False,
            "conversation_log_path": "/tmp/dialog_conversations.log",
            "conversation_log_max_mb": 10.0,
            # Questions sent to another model than llm_model, as "question=model" (the question is
            # the method of LLMDialogClient that asks it, e.g. "classify_intent=qwen2.5:14b");
            # [""] for none: every question goes to llm_model
            "routes": [""],
            # Python modules with more skills (and dialogue modes) to register, e.g. the skills
            # of a new dialogue behavior; [""] for none
            "skill_modules": [""],
            # Tests only: "<module>:<callable>" returning an object with the methods of
            # LLMDialogClient, used instead of the LLM gateway
            "llm_client_factory": "",
        }
        for name, default in defaults.items():
            if not self.has_parameter(name):
                self.declare_parameter(name, default)

    def _read_parameters(self) -> None:
        p = self.get_parameter
        self._transcription_topics = list(p("transcription_topics").value)
        self._user_state_service = p("user_state_service").value
        self._llm_timeout = p("llm_timeout").value
        self._llm_provider = p("llm_provider").value
        self._llm_model = p("llm_model").value
        self._routes: dict[str, str] = {}
        for entry in p("routes").value or []:
            if not entry.strip():
                continue
            question, _, model = entry.partition("=")
            if not model.strip():
                raise ValueError(f"Invalid route '{entry}': use question=model")
            self._routes[question.strip()] = model.strip()
        # An unknown question stops the node here, not at the first conversation
        self._routes = LLMDialogClient.resolve_routes(self._routes)
        # The language file of the dialogue language (config/languages): a language without
        # one stops the node here, not with a user in front of the robot (languages.py)
        try:
            self._language = load_language(p("language").value).name
        except (UnknownLanguageError, LanguageFileError) as exc:
            self.get_logger().fatal(str(exc))
            raise
        self._history_max_turns = p("history_max_turns").value
        self._silent = p("silent_mode").value
        self._conversation_log_path = p("conversation_log_path").value
        self._conversation_log_max_mb = p("conversation_log_max_mb").value

        # Canned-phrase overrides from the Strategies editor (params under "phrases.*").
        # 'ack' (list) and 'timeout' (str) are the system's own phrases; the rest are NLG
        # fallbacks layered over the language defaults in LLMDialogClient.
        phrases = self._load_phrase_overrides()
        ack_ovr = phrases.pop("ack", None)
        filler_ovr = phrases.pop("filler", None)
        timeout_ovr = phrases.pop("timeout", None)
        self._phrase_overrides = phrases

        # Precedence for ack / timeout: phrases.* override > dedicated param > language-aware
        # default. Empty everywhere => follows `language`.
        legacy_ack = tuple(s for s in (p("ack_phrases").value or []) if s.strip())
        ack_ovr = tuple(s for s in (ack_ovr or []) if s and s.strip())
        self._ack_phrases = ack_ovr or legacy_ack or default_ack_phrases(self._language)
        # The system's default; a request may turn them on or off (DialogTask.acknowledgments)
        self._ack_enabled = bool(p("ack_enabled").value)

        # The same precedence for the fillers (phrases.filler > filler_phrases > language)
        filler_param = tuple(s for s in (p("filler_phrases").value or []) if s.strip())
        filler_ovr = tuple(s for s in (filler_ovr or []) if s and s.strip())
        self._filler_phrases = (
            filler_ovr or filler_param or default_filler_phrases(self._language))

        legacy_timeout = (p("timeout_prompt").value or "").strip()
        self._timeout_prompt = (
            (timeout_ovr or "").strip() or legacy_timeout or default_timeout_prompt(self._language)
        )

    def _load_phrase_overrides(self) -> dict:
        out: dict = {}
        for name, param in self.get_parameters_by_prefix("phrases").items():
            val = param.value
            if val is None:
                continue
            if isinstance(val, str):
                if val.strip():
                    out[name] = val
            else:  # 'ack' and 'filler' arrive as string lists
                out[name] = val
        return out

    def _configure_logging(self) -> None:
        level = logging.WARNING if self._silent else logging.INFO
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[%(name)s] %(levelname)s: %(message)s"))
        for name in _LOGGERS:
            log = logging.getLogger(name)
            log.setLevel(level)
            if not log.handlers:
                log.addHandler(handler)
            log.propagate = False

    def _load_prompt_templates(self) -> dict[str, str]:
        params = self.get_parameters_by_prefix("prompts")
        templates = {k: v.value for k, v in params.items() if v.value}
        if not templates:
            self.get_logger().fatal(
                "No prompt templates loaded. Check that prompts.yaml is passed via --params-file."
            )
            raise RuntimeError("prompts.yaml missing or empty under 'prompts.*'")

        broken: list[str] = []
        for name, tpl in templates.items():
            try:
                for _, field_name, _, _ in string.Formatter().parse(tpl):
                    if field_name is None or field_name == "":
                        continue
                    if not field_name.isidentifier():
                        broken.append(f"{name}: malformed placeholder '{{{field_name}}}'")
            except ValueError as exc:
                broken.append(f"{name}: format-string error: {exc}")
        if broken:
            for line in broken:
                self.get_logger().fatal(f"Bad prompt template — {line}")
            raise RuntimeError(
                "Invalid format strings in prompts.yaml. Literal '{' and '}' must be doubled."
            )
        return templates

    def _llm_factory(self) -> Callable[[dict, str, CallLimits], object]:
        """How the LLM client of a request is built: on the LLM gateway, or (tests) a double. The
        limits of the request (llm_timeout, its time budget) bound every call to the model."""
        spec = (self.get_parameter("llm_client_factory").value or "").strip()
        if not spec:
            def make(sessions: dict, dialogue_id: str, limits: CallLimits) -> LLMDialogClient:
                nlu_sid, nlg_sid = sessions[""]
                return LLMDialogClient(
                    nlu_sid,
                    nlg_sid,
                    lambda sid, prompt, max_tokens, schema: self._infer_sync(
                        dialogue_id, sid, prompt, max_tokens, schema, limits.wait
                    ),
                    PromptBuilder(self._templates, language=self._language),
                    language=self._language,
                    history_max_turns=self._history_max_turns,
                    phrase_overrides=self._phrase_overrides,
                    routes={q: sessions[m] for q, m in self._routes.items()},
                    limits=limits,
                )

            return make
        module_name, _, attr = spec.partition(":")
        factory = getattr(importlib.import_module(module_name), attr)
        self.get_logger().warning(f"Using the test LLM client {spec}")

        def make_double(sessions: dict, dialogue_id: str, limits: CallLimits):
            client = factory()
            # A double that builds real clients (the injector of model failures) bounds their
            # calls with the limits of the request, and speaks the language of the dialogue
            for name, value in (("limits", limits), ("language", self._language)):
                try:
                    setattr(client, name, value)
                except AttributeError:
                    pass
            return client

        return make_double

    # ==== SERVICES ====

    def _on_ask(self, req, res):
        t0 = time.monotonic()
        # The calls of this request wait at most llm_timeout each, and only while the budget of
        # the reply being prepared lasts (budget_s; 0: none)
        limits = CallLimits(self._llm_timeout, req.budget_s)
        res = self._ask(req, res, limits)
        self._trace_pub.publish(String(data=json.dumps({
            "dialogue_id": req.dialogue_id, "skill": req.skill, "label": res.label,
            "success": res.success, "time_s": round(time.monotonic() - t0, 3),
            "message": res.message, "budget_s": round(req.budget_s, 3),
            "llm_timeouts": limits.timeouts, "llm_skipped": limits.skipped,
        })))
        return res

    def _ask(self, req, res, limits: CallLimits):
        res.state = req.state
        if req.skill == "finish":
            self._finish(req)
            res.success = True
            return res
        try:
            state = json.loads(req.state) if req.state else {}
            args = json.loads(req.args) if req.args else {}
            env = SkillEnv(
                llm=self._llm_for(req.dialogue_id, limits),
                ctx=self._dialogue_context(state if req.skill != "begin" else {}),
                ack_phrases=self._ack_phrases,
                ack_enabled=self._ack_enabled,
                filler_phrases=self._filler_phrases,
                timeout_prompt=self._timeout_prompt,
                history_max_turns=self._history_max_turns,
            )
            if req.skill == "begin":
                self._transcript_header(req.dialogue_id, args.get("task_description", ""))
            result = get_skill(req.skill)(
                env, state, SkillInput(req.user_text, req.option, req.number, args)
            )
            # Whether the model is down, for the next requests of the dialogue: the outcome of
            # the last call to it (a request without a call leaves it as it was). Kept in the
            # state of the dialogue because the intelligence keeps none between requests
            status = getattr(env.llm, "model_status", None)
            if status is not None and req.skill != "begin":
                state["model_down"] = not status
            mode = get_mode(state["mode"])
            res.success = True
            res.state = json.dumps(state, ensure_ascii=False)
            res.reply = result.reply or ""
            res.label = result.label or ""
            res.phase = mode.phase(state)
            res.data = json.dumps(mode.data(state), ensure_ascii=False)
        except Exception as exc:
            res.success = False
            res.message = f"{req.skill}: {exc}"
            self.get_logger().error(
                f"Skill {req.skill} failed: {type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            )
        return res

    def _on_ask_behavior(self, req, res):
        behaviors = [
            {
                "behavior_id": b.behavior_id,
                "name": b.name,
                "mode": b.mode,
                "explanation": b.explanation,
                "needs": list(b.needs),
            }
            for b in req.behaviors
        ]
        resources = parse_resources(req.resources_json)
        selection_id = f"select_{time.monotonic_ns()}"
        try:
            ctx = DialogContext(
                resources=[Resource(**r) for r in resources], domain=req.domain
            )
            ctx.update_user_state(self._current_user_state())
            chosen, offered = choose_behavior(
                self._llm_for(selection_id, CallLimits(self._llm_timeout)),
                req.task_description, behaviors, resources, ctx
            )
            res.success = True
            res.behavior_id = chosen
            res.offered_ids = offered
            name = next((b["name"] for b in behaviors if b["behavior_id"] == chosen), "none")
            self._log(f"Dialogue behavior for '{req.task_description[:60]}': {name}")
        except Exception as exc:
            res.success = False
            res.behavior_id = -1
            res.message = str(exc)
            self.get_logger().error(f"Choosing the dialogue behavior failed: {exc}")
        finally:
            self._release(selection_id)
        return res

    def _finish(self, req) -> None:
        """The dialogue ended (option: its outcome; number: its turns): cancel its pending LLM
        calls, delete its sessions and close the transcript."""
        with self._lock:
            self._finished.add(req.dialogue_id)
        self._release(req.dialogue_id)
        data = "{}"
        try:
            state = json.loads(req.state) if req.state else {}
            if state.get("mode"):
                data = json.dumps(get_mode(state["mode"]).data(state), ensure_ascii=False)
        except Exception:
            pass
        self._transcript_footer(req.dialogue_id, req.option or "ended", req.number, data)

    # ==== DIALOGUE CONTEXT ====

    def _dialogue_context(self, state: dict) -> DialogContext:
        hidden = set(state.get("hidden_resources") or [])
        resources = [
            Resource(name=r["name"], description=r.get("description", ""), content=r["content"])
            for i, r in enumerate(state.get("resources") or [])
            if i not in hidden
        ]
        ctx = DialogContext(resources=resources, domain=state.get("domain", ""))
        ctx.update_user_state(self._current_user_state())
        return ctx

    def _current_user_state(self) -> UserState:
        """The state of the user from the perception subsystem, if it serves it (neutral
        otherwise). The context includes it only when it is notable and recent."""
        client = self._user_state_client
        if client is None or not client.service_is_ready():
            return UserState()
        res = self._call_service_sync(client, GetUserState.Request(), timeout=0.5)
        if res is None or not res.available:
            return UserState()
        s = res.state
        age = (self.get_clock().now() - Time.from_msg(s.stamp)).nanoseconds / 1e9
        return UserState(
            emotion=s.emotion or "neutral",
            confidence=s.confidence,
            source=s.source or "vision",
            timestamp=time.monotonic() - max(0.0, age),
        )

    def _on_transcription(self, msg: String) -> None:
        clean = clean_user_input(msg.data)
        if clean:
            self._utterance_pub.publish(String(data=clean))

    # ==== LLM SESSIONS ====

    def _llm_for(self, dialogue_id: str, limits: CallLimits):
        """The LLM client of a request of a dialogue, with the dialogue's NLU and NLG sessions
        (created on first use: one pair for llm_model ("") and one for each model of the routes)
        and the time limits of the request."""
        with self._lock:
            sessions = self._sessions.get(dialogue_id)
        if sessions is None:
            sessions = {}
            tag = dialogue_id[:12] or "dialogue"
            for i, model in enumerate(["", *sorted(set(self._routes.values()))]):
                if self._fake():
                    sessions[model] = ("nlu", "nlg")
                    continue
                nlu = self._create_llm_session(f"{tag}_{i}_nlu", model)
                nlg = self._create_llm_session(f"{tag}_{i}_nlg", model)
                if not nlu or not nlg:
                    raise RuntimeError("LLM Manager connection failed")
                sessions[model] = (nlu, nlg)
            with self._lock:
                self._sessions[dialogue_id] = sessions
        return self._make_llm(sessions, dialogue_id, limits)

    def _fake(self) -> bool:
        return bool((self.get_parameter("llm_client_factory").value or "").strip())

    def _release(self, dialogue_id: str) -> None:
        with self._lock:
            sessions = self._sessions.pop(dialogue_id, None)
        if sessions and not self._fake():
            for pair in sessions.values():
                for sid in pair:
                    self._delete_llm_session(sid)

    def _create_llm_session(self, session_id: str, model: str = "") -> str | None:
        if not self._llm_create.wait_for_service(timeout_sec=5.0):
            return None
        req = CreateSession.Request(
            session_id=session_id,
            provider=self._llm_provider,
            model=model or self._llm_model,
            stateful=False,
        )
        res = self._call_service_sync(self._llm_create, req)
        return res.session_id if res and res.success else None

    def _delete_llm_session(self, session_id: str) -> None:
        if not self._llm_delete.wait_for_service(timeout_sec=3.0):
            return
        self._call_service_sync(self._llm_delete, DeleteSession.Request(session_id=session_id))

    # ==== INFERENCE ====

    def _infer_sync(
        self, dialogue_id: str, session_id: str, prompt: str, max_tokens: int,
        schema: dict | None, timeout: float | None = None,
    ) -> str | None:
        """One call to the LLM gateway; nothing after `timeout` seconds (the wait the client
        allows this call: llm_timeout or what is left of the budget; None: no limit)."""
        if not prompt or not prompt.strip():
            self.get_logger().error("Empty prompt; aborting inference.")
            return None
        timeout = float("inf") if timeout is None else timeout
        started = time.monotonic()
        if not self._llm_action.wait_for_server(timeout_sec=min(5.0, timeout)):
            self.get_logger().error("LLM action server not available.")
            return None
        goal = LlmInference.Goal(
            session_id=session_id,
            prompt=prompt,
            max_tokens=max_tokens or 0,
            format_schema=json.dumps(schema) if schema else "",
            cache_prefix_len=getattr(prompt, "cache_prefix_len", 0),
        )
        result = self._action_call_sync(
            self._llm_action,
            goal,
            max(timeout - (time.monotonic() - started), 0.0),
            external_cancel=lambda: dialogue_id in self._finished,
        )
        if result is None:
            return None
        if not result.result.success:
            self.get_logger().error(f"LLM inference error: {result.result.answer}")
            return None
        return result.result.answer

    def _action_call_sync(
        self, client: ActionClient, goal, timeout_secs: float,
        external_cancel: Callable[[], bool] | None = None,
    ):
        """Send goal and block until done, timeout, or external cancel. Cancels the remote goal
        on timeout or external cancel to avoid server-side leaks."""
        done = threading.Event()
        goal_handle_box: list = [None]
        result_box: list = [None]

        def on_accepted(future):
            gh = future.result()
            if not gh or not gh.accepted:
                done.set()
                return
            goal_handle_box[0] = gh
            gh.get_result_async().add_done_callback(
                lambda r: (result_box.__setitem__(0, r.result()), done.set())
            )

        client.send_goal_async(goal).add_done_callback(on_accepted)
        deadline, poll = time.monotonic() + timeout_secs, 0.05
        while not done.wait(timeout=poll):
            if time.monotonic() >= deadline:
                if goal_handle_box[0] is not None:
                    goal_handle_box[0].cancel_goal_async()
                self.get_logger().warning(f"LLM call timed out after {timeout_secs:.1f}s.")
                return None
            if external_cancel and external_cancel():
                if goal_handle_box[0] is not None:
                    goal_handle_box[0].cancel_goal_async()
                done.wait(timeout=1.0)
                return None
        return result_box[0]

    def _call_service_sync(self, client, req, timeout: float = 10.0):
        done = threading.Event()
        result = [None]

        def cb(future):
            try:
                result[0] = future.result()
            finally:
                done.set()

        client.call_async(req).add_done_callback(cb)
        done.wait(timeout=timeout)
        return result[0]

    # ==== TRANSCRIPT (best-effort, never raises) ====

    def _transcript_path(self) -> Path | None:
        return Path(self._conversation_log_path).expanduser() if self._conversation_log_path else None

    def _transcript_write(self, text: str) -> None:
        path = self._transcript_path()
        if not path:
            return
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(text)
        except Exception as exc:
            self.get_logger().warning(f"Could not write the transcript {path}: {exc}")

    def _transcript_header(self, dialogue_id: str, task: str) -> None:
        with self._lock:
            self._transcript_dialogue = dialogue_id
            self._finished.discard(dialogue_id)
        path = self._transcript_path()
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            max_bytes = int(self._conversation_log_max_mb * 1024 * 1024)
            if max_bytes > 0 and path.exists() and path.stat().st_size >= max_bytes:
                path.replace(path.with_suffix(".log.1"))
        except Exception:
            pass
        self._transcript_write(
            f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} — New dialog ===\nTask: {task}\n\n"
        )

    def _transcript_line(self, role: str, text: str) -> None:
        with self._lock:
            active = self._transcript_dialogue is not None
        if active:
            self._transcript_write(f"[{datetime.now():%H:%M:%S}] {role:9s} {text}\n")

    def _transcript_footer(self, dialogue_id: str, outcome: str, turns: int, data: str) -> None:
        with self._lock:
            if self._transcript_dialogue != dialogue_id:
                return
            self._transcript_dialogue = None
        self._transcript_write(
            f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} — Ended: {outcome} ({turns} turns) ===\n"
            f"Final frame: {data}\n"
        )

    def _on_robot_utterance(self, msg: String) -> None:
        self._transcript_line("assistant", msg.data)

    def _on_robot_filler(self, msg: String) -> None:
        # A conversational filler: said by the robot, but not a line of the dialogue
        self._transcript_line("filler", msg.data)

    def _on_user_turn(self, msg: String) -> None:
        self._transcript_line("user", msg.data)

    def _log(self, msg: str) -> None:
        if not self._silent:
            self.get_logger().info(msg)


def main(args=None):
    rclpy.init(args=args)
    node = InteractionIntelligence()
    executor = MultiThreadedExecutor(num_threads=8)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
