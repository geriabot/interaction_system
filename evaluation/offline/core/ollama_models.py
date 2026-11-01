# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The models of the Ollama server for an unattended run of several models: wait for the server,
pull a missing model, read its size, load it, and remove it afterwards when the run pulled it
(disk is limited). The pull follows ensure_models of scripts/run_experiment.py (which cannot be
imported without a sourced ROS workspace); the models that were on the server before this work
are never removed (KEEP: the one list of every runner, scripts/run_e0a.py and run_e0b.py import
it).

A model stays in VRAM after its runs: the production provider loads it with keep_alive -1 (for
ever), the other requests with Ollama's default (5 minutes), and deleting it does not unload it.
Every runner unloads its models when done with them (unload: keep_alive 0, and wait until the
server lists it no more), so that nothing of this work stays on the GPU and the next run's check
of the server (run_experiment.py, busy_servers) does not take the previous model for other
work's.
"""

from __future__ import annotations

import json
import os
import re
import time

import requests

# Models that were on the server before this work: never removed (the one list of every runner)
KEEP = {"qwen2.5vl:32b", "llama3.2:3b", "gemma3:4b", "qwen2.5:3b"}
# The pull of a large model through a slow link can take an hour or more
PULL_TIMEOUT_S = 7200


def base_url(cfg: dict) -> str:
    llm = cfg.get("llm", {})
    return f"http://{llm.get('ollama_host', '127.0.0.1')}:{llm.get('ollama_port', 11434)}"


def server_up(base: str) -> bool:
    try:
        requests.get(f"{base}/api/tags", timeout=15).raise_for_status()
        return True
    except requests.RequestException:
        return False


def wait_for_server(base: str, minutes: float = 10.0) -> bool:
    """Whether the server answers within the given minutes (polled every 30 s)."""
    deadline = time.monotonic() + minutes * 60
    while True:
        if server_up(base):
            return True
        if time.monotonic() >= deadline:
            return False
        print(f"   the Ollama server at {base} does not answer; waiting...", flush=True)
        time.sleep(30)


def installed(base: str) -> set[str]:
    names = {m["name"] for m in requests.get(f"{base}/api/tags", timeout=30).json()["models"]}
    return names | {n.removesuffix(":latest") for n in names}


def pull(base: str, model: str) -> None:
    print(f"   pulling {model} on the server...", flush=True)
    r = requests.post(f"{base}/api/pull", json={"model": model, "stream": False},
                      timeout=PULL_TIMEOUT_S)
    r.raise_for_status()


def protected(model: str) -> bool:
    """A model this work must never remove: those of KEEP and any llama.cpp manifest."""
    return (model in KEEP or model.removesuffix(":latest") in KEEP
            or model.startswith("llamacpp/"))


def loaded(base: str) -> set[str]:
    """The models in the server's memory now (/api/ps), with and without ":latest"."""
    names = {m["name"] for m in requests.get(f"{base}/api/ps", timeout=15).json().get("models", [])}
    return names | {n.removesuffix(":latest") for n in names}


def gpu_fit(base: str, model: str) -> dict:
    """Whether the loaded model is entirely in GPU memory (no part offloaded to the CPU), from
    /api/ps right after it was loaded with the run's context: {size, size_vram, fits}; fits is
    None when the server does not list it. E0a only chooses a model that fits (the author)."""
    try:
        listed = requests.get(f"{base}/api/ps", timeout=15).json().get("models", [])
    except (requests.RequestException, ValueError):
        return {"size": None, "size_vram": None, "fits": None}
    for m in listed:
        if m.get("name") in (model, f"{model}:latest") or m.get("model") == model:
            size, vram = m.get("size"), m.get("size_vram")
            fits = (vram >= size) if isinstance(size, int) and isinstance(vram, int) else None
            return {"size": size, "size_vram": vram, "fits": fits}
    return {"size": None, "size_vram": None, "fits": None}


