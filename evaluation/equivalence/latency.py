# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Criterion 6: the latency that the system's own code adds to a skill, with an instant model,
through the former gateway and through the interpretation server; and, on the way, the bodies
that reach the model through the ROS nodes of each path (criterion 3, end to end).

Two systems are started one after the other, in the ROS domain of the environment (use one of
your own: ROS_DOMAIN_ID), against a stand-in of Ollama that answers at once
(fake_backend.py):

  gateway         the interaction intelligence of another checkout (--main-src, e.g. a
                  checkout of main, whose node asks /llm/inference) with ros2_llm_manager's
                  llm_manager_node, as main.launch.py started them
  interpretation  the installed interaction intelligence with behavior_architecture's
                  interpretation_server, configured as interaction_system.launch.py configures it

Each round asks the intelligence, as the trees do: begin (a slot filling with its form; then
slot_filling.prepare, not timed), slot_filling.opening (one generated line), cancel_check (one
understanding question) and slot_filling.update (an extraction and a reply), and times each
request from the client's side.

    ROS_DOMAIN_ID=88 python3 latency.py --main-src ~/main-checkout [--rounds 100] [--out FILE]
"""

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from transports import PROMPTS, use_sources  # noqa: E402

SCHEMA = ('{"slots": [{"name": "drink", "type": "str", "canonical_values": ["latte", "tea"]},'
          ' {"name": "size", "type": "str"}]}')
SKILLS = ("begin", "slot_filling.opening", "cancel_check", "slot_filling.update")


def gateway_params(url: str) -> dict:
    host, port = url.rsplit("//", 1)[1].split(":")
    gateway = {"default_provider": "ollama", "default_model": "", "max_concurrent": 4,
               "silent_mode": True,
               "ollama": {"enable": True, "host": host, "port": int(port),
                          "model": "qwen2.5:7b", "timeout": 120.0, "think": False,
                          "num_ctx": 8192}}
    intelligence = {"language": "English", "conversation_log_path": "", "silent_mode": True}
    return {"llm_manager_node": {"ros__parameters": gateway},
            "interaction_intelligence": {"ros__parameters": intelligence}}


def start(path: str, params_file: str, main_src: str, tmp: str, log) -> list:
    procs = []
    if path == "gateway":
        procs.append(subprocess.Popen(
            ["ros2", "run", "ros2_llm_manager", "llm_manager_node", "--ros-args",
             "--params-file", params_file], stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True))
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.join(main_src, "interaction_system_core",
                                         "interaction_intelligence") + os.pathsep + \
            env.get("PYTHONPATH", "")
        procs.append(subprocess.Popen(
            [sys.executable, "-c",
             "from interaction_intelligence.intelligence_node import main; main()",
             "--ros-args", "-r", "__node:=interaction_intelligence",
             "--params-file", PROMPTS, "--params-file", params_file],
            stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True))
        return procs
    from ament_index_python.packages import get_package_share_directory
    from interaction_intelligence.interpretation_config import server_parameters
    import yaml
    share = get_package_share_directory("interaction_intelligence")
    parameters, _ = server_parameters(
        [params_file], os.path.join(share, "config", "interpretation", "questions.yaml"),
        PROMPTS, out_dir=tmp)
    server_file = os.path.join(tmp, "server.yaml")
    with open(server_file, "w", encoding="utf-8") as f:
        yaml.safe_dump({"/**/interpretation_server": {"ros__parameters": parameters}}, f)
    procs.append(subprocess.Popen(
        ["ros2", "run", "behavior_architecture", "interpretation_server", "--ros-args",
         "-r", "__ns:=/interaction", "--params-file", server_file],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
    procs.append(subprocess.Popen(
        ["ros2", "run", "interaction_intelligence", "interaction_intelligence", "--ros-args",
         "--params-file", PROMPTS, "--params-file", params_file],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
    return procs


def stop(procs: list) -> None:
    import signal
    for proc in procs:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
                proc.wait(timeout=5)
                break
            except (ProcessLookupError, subprocess.TimeoutExpired):
                continue


def measure(rounds: int, warmup: int = 3) -> dict:
    """{skill: [seconds]} of `rounds` rounds of the skills (after `warmup` rounds)."""
    import rclpy
    from interaction_system_interfaces.srv import AskIntelligence

    rclpy.init()
    node = rclpy.create_node("latency_client")
    client = node.create_client(AskIntelligence, "/interaction/ask_intelligence")
    if not client.wait_for_service(timeout_sec=30.0):
        raise RuntimeError("The intelligence did not start")

    def ask(skill, state, user_text="", args=None, dialogue="d"):
        request = AskIntelligence.Request(dialogue_id=dialogue, skill=skill, state=state,
                                          user_text=user_text, option="", number=0,
                                          args=json.dumps(args or {}), budget_s=20.0)
        started = time.monotonic()
        future = client.call_async(request)
        rclpy.spin_until_future_complete(node, future, timeout_sec=60.0)
        elapsed = time.monotonic() - started
        response = future.result()
        if response is None or not response.success:
            raise RuntimeError(f"{skill}: {getattr(response, 'message', 'no response')}")
        return response, elapsed

    times = {skill: [] for skill in SKILLS}
    replies = []
    try:
        for n in range(warmup + rounds):
            dialogue = f"latency_{n}"
            goal = {"task_description": "Take a coffee order", "frame_schema_json": SCHEMA,
                    "dialog_mode": "slot_filling", "wait_timeout": 15.0}
            response, t0 = ask("begin", "", args=goal, dialogue=dialogue)
            response, _ = ask("slot_filling.prepare", response.state, dialogue=dialogue)
            state = response.state
            response, t1 = ask("slot_filling.opening", state, dialogue=dialogue)
            state = response.state
            response, t2 = ask("cancel_check", state, "I think I would rather do it later",
                               dialogue=dialogue)
            state = response.state
            response, t3 = ask("slot_filling.update", state, "A latte, large",
                               dialogue=dialogue)
            replies.append(response.reply)
            if n >= warmup:
                for skill, t in zip(SKILLS, (t0, t1, t2, t3)):
                    times[skill].append(t)
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return {"times": times, "replies": replies}


def run_path(path: str, main_src: str, rounds: int) -> dict:
    """Start one path's nodes and the stand-in in a subprocess-free way, measure, stop."""
    from fake_backend import FakeBackend
    fake = FakeBackend(json_answer='{"act": "values", "extractions": [], "about": '
                                   '"conversation", "request": "none"}',
                       text_answer="Hello! What would you like to drink?").start()
    tmp = tempfile.mkdtemp(prefix=f"latency_{path}_")
    params_file = os.path.join(tmp, "params.yaml")
    import yaml
    with open(params_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(gateway_params(fake.url), f)
    log = open(os.path.join(tmp, "nodes.log"), "w")
    procs = start(path, params_file, main_src, tmp, log)
    try:
        time.sleep(3.0)
        result = measure(rounds)
    finally:
        stop(procs)
        log.close()
        fake.stop()
    result["bodies"] = [b["body"].decode("utf-8") for b in fake.bodies
                        if b["path"].endswith("chat")]
    shutil.rmtree(tmp, ignore_errors=True)
    return result


def summary(times: list[float]) -> dict:
    ordered = sorted(times)
    p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
    return {"median_ms": round(statistics.median(ordered) * 1000, 2),
            "p95_ms": round(p95 * 1000, 2), "n": len(ordered)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--main-src", required=True,
                        help="a checkout of the former code (its interaction intelligence)")
    parser.add_argument("--rounds", type=int, default=100)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    if not os.environ.get("ROS_DOMAIN_ID"):
        print("Set ROS_DOMAIN_ID to a domain of your own", file=sys.stderr)
        return 2
    use_sources(None)
    results = {path: run_path(path, os.path.abspath(args.main_src), args.rounds)
               for path in ("gateway", "interpretation")}
    report = {path: {skill: summary(r["times"][skill]) for skill in SKILLS}
              for path, r in results.items()}
    same_bodies = results["gateway"]["bodies"] == results["interpretation"]["bodies"]
    report["bodies"] = {"gateway": len(results["gateway"]["bodies"]),
                        "interpretation": len(results["interpretation"]["bodies"]),
                        "identical": same_bodies}
    report["replies_identical"] = results["gateway"]["replies"] == \
        results["interpretation"]["replies"]
    print(json.dumps(report, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    return 0 if same_bodies else 1


if __name__ == "__main__":
    sys.exit(main())
