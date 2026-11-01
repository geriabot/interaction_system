# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The pilot's stand-in for the cloud providers (doc/PILOT.md): one small local server that speaks
Gemini's REST API (models/<model>:generateContent and :streamGenerateContent?alt=sse) and OpenAI's
Chat Completions (/v1/chat/completions, streamed or not, and /v1/models/<model>), the shapes that
the evaluation's client (interaction_sim/cloud_llm.py), the gateway's google-genai SDK and the
gateway's openai SDK send and read. With it the pilot runs every cloud code path of the
evaluation (requests, schemas, streaming, usage, model versions) without a paid call. Who answers
a request:

    the simulated user   (its prompt, interaction_sim/llm_user.py) a SYNTHETIC user: simple rules
                         on the scenario's goal and the robot's last line (give the value asked,
                         else the next goal item; answer a confirmation; decline a stop the
                         scenario does not plan; end when the robot says goodbye). The planned
                         events (silence, cancel, detours) are said by the simulator's own code,
                         as in every experiment. A dialogue that this user derails is a simulator
                         failure (S), never a program failure
    a model named as an Ollama model (it contains ":", e.g. qwen3:32b): that LOCAL model on the
                         Ollama server (/api/chat, reasoning off, the context of the experiments,
                         the schema as its format): the cloud SYSTEM cells of the pilot (E1's
                         OpenAI model, the role swap's Gemini model)
    anything else        a SYNTHETIC answer built from the request's schema, deterministic per
                         request (the judges: their labels only have to go through the chain)

    python3 pilot_standin.py serve [--port 8766] [--ollama http://127.0.0.1:11434] [--log FILE]
                                   [--user-model MODEL [--user-ollama URL]]

--user-model is the fallback if the script derails too many dialogues: a small local model plays
the simulated user (through the same client and server), next to the system's model on the GPU.

Every answer carries its usage (the tokens Ollama counted; for a synthetic answer, about four
characters per token of the real prompt and of the answer) and
a model version "<model>-pilot-standin"; every call is appended to --log (model, role, tokens),
which pilot_check.py reads. Listens on 127.0.0.1 only.
"""

import argparse
import glob
import hashlib
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

DEFAULT_PORT = 8766
NUM_CTX = 8192          # as the system's provider and the simulated user (cloud_llm.py)
SYNTHETIC_TEXT = "This is a synthetic answer of the pilot's stand-in."
VERSION_SUFFIX = "-pilot-standin"
# The simulated user's prompt (interaction_sim/llm_user.py, PROMPT) begins so
USER_MARKER = "You play a person talking to a social robot"


# ------------------------------------------------------------------ schemas
def json_schema(schema: dict | None) -> dict | None:
    """A Gemini responseSchema (OpenAPI types, upper case, propertyOrdering) as a plain JSON
    schema (lower-case types), e.g. for Ollama's `format`."""
    if not isinstance(schema, dict):
        return None
    out = {}
    for key, value in schema.items():
        if key == "type" and isinstance(value, str):
            out["type"] = value.lower()
        elif key == "properties" and isinstance(value, dict):
            out["properties"] = {k: json_schema(v) for k, v in value.items()}
        elif key == "items":
            out["items"] = json_schema(value)
        elif key in ("propertyOrdering", "property_ordering", "nullable"):
            continue
        else:
            out[key] = value
    return out


def synthetic(schema: dict | None, seed: str, path: str = "") -> object:
    """A value that satisfies the schema, chosen deterministically from the request (seed) and the
    property's path: varied (both booleans and every enum value occur) but the same for the same
    request."""
    schema = json_schema(schema) or {}
    digest = int(hashlib.sha256(f"{seed}|{path}".encode()).hexdigest()[:8], 16)
    kind = schema.get("type", "string")
    if schema.get("enum"):
        return schema["enum"][digest % len(schema["enum"])]
    if kind == "object":
        props = schema.get("properties") or {}
        return {k: synthetic(v, seed, f"{path}.{k}") for k, v in props.items()}
    if kind == "array":
        return [synthetic(schema.get("items") or {}, seed, f"{path}[0]")]
    if kind == "boolean":
        return digest % 4 != 0          # mostly true, sometimes false
    if kind == "integer":
        low = int(schema.get("minimum", 0))
        high = int(schema.get("maximum", low + 4))
        return low + digest % (high - low + 1)
    if kind == "number":
        return float(schema.get("minimum", 0.0)) + (digest % 100) / 100
    return "pilot"


# ------------------------------------------------------------------ the synthetic user
PHRASES = {
    "English": {"yes": "Yes, that's right.", "no_stop": "No, let's go on.",
                "unsure": "I'm not sure, sorry.", "ok": "Okay, I see. Thank you.",
                "change": "Actually, make it {value}.", "value": "{value}."},
    "Spanish": {"yes": "Sí, correcto.", "no_stop": "No, sigamos.",
                "unsure": "No estoy seguro, perdona.", "ok": "Vale, entendido. Gracias.",
                "change": "Mejor que sea {value}.", "value": "{value}."},
}
GOODBYE = re.compile(r"\b(good ?bye|bye|take care|have a (nice|good|great) (day|evening|one)|"
                     r"adi[oó]s|hasta (luego|pronto|otra)|que (tengas|vaya) )", re.I)
STOP_WORDS = re.compile(r"\b(stop|cancel|end (this|the)|quit|parar|paramos|cancelar|dejarlo|"
                        r"terminar)\b", re.I)
CONFIRM = re.compile(r"(is that (right|correct)|is this (right|correct)|correct\?|right\?|"
                     r"shall i|should i|can i confirm|do you confirm|¿es correcto|¿correcto|"
                     r"¿está bien|¿lo confirmo|¿confirmas|¿de acuerdo)", re.I)


def _section(prompt: str, start: str, end: str) -> str:
    i = prompt.find(start)
    if i < 0:
        return ""
    i += len(start)
    j = prompt.find(end, i)
    return prompt[i:j if j >= 0 else None].strip()


SCENARIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "experiments",
                            "scenarios")
_GOALS: dict | None = None


def goal_items_by_goal() -> dict:
    """{the user goal, normalised: its goal items}, from every scenario file. The simulated user
    no longer gets its goal items (it only speaks, decided by the author, 2026-10-31): the
    synthetic user finds them by the goal its prompt carries."""
    global _GOALS
    if _GOALS is None:
        import yaml
        _GOALS = {}
        for path in glob.glob(os.path.join(SCENARIO_DIR, "*.yaml")):
            with open(path, encoding="utf-8") as f:
                for sc in (yaml.safe_load(f) or {}).get("scenarios") or []:
                    if sc.get("user_goal") and sc.get("goal_items"):
                        _GOALS[_norm(" ".join(str(sc["user_goal"]).split()))] = [
                            {"id": str(i["id"]), "value": str(i["value"]),
                             **({"then": str(i["then"])} if i.get("then") is not None else {})}
                            for i in sc["goal_items"]]
    return _GOALS


def parse_user_prompt(prompt: str) -> dict:
    """What the synthetic user needs from the simulated user's prompt: the language, the goal
    items (id, value, then), what the user said before, the robot's last line."""
    lang = re.search(r"in (\w+)(?: \([^)]*\))?, with no stage\s+directions", prompt)
    items = []
    for m in re.finditer(r'^- ([^:\n]+): "([^"]*)"(?:, later[^"\n]*"([^"]*)")?$',
                         _section(prompt, "YOUR GOAL ITEMS", "With each line you say")
                         or _section(prompt, "nothing to declare about it)", "THE CONVERSATION SO FAR"),
                         re.M):
        items.append({"id": m.group(1).strip(), "value": m.group(2),
                      **({"then": m.group(3)} if m.group(3) is not None else {})})
    if not items:
        goal = " ".join(_section(prompt, "WHAT YOU WANT FROM THIS CONVERSATION",
                                 "HOW TO ANSWER").split())
        items = [dict(i) for i in goal_items_by_goal().get(_norm(goal), [])]
    said = [line[len("You: "):] for line in
            _section(prompt, "THE CONVERSATION SO FAR", "THE ROBOT JUST SAID").splitlines()
            if line.startswith("You: ")]
    robot = _section(prompt, "THE ROBOT JUST SAID", "\n\n").strip().strip('"')
    return {"language": lang.group(1) if lang else "English", "items": items, "said": said,
            "robot": robot}


def _norm(text: str) -> str:
    return re.sub(r"[^\w ]+", " ", text.lower())


def user_line(prompt: str) -> dict:
    """The synthetic user's answer (action, text, revealed) to the simulated user's prompt."""
    p = parse_user_prompt(prompt)
    words = PHRASES.get(p["language"], PHRASES["English"])
    robot, said = p["robot"], " ".join(_norm(s) for s in p["said"])
    if GOODBYE.search(robot):
        return {"action": "end", "text": "", "revealed": []}
    if robot.rstrip().endswith("?") and STOP_WORDS.search(robot):
        return {"action": "say", "text": words["no_stop"], "revealed": []}
    pending = [i for i in p["items"] if _norm(i["value"]).strip() not in said]
    if robot.rstrip().endswith("?") and CONFIRM.search(robot) and not any(
            any(w in _norm(robot) for w in _norm(i["id"]).replace("_", " ").split() if len(w) > 2)
            for i in pending):
        return {"action": "say", "text": words["yes"], "revealed": []}
    if pending:
        # The item the robot asks for: its id's words (ids are English in every language) or its
        # value among the options the robot offers; else the next one (the items are listed in
        # the order the conversation asks them)
        asked = [i for i in pending if any(
            w in _norm(robot) for w in _norm(i["id"]).replace("_", " ").split() if len(w) > 2)
            or re.search(rf"\b{re.escape(_norm(i['value']).strip())}\b", _norm(robot))]
        item = (asked or pending)[0]
        return {"action": "say", "text": words["value"].format(value=item["value"]),
                "revealed": [{"id": item["id"], "value": item["value"]}]}
    changes = [i for i in p["items"] if "then" in i and _norm(i["then"]).strip() not in said]
    if changes:
        item = changes[0]
        return {"action": "say", "text": words["change"].format(value=item["then"]),
                "revealed": [{"id": item["id"], "value": item["then"]}]}
    if robot.rstrip().endswith("?"):
        return {"action": "say", "text": words["unsure"], "revealed": []}
    return {"action": "say", "text": words["ok"], "revealed": []}


def fit_to_schema(answer: dict, schema: dict | None, seed: str) -> dict:
    """The synthetic user's answer with exactly the properties of the request's schema (no
    `revealed` when the scenario has no goal items), any other filled synthetically."""
    props = (json_schema(schema) or {}).get("properties")
    if not props:
        return answer
    return {k: answer[k] if k in answer else synthetic(v, seed, k) for k, v in props.items()}


# ------------------------------------------------------------------ requests
def gemini_request(body: dict) -> dict:
    """What a generateContent request asks: the system text, the messages (Gemini's roles user and
    model), the schema, the cap and the temperature. Both shapes are read: the REST one of
    cloud_llm.py and the SDK's (systemInstruction at the top or in generationConfig)."""
    config = body.get("generationConfig") or body.get("generation_config") or {}
    system = body.get("systemInstruction") or body.get("system_instruction") or config.get(
        "systemInstruction") or config.get("system_instruction")
    system_text = ""
    if isinstance(system, dict):
        system_text = "".join(p.get("text", "") for p in system.get("parts") or [])
    elif isinstance(system, str):
        system_text = system
    messages = []
    for content in body.get("contents") or []:
        text = "".join(p.get("text", "") for p in content.get("parts") or [])
        messages.append({"role": "assistant" if content.get("role") == "model" else "user",
                         "content": text})
    schema = (config.get("responseSchema") or config.get("response_schema")
              or config.get("responseJsonSchema") or config.get("response_json_schema"))
    return {"system": system_text, "messages": messages, "schema": schema,
            "json": (config.get("responseMimeType") or config.get("response_mime_type")) ==
            "application/json" or bool(schema),
            "max_tokens": int(config.get("maxOutputTokens") or config.get("max_output_tokens")
                              or 0),
            "temperature": config.get("temperature"), "seed": config.get("seed")}


def openai_request(body: dict) -> dict:
    """What a Chat Completions request asks, in the same form as gemini_request."""
    system, messages = "", []
    for m in body.get("messages") or []:
        content = m.get("content")
        if isinstance(content, list):
            content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        if m.get("role") in ("system", "developer"):
            system += content or ""
        else:
            messages.append({"role": "assistant" if m.get("role") == "assistant" else "user",
                             "content": content or ""})
    fmt = body.get("response_format") or {}
    schema = (fmt.get("json_schema") or {}).get("schema") if fmt.get("type") == "json_schema" \
        else None
    return {"system": system, "messages": messages, "schema": schema,
            "json": fmt.get("type") in ("json_schema", "json_object"),
            "max_tokens": int(body.get("max_completion_tokens") or body.get("max_tokens") or 0),
            "temperature": body.get("temperature"), "seed": body.get("seed")}


def ask_ollama(base: str, model: str, req: dict, timeout: float = 300) -> tuple[str, int, int]:
    """The answer of a local Ollama model to the request: (text, prompt tokens, output tokens)."""
    messages = ([{"role": "system", "content": req["system"]}] if req["system"] else []) + \
        req["messages"]
    options = {"num_ctx": NUM_CTX}
    if req["max_tokens"]:
        options["num_predict"] = req["max_tokens"]
    if req["temperature"] is not None:
        options["temperature"] = req["temperature"]
    if req["seed"] is not None:
        options["seed"] = req["seed"]
    payload = {"model": model, "messages": messages, "stream": False, "think": False,
               "options": options}
    if req["json"]:
        payload["format"] = json_schema(req["schema"]) or "json"
    r = requests.post(f"{base}/api/chat", json=payload, timeout=timeout)
    r.raise_for_status()
    out = r.json()
    return ((out.get("message") or {}).get("content") or "",
            int(out.get("prompt_eval_count") or 0), int(out.get("eval_count") or 0))


def role_of(model: str, req: dict) -> str:
    """Who answers: the synthetic user, a local model, or a synthetic answer."""
    text = req["system"] + "".join(m["content"] for m in req["messages"])
    if USER_MARKER in text:
        return "user"
    return "ollama" if ":" in model else "synthetic"


def answer(model: str, req: dict, ollama: str, user_model: str = "",
           user_ollama: str = "") -> tuple[str, str, int, int]:
    """(role, text, prompt tokens, output tokens) of a request. user_model: the simulated user is
    that local model instead of the script (the fallback, --user-model), on user_ollama (default:
    the same server)."""
    role = role_of(model, req)
    if role == "user" and user_model:
        return (role,) + ask_ollama(user_ollama or ollama, user_model, req)
    # A synthetic answer counts tokens as about four characters each (the prompt is the real one)
    prompt_tokens = max(1, round(len(req["system"] + "".join(m["content"]
                                                            for m in req["messages"])) / 4))
    seed = json.dumps([req["system"], req["messages"]], sort_keys=True)
    if role == "ollama":
        return (role,) + ask_ollama(ollama, model, req)
    if role == "user":
        prompt = req["messages"][-1]["content"] if req["messages"] else req["system"]
        answer = user_line(prompt)
        # Whether the line asks the robot for something (the simulator's `request` mark)
        answer.setdefault("request", "yes" if answer.get("text", "").rstrip().endswith("?")
                          else "no")
        text = json.dumps(fit_to_schema(answer, req["schema"], seed), ensure_ascii=False)
    else:
        text = (json.dumps(synthetic(req["schema"], seed)) if req["json"] else SYNTHETIC_TEXT)
    return role, text, prompt_tokens, max(1, round(len(text) / 4))


def gemini_response(model: str, text: str, prompt_tokens: int, output_tokens: int) -> dict:
    """A generateContent response, as Google's API shapes it."""
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]},
                            "finishReason": "STOP", "index": 0}],
            "usageMetadata": {"promptTokenCount": prompt_tokens,
                              "candidatesTokenCount": output_tokens,
                              "totalTokenCount": prompt_tokens + output_tokens},
            "modelVersion": model + VERSION_SUFFIX}


