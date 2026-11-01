# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
Run an experiment on the hardware of a profile: one run_experiment.py per lane of the profile,
all at once, each with its own ROS_DOMAIN_ID and log (results/<name>/lane_<lane>.log).

    python3 run_parallel.py experiments/e1_bounded_vs_llm_led.yaml experiments/hardware/single_gpu.yaml
        [--lanes local,cloud] [--models M1,M2] [--allow-cloud] [--dry-run] [--check-servers]
        [--force-busy] [--force-provenance] [--out DIR]

Each lane checks before running that its servers are free (no other work's models loaded, no
other GPU processes; --force-busy skips it, --force-provenance resumes a folder begun with other
code; --check-servers only checks, lane by lane, and exits
with status 3 if any is in use). --models replaces the system models of the experiment file
(passed to every lane, which selects among them as its profile says). Lanes that share an
Ollama server for their models slow each other down: their latencies are then not comparable (a
warning says so). Ctrl-C stops every lane (each in its own process group).
"""

import argparse
import os
import signal
import subprocess
import sys
import time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from experiment_files import load_experiment  # noqa: E402
EVAL = os.path.normpath(os.path.join(HERE, ".."))


def shared_servers(lanes: dict) -> list[str]:
    """The servers that more than one lane uses (for system models or the open simulator)."""
    users = {}
    for name, spec in lanes.items():
        for server in {spec["system"], spec.get("simulator", spec["system"])}:
            users.setdefault(server, []).append(name)
    return [f"{server} ({', '.join(names)})" for server, names in users.items()
            if len(names) > 1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("experiment")
    parser.add_argument("hardware")
    parser.add_argument("--lanes", default=None, help="Only these lanes (comma separated)")
    parser.add_argument("--out", default=None)
    parser.add_argument("--models", default=None,
                        help="Replace the experiment's system models (comma separated)")
    parser.add_argument("--allow-cloud", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check-servers", action="store_true",
                        help="Only check that the servers of each lane are free")
    parser.add_argument("--force-busy", action="store_true")
    parser.add_argument("--force-provenance", action="store_true")
    parser.add_argument("--force", action="store_true", help="Deprecated: --force-busy")
    args = parser.parse_args()

    exp = load_experiment(args.experiment)   # an experiment file, or a pilot (doc/PILOT.md)
    with open(args.hardware, encoding="utf-8") as f:
        hardware = yaml.safe_load(f)
    lanes = hardware.get("lanes", {})
    if args.lanes:
        lanes = {k: v for k, v in lanes.items() if k in args.lanes.split(",")}
    out = os.path.abspath(args.out or os.path.join(EVAL, "results", exp["name"]))
    os.makedirs(out, exist_ok=True)
    shared = shared_servers(lanes)
    if shared and not (args.dry_run or args.check_servers):
        print(f"Note: lanes share servers {shared}: if both run models there, their latencies "
              f"are not comparable")

    common = ["--hardware", os.path.abspath(args.hardware), "--out", out]
    if args.models:
        common += ["--models", args.models]
    if args.force:
        print("--force is deprecated: it means --force-busy now")
        args.force_busy = True
    for flag in ("allow_cloud", "dry_run", "check_servers", "force_busy", "force_provenance"):
        if getattr(args, flag):
            common.append("--" + flag.replace("_", "-"))
    procs = {}
    in_use = []
    for name, spec in lanes.items():
        cmd = [sys.executable, os.path.join(HERE, "run_experiment.py"),
               os.path.abspath(args.experiment), "--lane", name] + common
        env = dict(os.environ, ROS_DOMAIN_ID=str(spec["ros_domain_id"]))
        if args.dry_run or args.check_servers:
            print(f"lane {name}:", flush=True)
            if subprocess.run(cmd, env=env, check=False).returncode == 3:
                in_use.append(name)
            continue
        log = open(os.path.join(out, f"lane_{name}.log"), "a")
        procs[name] = (subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                        start_new_session=True), log)
        print(f"lane {name}: ROS_DOMAIN_ID {spec['ros_domain_id']}, log "
              f"{os.path.join(out, f'lane_{name}.log')}", flush=True)

    def stop(*_):
        for proc, _log in procs.values():
            try:
                os.killpg(proc.pid, signal.SIGINT)
            except ProcessLookupError:
                pass

    if in_use:
        sys.exit(3)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    failed = []
    while procs:
        for name, (proc, log) in list(procs.items()):
            code = proc.poll()
            if code is None:
                continue
            log.close()
            del procs[name]
            print(f"lane {name}: finished ({code})", flush=True)
            if code:
                failed.append(name)
        time.sleep(2)
    if failed:
        sys.exit(f"Lanes that failed: {failed} (see their logs)")


if __name__ == "__main__":
    main()
