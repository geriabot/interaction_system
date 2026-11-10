# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""The topic-bounded social conversation's skills: the request's topic, the reading of each turn
(a request to hear the last line again and a turn the model cannot read stay out of the
conversation), the limits of exchanges and of time, the closing with its result, the normal end
when the user says they have finished, and the client's fallbacks when the model fails."""

import json

import pytest

from interaction_intelligence.dialog_context import DialogContext
from interaction_intelligence.llm_client import LLMDialogClient
from interaction_intelligence.prompt_builder import PromptBuilder
from interaction_intelligence.selection import provided_needs
from interaction_intelligence.skills import SkillEnv, SkillInput, get_mode, get_skill
from interaction_intelligence.skills import topic_chat

CHAT = {"topic": "Your village in the sixties",
        "material": ["Born in Almagro in 1948", "Worked as a baker"],
        "questions": ["What was the square like?", "What did you do on holidays?"],
        "avoid": ["The death of her husband"], "max_exchanges": 4}


class Model:
    """A test double of the client: the reading of every turn and what the review returns."""

    def __init__(self, kind="on_topic", covered=(1,), topics=("the square",)):
        self.kind = kind
        self.covered = list(covered)
        self.topics = list(topics)
        self.replies = []

    def phrase(self, key):
        return f"[{key}]"

    def topic_chat_opening(self, task, chat, greet=True, ctx=None):
        return "[open]" if greet else "[open_direct]"

    def classify_topic_turn(self, user_text, chat, history, ctx=None):
        return self.kind

    def topic_chat_reply(self, chat, history, kind, ctx=None):
        self.replies.append(kind)
        return f"[{kind}]"

    def topic_chat_closing(self, chat, history, ctx=None):
        return "[bye]"

    def review_topic_chat(self, chat, history, ctx=None):
        return {"covered": self.covered, "topics": self.topics}


def start(model, chat=CHAT, max_turns=0):
    env = SkillEnv(llm=model, ctx=DialogContext())
    state = {}
    goal = {"dialog_mode": "topic_chat", "task_description": "Talk about the village",
            "max_turns": max_turns,
            "resources_json": json.dumps([{"name": "topic", "content": json.dumps(chat)}])}
    get_skill("begin")(env, state, SkillInput(args=goal))
    return env, state


def turn(env, state, text):
    return get_skill("topic_chat.classify")(env, state, SkillInput(user_text=text))


def test_the_topic_is_what_the_request_provides():
    resources = [{"name": "topic", "content": json.dumps(CHAT)}]
    assert "topic" in provided_needs(resources)
    assert "material" not in provided_needs(resources)  # not grounded question answering
    assert "topic" not in provided_needs([{"name": "notes", "content": "Plain text"}])


def test_a_request_without_a_topic_fails_at_once():
    with pytest.raises(ValueError, match="topic"):
        start(Model(), chat={"material": "x"})


def test_the_limit_has_a_minimum_and_follows_max_turns():
    _, state = start(Model(), chat=dict(CHAT, max_exchanges=1))
    assert state["max_exchanges"] == topic_chat.MIN_EXCHANGES
    _, state = start(Model(), chat=dict(CHAT, max_exchanges=0))
    assert state["max_exchanges"] == topic_chat.DEFAULT_MAX_EXCHANGES
    _, state = start(Model(), max_turns=2)
    assert state["max_exchanges"] == 2
    assert state["hidden_resources"] == [0]


def test_the_opening_with_and_without_the_greeting():
    env, state = start(Model())
    assert get_skill("topic_chat.open")(env, state, SkillInput()).reply == "[open]"
    assert get_skill("topic_chat.open_direct")(env, state, SkillInput()).reply == "[open_direct]"


@pytest.mark.parametrize("kind", ["on_topic", "drifting", "avoid"])
def test_a_turn_that_goes_on_gets_the_line_of_its_kind(kind):
    model = Model(kind=kind)
    env, state = start(model)
    result = turn(env, state, "We danced in the square.")
    assert result.label == kind and state["exchanges"] == 1
    reply = get_skill("topic_chat.reply")(env, state, SkillInput(option=result.label))
    assert reply.reply == f"[{kind}]"
    assert state["history"][-2:] == [{"role": "user", "content": "We danced in the square."},
                                     {"role": "assistant", "content": f"[{kind}]"}]


def test_a_request_to_repeat_says_the_last_line_again_without_the_model():
    env, state = start(Model(kind="repeat"))
    get_skill("topic_chat.open")(env, state, SkillInput())
    result = turn(env, state, "Sorry?")
    assert (result.label, result.reply) == ("repeat", "[open]")
    assert state["exchanges"] == 0 and len(state["history"]) == 1


def test_an_unclear_turn_asks_to_repeat_and_is_kept_out():
    env, state = start(Model(kind="unclear"))
    result = turn(env, state, "mmh the")
    assert (result.label, result.reply) == ("unclear", "[unclear]")
    assert state["history"] == [] and state["exchanges"] == 0


def test_the_limit_of_exchanges_closes_on_the_last_turn():
    env, state = start(Model())
    labels = [turn(env, state, f"line {i}").label for i in range(4)]
    assert labels == ["on_topic", "on_topic", "on_topic", "limit"]