def openai_response(model: str, text: str, prompt_tokens: int, output_tokens: int) -> dict:
    return {"id": "chatcmpl-pilot", "object": "chat.completion", "created": int(time.time()),
            "model": model + VERSION_SUFFIX,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": output_tokens,
                      "total_tokens": prompt_tokens + output_tokens}}


def openai_stream(model: str, text: str, prompt_tokens: int, output_tokens: int,
                  usage: bool) -> str:
    """The same answer as Server-Sent Events of chat.completion.chunk objects."""
    base = {"id": "chatcmpl-pilot", "object": "chat.completion.chunk",
            "created": int(time.time()), "model": model + VERSION_SUFFIX}
    chunks = [{**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": text},
                                    "finish_reason": None}]},
              {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
    if usage:
        chunks.append({**base, "choices": [], "usage": {
            "prompt_tokens": prompt_tokens, "completion_tokens": output_tokens,
            "total_tokens": prompt_tokens + output_tokens}})
    return "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks) + \
        "data: [DONE]\n\n"


# ------------------------------------------------------------------ the server
class Handler(BaseHTTPRequestHandler):
    server_version = "PilotStandin/1"
    ollama = "http://127.0.0.1:11434"
    user_model = ""
    user_ollama = ""
    log_path = ""
    lock = threading.Lock()

    def log_message(self, fmt, *args):  # quiet: the calls go to the log file
        pass

    def _send(self, status: int, body, content_type: str = "application/json") -> None:
        data = (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _not_found(self, what: str = "Not found"):
        self._send(404, {"error": {"code": 404, "message": what, "status": "NOT_FOUND"}})

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        if path in ("", "/health"):
            return self._send(200, {"ok": True})
        if path.startswith("/v1/models/"):
            model = path[len("/v1/models/"):]
            return self._send(200, {"id": model, "object": "model", "created": 0,
                                    "owned_by": "pilot-standin"})
        return self._not_found()

    def do_POST(self):  # noqa: N802
        path, _, query = self.path.partition("?")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0))
                              or b"{}")
        except ValueError:
            return self._send(400, {"error": {"code": 400, "message": "Not JSON"}})
        if path.rstrip("/").endswith("/chat/completions"):
            return self._openai(body)
        last = path.rsplit("/", 1)[-1]
        if "/models/" in path and ":" in last:
            model, _, method = last.rpartition(":")
            if method in ("generateContent", "streamGenerateContent"):
                return self._gemini(model, method, body, query)
        return self._not_found()

    def _call(self, provider: str, model: str, req: dict) -> tuple | None:
        t0 = time.time()
        try:
            role, text, p_tok, o_tok = answer(model, req, self.ollama, self.user_model,
                                              self.user_ollama)
        except Exception as exc:  # the client sees a server error, as it would from the API
            self._send(500, {"error": {"code": 500, "message": f"{type(exc).__name__}: {exc}",
                                       "status": "INTERNAL"}})
            return None
        self._record({"time": t0, "provider": provider, "model": model, "role": role,
                      "prompt_tokens": p_tok, "completion_tokens": o_tok,
                      "seconds": round(time.time() - t0, 3)})
        return text, p_tok, o_tok

    def _gemini(self, model, method, body, query):
        got = self._call("gemini", model, gemini_request(body))
        if got is None:
            return None
        out = gemini_response(model, *got)
        if method == "streamGenerateContent":
            if "alt=sse" in query:
                return self._send(200, f"data: {json.dumps(out, ensure_ascii=False)}\r\n\r\n",
                                  "text/event-stream")
            return self._send(200, [out])
        return self._send(200, out)

    def _openai(self, body):
        model = body.get("model", "")
        got = self._call("openai", model, openai_request(body))
        if got is None:
            return None
        if body.get("stream"):
            usage = bool((body.get("stream_options") or {}).get("include_usage"))
            return self._send(200, openai_stream(model, *got, usage), "text/event-stream")
        return self._send(200, openai_response(model, *got))

    def _record(self, entry: dict) -> None:
        if not self.log_path:
            return
        with self.lock, open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def make_server(port: int, ollama: str = "http://127.0.0.1:11434", log_path: str = "",
                user_model: str = "", user_ollama: str = "") -> ThreadingHTTPServer:
    """The server on 127.0.0.1 (never another interface)."""
    handler = type("PilotHandler", (Handler,), {"ollama": ollama.rstrip("/"),
                                                "log_path": log_path, "user_model": user_model,
                                                "user_ollama": user_ollama.rstrip("/")})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--ollama", default="http://127.0.0.1:11434")
    p.add_argument("--log", default="", help="Append every call here (JSON lines)")
    p.add_argument("--user-model", default="",
                   help="The fallback: a local model plays the simulated user instead of the "
                        "script (e.g. gemma3:4b)")
    p.add_argument("--user-ollama", default="",
                   help="The Ollama server of --user-model (default: --ollama), e.g. another "
                        "computer's or this one's, so that it does not share the GPU")
    args = parser.parse_args()
    try:
        server = make_server(args.port, args.ollama,
                             os.path.abspath(args.log) if args.log else "", args.user_model,
                             args.user_ollama)
    except OSError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Cloud stand-in (Gemini and OpenAI shapes) on http://127.0.0.1:{args.port}",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