def unload(base: str, model: str, wait_s: float = 60.0) -> bool:
    """Free the model's memory on the server (keep_alive 0) and wait until /api/ps lists it no
    more (the unload is asynchronous). Whether it is out of memory; a model the server does not
    have (404) is not loaded either."""
    try:
        r = requests.post(f"{base}/api/generate", json={"model": model, "keep_alive": 0},
                          timeout=120)
        if r.status_code != 404:
            r.raise_for_status()
    except requests.RequestException as exc:
        print(f"   {model} could not be unloaded ({exc})", flush=True)
        return False
    deadline = time.monotonic() + wait_s
    while True:
        try:
            if model not in loaded(base):
                return True
        except requests.RequestException:
            return False
        if time.monotonic() >= deadline:
            print(f"   {model} is still in memory {wait_s:.0f} s after its unload", flush=True)
            return False
        time.sleep(1.0)


def unload_all(base: str, models) -> None:
    """Unload those of the models that are in memory now (never the others: they may be other
    work's)."""
    try:
        now = loaded(base)
    except requests.RequestException:
        return
    for model in sorted(set(models)):
        if model in now or model.removesuffix(":latest") in now:
            unload(base, model)


def remove(base: str, model: str) -> bool:
    """Unload the model and delete it from the server, unless it is protected. Whether it is
    gone: a model the server does not have (404: removed before, or a pull that failed) is gone
    too, so a resume after a crash does not stop here."""
    if protected(model):
        return False
    unload(base, model)
    r = requests.delete(f"{base}/api/delete", json={"model": model}, timeout=120)
    if r.status_code == 404:
        print(f"   {model} was not on the server", flush=True)
        return True
    r.raise_for_status()
    print(f"   removed {model} from the server", flush=True)
    return True


def load_pulled(path: str) -> list[str]:
    """The models a runner pulled and has not removed yet (persisted: a crash after a pull is
    cleaned by the resume)."""
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return list(json.load(f))
    return []


def save_pulled(path: str, pulled: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(set(pulled)), f, indent=1)
    os.replace(tmp, path)


# Models whose reasoning cannot be switched off in Ollama, only lowered: they get this level
THINK_LEVEL_ONLY = {"gpt-oss": "low"}


def _generate(base: str, model: str, think, num_ctx: int | None) -> requests.Response:
    options = {"num_predict": 1}
    if num_ctx:
        options["num_ctx"] = int(num_ctx)
    return requests.post(f"{base}/api/generate", json={
        "model": model, "prompt": "Hi", "stream": False, "think": think, "options": options},
        timeout=900)


def warm_up(base: str, model: str, think=False, num_ctx: int | None = None):
    """Load the model with the options of the run (so that the latency of the first case does
    not include the load) and return the `think` value it accepts: the one asked for, or, for a
    model that cannot switch its reasoning off (THINK_LEVEL_ONLY, or one that rejects
    think=false), the lowest level, "low"."""
    family = model.split(":", 1)[0].split("/")[-1]
    if think is False and family in THINK_LEVEL_ONLY:
        think = THINK_LEVEL_ONLY[family]
    r = _generate(base, model, think, num_ctx)
    if r.status_code >= 400 and think is False and "think" in r.text.lower():
        print(f"   {model} rejects think=false ({r.text.strip()[:120]}); using \"low\"",
              flush=True)
        think = "low"
        r = _generate(base, model, think, num_ctx)
    r.raise_for_status()
    return think


def size_from_show(info: dict) -> float | None:
    """The parameters of the model, in billions, from /api/show."""
    count = (info.get("model_info") or {}).get("general.parameter_count")
    if isinstance(count, (int, float)) and count > 0:
        return round(count / 1e9, 2)
    return parse_size((info.get("details") or {}).get("parameter_size", ""))


def parse_size(text: str) -> float | None:
    """"7.6B" -> 7.6, "500M" -> 0.5, "qwen2.5:14b" -> 14.0 (the tag after the colon)."""
    text = (text or "").strip().lower()
    if ":" in text:
        text = text.split(":", 1)[1]
    m = re.search(r"(\d+(?:\.\d+)?)\s*([bm])\b", text) or re.search(r"(\d+(?:\.\d+)?)([bm])", text)
    if not m:
        return None
    value = float(m.group(1))
    return round(value / 1000 if m.group(2) == "m" else value, 2)


def show_size(base: str, model: str) -> float | None:
    try:
        r = requests.post(f"{base}/api/show", json={"model": model}, timeout=60)
        r.raise_for_status()
        return size_from_show(r.json())
    except requests.RequestException:
        return None
