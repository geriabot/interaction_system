# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Quick checks of the system without hardware (no microphone, speaker or robot), before running
experiments; nothing is stored. The speech capability is the simulation harness
(main.launch.py speech:=none), as in the experiments.

    python3 sim_checks.py start                    # the system starts, bounded and LLM-led
    python3 sim_checks.py scripted                 # one dialogue with the scripted LLM (no model)
    python3 sim_checks.py model experiments/e1_bounded_vs_llm_led.yaml --model qwen2.5:14b [--condition C]
                         [--hardware H --lane L] [--language es] [--allow-cloud] [--force]

  start     launches the system twice, with the method's trees (bounded) and with the monolithic
            ablation's (LLM-led), with the scripted LLM and the LLM gateway without providers,
            and checks that /dialog/execute_task is served;
  scripted  one coffee order with the scripted LLM (interaction_sim.scripted_llm) and fixed user
            lines, as the system tests do: the dialogue must succeed with the expected frame;
  model     one coffee order (the scenario sf_coffee) with a system model of an experiment, in
            one of its conditions, and fixed user lines (no simulated user model): the dialogue
            must run with the model answering (whether it succeeds with fixed lines is shown,
            not required). With --hardware/--lane, the servers of the lane must be free.

The last line is the verdict, read by the control panel: CHECK <name>: OK | FAILED - <why>.
Needs a sourced workspace and a ROS_DOMAIN_ID that nothing else uses.
"""

import argparse
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from run_experiment import (EVAL, System, apply_hardware, busy_servers, load,  # noqa: E402
                            load_hardware, make_goal, split_model)

SCRIPTED = {"llm_manager_node": {"ros__parameters": {"ollama": {"enable": False}}},
            "interaction_intelligence": {"ros__parameters": {
                "llm_client_factory": "interaction_sim.scripted_llm:make"}}}
LLM_LED = {"ablation": "monolithic", "skill_modules": ["interaction_ablations.skills"]}
# The happy path of the system tests (test_dialogues.py): the scripted LLM reads "slot=value"
DRINKS = ('{"slots": [{"name": "drink", "type": "str", "canonical_values": ["coffee", "tea"]}, '
          '{"name": "size", "type": "str"}]}')
LINES = {"English": ["I'd like a medium latte with oat milk, please", "My name is Laura",
                     "Yes, that's right", "Yes"],
         "Spanish": ["Quiero un café con leche mediano, con leche de avena", "Me llamo Laura",
                     "Sí, está bien", "Sí"]}


def verdict(name: str, ok: bool, detail: str) -> int:
    print(f"\nCHECK {name}: {'OK' if ok else 'FAILED'} - {' '.join(str(detail).split())}", flush=True)
    return 0 if ok else 1


class Printed:
    """Fixed user lines that print the conversation as it goes."""

    def __init__(self, lines):
        from interaction_sim.harness import ScriptedUser
        self.user = ScriptedUser(lines)

    def __call__(self, robot_utterance, transcript):
        print(f"ROBOT: {robot_utterance}", flush=True)
        turn = self.user(robot_utterance, transcript)
        if turn is not None and turn.text is not None:
            print(f"USER: {turn.text}", flush=True)
        return turn


def started(harness, exp, model, condition, out, timeout=120.0):
    """The system launched (System of run_experiment.py) once /dialog/execute_task is served."""
    system = System(exp, model, condition, out)
    try:
        if not harness.node.wait_for_system(timeout):
            system.stop()
            return None
        time.sleep(3.0)    # the intelligence's services
    except KeyboardInterrupt:
        system.stop()
        raise
    return system


def check_start(harness, out) -> tuple[bool, str]:
    results, ok = [], True
    for name, condition in (("bounded", {}), ("llm_led", LLM_LED)):
        print(f">>> Starting the system: {name}", flush=True)
        t0 = time.monotonic()
        system = started(harness, {"language": "English"}, "scripted",
                         {**condition, "params": SCRIPTED}, os.path.join(out, name))
        if system is None:
            results.append(f"{name}: did not start in 120 s")
            ok = False
            continue
        results.append(f"{name}: up in {time.monotonic() - t0:.0f} s")
        print(f">>> {results[-1]}; stopping it", flush=True)
        system.stop()
        # Until the action server is gone, so that the next start is not taken for this one
        end = time.monotonic() + 20.0
        while harness.node._dialog_client.server_is_ready() and time.monotonic() < end:
            time.sleep(0.5)
    return ok, "; ".join(results)


def check_scripted(harness, out) -> tuple[bool, str]:
    from ros2_dialog_interfaces.action import DialogTask
    system = started(harness, {"language": "English"}, "scripted", {"params": SCRIPTED}, out)
    if system is None:
        return False, "the system did not start in 120 s"
    try:
        goal = DialogTask.Goal(task_description="Take a drink order", dialog_mode="slot_filling",
                               frame_schema_json=DRINKS)
        run = harness.node.run(goal, Printed(["drink=coffee", "size=large", "yes"]), timeout=60.0)
    finally:
        system.stop()
    frame = run.result.final_frame_json if run.result else ""
    ok = run.status == "succeeded" and '"coffee"' in frame and '"large"' in frame
    return ok, f"{run.status}, {len(run.robot_lines())} robot lines, frame {frame or '-'}"


def check_model(harness, args, out) -> tuple[bool, str]:
    exp = load(os.path.abspath(args.experiment))
    if args.hardware:
        hardware = load_hardware(os.path.abspath(args.hardware))
        exp = apply_hardware(exp, hardware, args.lane)
    if args.model not in exp["models"]:
        return False, f"{args.model} is not a model of {exp['name']}"
    condition = (exp["conditions"].get(args.condition) or {}) if args.condition else {}
    if args.condition and args.condition not in exp["conditions"]:
        return False, f"{args.condition} is not a condition of {exp['name']}"
    if "<pin>" in args.model:
        return False, "write the dated version of the model in the experiment"
    if split_model(args.model)[0] != "ollama" and not args.allow_cloud:
        return False, "a cloud model: paid calls need --allow-cloud"
    if args.hardware and not args.force:
        busy = busy_servers(hardware, args.lane, [args.model], {args.condition or "": condition},
                            {"provider": "none"})
        if busy:
            return False, "the servers are not free: " + "; ".join(busy)
    language = {"es": "Spanish", "en": "English"}[args.language]
    exp["language"] = language
    scenarios = load(os.path.join(EVAL, "experiments", "scenarios",
                                  "dialogues_es.yaml" if args.language == "es" else "dialogues.yaml"))
    scenario = next(s for s in scenarios["scenarios"] if s["id"] == "sf_coffee")
    print(f">>> Starting the system: {args.model}, {args.condition or 'bounded'}", flush=True)
    system = started(harness, exp, args.model, condition, out)
    if system is None:
        return False, "the system did not start in 120 s"
    try:
        run = harness.node.run(make_goal(scenario, exp), Printed(LINES[language]), timeout=300.0)
    finally:
        system.stop()
    answered = sum(1 for s in run.skills if s.get("success"))
    ok = run.status not in ("timeout", "rejected", "") and answered > 0
    return ok, (f"{run.status}, {len(run.robot_lines())} robot lines, {answered} of "
                f"{len(run.skills)} questions to the model answered"
                + (f", frame {run.result.final_frame_json}" if run.result else ""))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("check", choices=("start", "scripted", "model"))
    parser.add_argument("experiment", nargs="?", help="model: the experiment file")
    parser.add_argument("--model", help="model: the system model")
    parser.add_argument("--condition", default="", help="model: a condition of the experiment "
                                                        "(default: the method, bounded)")
    parser.add_argument("--hardware", default=None)
    parser.add_argument("--lane", default=None)
    parser.add_argument("--language", choices=("en", "es"), default="en")
    parser.add_argument("--allow-cloud", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--keep", action="store_true", help="Keep the logs of the check")
    args = parser.parse_args()
    if args.check == "model" and not (args.experiment and args.model):
        parser.error("model needs the experiment and --model")
    if args.hardware and not args.lane:
        parser.error("--hardware needs --lane")

    from interaction_sim.harness import HarnessRunner
    out = tempfile.mkdtemp(prefix=f"sim_check_{args.check}_")
    harness = HarnessRunner()
    try:
        if args.check == "start":
            ok, detail = check_start(harness, out)
        elif args.check == "scripted":
            ok, detail = check_scripted(harness, out)
        else:
            ok, detail = check_model(harness, args, out)
    except KeyboardInterrupt:
        ok, detail = False, "cancelled"
    finally:
        harness.close()
    if args.keep or not ok:
        print(f"The logs are in {out}")
    else:
        shutil.rmtree(out, ignore_errors=True)
    return verdict(args.check, ok, detail)


if __name__ == "__main__":
    sys.exit(main())