def test_the_limit_of_time_closes(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(topic_chat, "_now", lambda: clock[0])
    env, state = start(Model(), chat=dict(CHAT, max_minutes=5))
    assert turn(env, state, "first").label == "on_topic"
    clock[0] += 301
    assert turn(env, state, "second").label == "time"


def test_the_closing_records_the_result(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(topic_chat, "_now", lambda: clock[0])
    env, state = start(Model(kind="drifting", covered=[2, 7], topics=["the bakery"]))
    turn(env, state, "The government is a disaster.")
    clock[0] += 42
    result = get_skill("topic_chat.finish")(env, state, SkillInput(option="limit"))
    assert result.reply == "[bye]"
    data = get_mode("topic_chat").data(state)
    assert data["ended_by"] == "limit" and data["duration_s"] == 42
    assert data["covered"] == [2]
    assert data["questions_covered"] == ["What did you do on holidays?"]  # 7 does not exist
    assert data["questions_not_covered"] == ["What was the square like?"]
    assert data["topics"] == ["the bakery"] and data["drifts"] == 1
    assert get_mode("topic_chat").succeeded(state)


def test_a_request_to_end_closes_as_the_user_wants():
    env, state = start(Model(kind="end"))
    assert turn(env, state, "I'm tired, let's leave it here.").label == "end"
    assert state["exchanges"] == 0
    get_skill("topic_chat.finish")(env, state, SkillInput(option="user"))
    assert get_mode("topic_chat").data(state)["ended_by"] == "user"


def test_a_request_to_stop_is_the_normal_end_without_a_confirmation():
    # Nothing to collect: stopping and finishing are the same (DialogueMode.on_finished)
    env, state = start(Model())
    turn(env, state, "We danced in the square.")
    reply = get_mode("topic_chat").on_finished(env, state)
    assert reply == "[bye]" and state["completed"] and state["ended_by"] == "user"
    assert get_mode("topic_chat").on_cancel is None


def test_stopping_before_any_line_on_the_topic_is_a_fixed_goodbye():
    env, state = start(Model())
    reply = get_mode("topic_chat").on_finished(env, state)
    assert reply == env.llm.phrase("topic_chat_bye_early")
    assert state["completed"] and state["covered"] == [] and state["topics"] == []


# ==== The client's fallbacks ====

def templates() -> dict:
    import os

    import yaml

    path = os.path.join(os.path.dirname(__file__), "..", "config", "prompts.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["interaction_intelligence"]["ros__parameters"]["prompts"]


def client(answers):
    """A client whose model answers in order (None: no answer)."""
    queue = list(answers)

    def infer(sid, prompt, max_tokens, schema, think=False):
        return queue.pop(0) if queue else None

    return LLMDialogClient("nlu", "nlg", infer, PromptBuilder(templates()))


def test_the_client_reads_the_kind_or_says_unclear():
    chat = topic_chat.parse_topic([{"name": "t", "content": json.dumps(CHAT)}])[0]
    assert client(['{"kind": "drifting"}']).classify_topic_turn("x", chat, []) == "drifting"
    assert client(['{"kind": "chat"}', "no json"]).classify_topic_turn("x", chat, []) == "unclear"


def test_the_client_falls_back_to_fixed_lines():
    chat = topic_chat.parse_topic([{"name": "t", "content": json.dumps(CHAT)}])[0]
    llm = client([])
    assert llm.topic_chat_reply(chat, [], "on_topic") == llm.phrase("topic_chat_more")
    assert chat["topic"] in llm.topic_chat_reply(chat, [], "drifting")
    assert chat["topic"] in llm.topic_chat_opening("t", chat)
    assert llm.topic_chat_closing(chat, []) == llm.phrase("topic_chat_bye")
    assert llm.review_topic_chat(chat, []) == {"covered": [], "topics": []}


def test_the_client_keeps_one_question_and_no_question_in_the_closing():
    chat = topic_chat.parse_topic([{"name": "t", "content": json.dumps(CHAT)}])[0]
    line = client(["How lovely! Who was there? And what did you eat?"]).topic_chat_reply(
        chat, [], "on_topic")
    assert line == "How lovely! Who was there?"
    llm = client(["Thank you! Shall we talk again tomorrow?"])
    assert llm.topic_chat_closing(chat, []) == llm.phrase("topic_chat_bye")


def test_the_reply_prompt_says_what_to_do_with_each_kind():
    pb = PromptBuilder(templates())
    chat = topic_chat.parse_topic([{"name": "t", "content": json.dumps(CHAT)}])[0]
    drifting = pb.topic_chat_reply(chat, [], "drifting")
    avoid = pb.topic_chat_reply(chat, [], "avoid")
    assert "bridge back gently" in drifting and "do not go into it" in avoid
    # The per-turn instruction comes after the conversation: the cached prefix stays the same
    assert drifting[:drifting.cache_prefix_len] == avoid[:avoid.cache_prefix_len]
    assert "The death of her husband" in avoid and "Worked as a baker" in avoid


def test_a_line_that_repeats_an_earlier_question_is_asked_for_again_or_replaced():
    # A small model copied one of its own earlier lines word for word (development run with a
    # real model): asked for once more, then the fixed line
    chat = topic_chat.parse_topic([{"name": "t", "content": json.dumps(CHAT)}])[0]
    history = [{"role": "assistant", "content": "How lovely. What was it like up on the ladder?"},
               {"role": "user", "content": "A bit wobbly."}]
    again = "How lovely. What was it like up on that ladder?"
    new = "That sounds exciting. Who else picked the apples with you?"
    assert client([again, new]).topic_chat_reply(chat, history, "on_topic") == new
    c = client([again, again])
    assert c.topic_chat_reply(chat, history, "on_topic") == chat["questions"][0]
    c = client([again, again])
    no_questions = {**chat, "questions": []}
    assert c.topic_chat_reply(no_questions, history, "on_topic") == c.phrase("topic_chat_more")
