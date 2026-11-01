# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The provenance of a run: what code, prompts and models produced its results.

record(out, models, ollama) writes <out>/provenance.json the first time a results folder is used,
and on every resume checks that the code is the same: a folder holds the results of one version
only (resuming with changed code would mix two systems in one experiment). The code hash covers
the sources of the system and of the evaluation (Python, YAML, XML, JSON, prompts), not the
results; the model digests come from the Ollama server.
"""

import datetime
import glob
import hashlib
import json
import os
import subprocess
import sys

import requests

RESULTS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results"))
CATALOG = os.path.join(RESULTS, "CATALOG.jsonl")
REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
# The sources that define the system and the evaluation
SOURCES = ["interaction_system_core", "interaction_system_behaviors", "ros2_llm_manager",
           "evaluation/scripts", "evaluation/analysis", "evaluation/offline/core",
           "evaluation/offline/benchmarks", "evaluation/offline/datasets",
           "evaluation/offline/run_eval.py", "evaluation/offline/run_models.py",
           "evaluation/offline/config_ollama.yaml", "evaluation/offline/e0a_candidates.txt",
           "evaluation/simulation/interaction_sim/interaction_sim", "evaluation/ablations",
           "evaluation/experiments"]
EXTENSIONS = (".py", ".yaml", ".yml", ".xml", ".json", ".txt", ".cpp", ".hpp", ".action", ".msg")


def code_hash() -> str:
    """SHA-256 over the source files (path and content), in a fixed order."""
    files = []
    for entry in SOURCES:
        path = os.path.join(REPO, entry)
        if os.path.isfile(path):
            files.append(path)
        else:
            files += [p for p in glob.glob(os.path.join(path, "**", "*"), recursive=True)
                      if os.path.isfile(p) and p.endswith(EXTENSIONS)
                      and "__pycache__" not in p and "/test" not in p]
    digest = hashlib.sha256()
    for p in sorted(set(files)):
        digest.update(os.path.relpath(p, REPO).encode())
        with open(p, "rb") as f:
            digest.update(f.read())
    return digest.hexdigest()


def git_state() -> dict:
    def git(*args):
        try:
            return subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True,
                                  timeout=30).stdout.strip()
        except Exception:
            return ""
    return {"head": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}


def model_digests(models: list[str], ollama: str | None) -> dict:
    """The digest of each local model on the Ollama server (a tag can be re-pushed)."""
    if not ollama:
        return {}
    try:
        tags = requests.get(f"{ollama}/api/tags", timeout=30).json().get("models", [])
    except Exception:
        return {}
    have = {m["name"]: m.get("digest", "") for m in tags}
    return {m: have.get(m) or have.get(f"{m}:latest") for m in models if "/" not in m}


# Packages whose version can change what a run produces
PACKAGES = ("requests", "httpx", "PyYAML", "numpy", "google-genai", "openai", "anthropic")


def environment(ollama: str | None = None) -> dict:
    """What else shapes the results: the Ollama server's version (/api/version), the Ollama
    settings of this computer's environment (the server's own, e.g. OLLAMA_CONTEXT_LENGTH, cannot
    be read through its API: recorded as unknown; every request sends its num_ctx anyway), the
    Python version and the versions of the key packages."""
    import platform
    from importlib import metadata

    env = {"python": platform.python_version(), "packages": {},
           "runner_ollama_env": {k: v for k, v in os.environ.items() if k.startswith("OLLAMA_")},
           "server_ollama_env": "unknown (not exposed by the Ollama API; requests send num_ctx)"}
    for name in PACKAGES:
        try:
            env["packages"][name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    if ollama:
        try:
            env["ollama_version"] = requests.get(f"{ollama}/api/version",
                                                 timeout=15).json().get("version", "")
        except Exception:
            env["ollama_version"] = "unknown (no answer)"
    return env


def record(out: str, models: list[str] | None = None, ollama: str | None = None,
           force: bool = False) -> dict:
    """Write <out>/provenance.json, or check it on a resume. force: resume anyway (the change is
    recorded in the file's history)."""
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "provenance.json")
    now = {"code_hash": code_hash(), "git": git_state(),
           "models": model_digests(models or [], ollama),
           "command": " ".join(sys.argv),
           "time": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "environment": environment(ollama)}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            prov = json.load(f)
        if prov["code_hash"] != now["code_hash"] and not force:
            sys.exit(f"{out} holds results of another version of the code (provenance.json): "
                     f"use a new folder, or --force to resume anyway")
        prov.setdefault("resumes", []).append(now)
        for model, digest in now["models"].items():
            known = prov.get("models", {}).get(model)
            if known and digest and known != digest and not force:
                sys.exit(f"{model} changed on the server since this folder began ({known[:12]} -> "
                         f"{digest[:12]}): use a new folder, or --force")
            prov.setdefault("models", {}).setdefault(model, digest)
    else:
        prov = now
    with open(path, "w", encoding="utf-8") as f:
        json.dump(prov, f, indent=1)
    catalog("resume" if prov is not now else "start", out,
            {"code_hash": now["code_hash"], "git_head": now["git"]["head"],
             "git_dirty": now["git"]["dirty"], "models": sorted(now["models"])})
    return prov


def catalog(event: str, folder: str, details: dict | None = None) -> None:
    """One line in evaluation/results/CATALOG.jsonl (git-ignored, private) for every run that
    starts, resumes or finishes, every decision and every human review saved: what, where, when,
    with which code. The index of all the results, to find and analyse them later."""
    os.makedirs(RESULTS, exist_ok=True)
    entry = {"time": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
             "event": event, "folder": os.path.relpath(os.path.abspath(folder), RESULTS),
             "command": " ".join(sys.argv), **(details or {})}
    with open(CATALOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    # python3 provenance.py: the catalog, one line per event
    if os.path.exists(CATALOG):
        with open(CATALOG, encoding="utf-8") as f:
            for line in f:
                e = json.loads(line)
                print(f"{e['time']}  {e['event']:8s} {e['folder']:40s} {e.get('code_hash', '')[:12]}  "
                      f"{', '.join(e.get('models', []))}")


def add_models(out: str, models: list[str], ollama: str | None) -> None:
    """Record the digests of models pulled after the run began (checked against the first
    digest recorded for each). A folder without provenance.json gets one (e.g. the judges' labels
    of E0b, written next to dialogues recorded before)."""
    path = os.path.join(out, "provenance.json")
    if not os.path.exists(path):
        os.makedirs(out, exist_ok=True)
        prov = {"models": {}}
    else:
        with open(path, encoding="utf-8") as f:
            prov = json.load(f)
    for model, digest in model_digests(models, ollama).items():
        known = prov.setdefault("models", {}).get(model)
        if known and digest and known != digest:
            sys.exit(f"{model} changed on the server since this folder began ({known[:12]} -> "
                     f"{digest[:12]}): use a new folder")
        prov["models"][model] = known or digest
    with open(path, "w", encoding="utf-8") as f:
        json.dump(prov, f, indent=1)
