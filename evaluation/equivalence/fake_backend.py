# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
A stand-in for a model server that records the body of every request: Ollama's native API
and an OpenAI-compatible API, on one local port, answering at once.

    server = FakeBackend()            # 127.0.0.1, a free port
    server.start()
    server.label = "extract_turn/3"   # recorded with the next bodies
    ...                               # requests to server.url
    server.bodies                     # [{"label", "path", "body": bytes, "answer"}]
    server.stop()

Endpoints:
  GET  /api/tags                 200 (the gateway's Ollama provider checks it when it connects)
  POST /api/chat                 Ollama: streamed (NDJSON) or not, as the body asks
  GET  /v1/models/<model>        200 (the openai SDK's check of the model)
  POST /v1/chat/completions      OpenAI-compatible: streamed (SSE, with usage) or not

The answer depends only on the request: a request with a schema (Ollama `format`, OpenAI
`response_format`) gets `json_answer`, any other `text_answer`, so that two implementations that
send the same requests get the same answers and make the same calls. `answers` (a list) gives
the answers in order instead (the last one repeated), `delay_s` delays every answer and
`status` (other than 200) answers every request to the model with that HTTP error.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

JSON_ANSWER = "{}"
# A question (that a roleplay history can contain: the character's repeated question)
TEXT_ANSWER = "What would you like to drink?"


class FakeBackend:

    def __init__(self, host: str = "127.0.0.1", port: int = 0, json_answer: str = JSON_ANSWER,
                 text_answer: str = TEXT_ANSWER, delay_s: float = 0.0):
        self.json_answer = json_answer
        self.text_answer = text_answer
        self.answers: list[str] | None = None
        self.delay_s = delay_s
        # An HTTP status other than 200 answers every request to the model with an error
        self.status = 200
        self.label = ""
        self.bodies: list[dict] = []
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer((host, port), self._handler())
        self._server.daemon_threads = True
        self._thread = None

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "FakeBackend":
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def clear(self) -> None:
        with self._lock:
            self.bodies.clear()

    def _answer(self, has_schema: bool) -> str:
        with self._lock:
            if self.answers:
                calls = [b for b in self.bodies if b["path"].endswith(("chat", "completions"))]
                index = len(calls)
                return self.answers[min(index - 1, len(self.answers) - 1)]
        return self.json_answer if has_schema else self.text_answer

    def _record(self, path: str, body: bytes) -> dict:
        entry = {"label": self.label, "path": path, "body": body, "answer": None,
                 "t": time.monotonic()}
        with self._lock:
            self.bodies.append(entry)
        return entry

    def _handler(self):
        backend = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, code: int, data: bytes, content_type: str = "application/json"):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802 (the name of http.server's handler)
                if self.path.startswith("/api/tags"):
                    self._send(200, b'{"models": []}')
                elif self.path.startswith("/v1/models/"):
                    model = self.path[len("/v1/models/"):]
                    self._send(200, json.dumps({"id": model, "object": "model", "created": 0,
                                                "owned_by": "fake"}).encode())
                else:
                    self._send(404, b'{"error": "not found"}')

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                entry = backend._record(self.path, body)
                try:
                    payload = json.loads(body)
                except ValueError:
                    payload = {}
                if backend.delay_s:
                    time.sleep(backend.delay_s)
                if backend.status != 200 and self.path.startswith(("/api/chat", "/v1/chat")):
                    self._send(backend.status, b'{"error": "a failure of the model server"}')
                    return
                if self.path.startswith("/api/chat"):
                    answer = backend._answer("format" in payload)
                    entry["answer"] = answer
                    self._ollama(payload, answer)
                elif self.path.startswith("/v1/chat/completions"):
                    answer = backend._answer("response_format" in payload)
                    entry["answer"] = answer
                    self._openai(payload, answer)
                elif self.path.startswith("/api/generate"):
                    self._send(200, b'{"done": true}')
                else:
                    self._send(404, b'{"error": "not found"}')

            def _ollama(self, payload: dict, answer: str):
                model = payload.get("model", "")
                if not payload.get("stream"):
                    self._send(200, json.dumps({
                        "model": model, "message": {"role": "assistant", "content": answer},
                        "done": True, "prompt_eval_count": 10, "eval_count": 5}).encode())
                    return
                lines = [{"model": model, "message": {"role": "assistant", "content": answer},
                          "done": False},
                         {"model": model, "message": {"role": "assistant", "content": ""},
                          "done": True, "prompt_eval_count": 10, "eval_count": 5}]
                data = b"".join(json.dumps(line).encode() + b"\n" for line in lines)
                self._send(200, data, "application/x-ndjson")

            def _openai(self, payload: dict, answer: str):
                model = payload.get("model", "")
                usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
                if not payload.get("stream"):
                    self._send(200, json.dumps({
                        "id": "fake", "object": "chat.completion", "created": 0, "model": model,
                        "choices": [{"index": 0, "finish_reason": "stop", "message": {
                            "role": "assistant", "content": answer}}],
                        "usage": usage}).encode())
                    return
                chunks = [
                    {"id": "fake", "object": "chat.completion.chunk", "created": 0,
                     "model": model, "choices": [{"index": 0, "delta": {
                         "role": "assistant", "content": answer}, "finish_reason": None}]},
                    {"id": "fake", "object": "chat.completion.chunk", "created": 0,
                     "model": model, "choices": [{"index": 0, "delta": {},
                                                  "finish_reason": "stop"}]},
                    {"id": "fake", "object": "chat.completion.chunk", "created": 0,
                     "model": model, "choices": [], "usage": usage},
                ]
                data = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks)
                self._send(200, data + b"data: [DONE]\n\n", "text/event-stream")

        return Handler
