# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Load the model of the former gateway (its ollama.model) into the Ollama server's memory when
the interaction system starts, as that gateway did (OllamaProvider.ensure_model_ready): an
empty prompt that asks Ollama to keep the model loaded (keep_alive -1) with the context length
of the requests, so that the first question of the first dialogue does not wait for the model
to load. Up to three attempts, one and two seconds apart; it exits when the model is loaded or
the attempts are spent. The launch file starts it when the Ollama provider is enabled.

    interpretation_warmup --url http://127.0.0.1:11434 --model qwen2.5:7b [--num-ctx 8192]
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def warm_up(url: str, model: str, num_ctx: int = 0, timeout: float = 180.0) -> tuple[bool, str]:
    payload = {"model": model, "prompt": "", "keep_alive": -1}
    if num_ctx:
        # Loaded with the context of the requests (otherwise the first one reloads it)
        payload["options"] = {"num_ctx": num_ctx}
    body = json.dumps(payload).encode()
    error = "unknown error"
    for attempt in range(3):
        request = urllib.request.Request(url.rstrip("/") + "/api/generate", data=body,
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response.read()
                return True, f"Model '{model}' loaded into memory."
        except urllib.error.HTTPError as exc:
            return False, f"Load failed: HTTP {exc.code}"
        except (urllib.error.URLError, OSError) as exc:
            error = str(exc)
            if attempt < 2:
                time.sleep(2 ** attempt)
    return False, f"Failed after 3 attempts: {error}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--num-ctx", type=int, default=0)
    args, _ = parser.parse_known_args(argv)  # the launch may add --ros-args
    ok, message = warm_up(args.url, args.model, args.num_ctx)
    print(f"[interpretation_warmup] {message}", file=sys.stdout if ok else sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
