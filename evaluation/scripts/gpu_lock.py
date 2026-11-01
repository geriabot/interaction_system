# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
One GPU campaign at a time: E0a (phases A and B) and E0b (dialogues, local judges) use the same
GPU server, the same lane (local) and the same ROS_DOMAIN_ID; two of them at once would mix their
dialogues and latencies. run_e0a.py and run_e0b.py hold this lock while they use the GPU:
evaluation/results/.gpu_run.lock, with the PID and the command of the holder. A lock whose PID
is gone (a crash, a reboot) is stale and taken over.

    with gpu_lock.held("run_e0a.py phase-b"):
        ...
"""

import contextlib
import json
import os
import sys
import time

RESULTS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                        "results"))
LOCK = os.path.join(RESULTS, ".gpu_run.lock")


def alive(pid: int) -> bool:
    """Whether a process with this PID exists (one of another user counts as alive)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def holder(path: str = LOCK) -> dict | None:
    """The holder of the lock ({pid, command, since}) if it is alive; None if there is no lock or
    it is stale."""
    try:
        with open(path, encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        return None
    return info if alive(int(info.get("pid", 0))) else None


def acquire(what: str, path: str = LOCK) -> None:
    """Take the lock, or exit with who holds it. A stale lock is taken over."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    info = {"pid": os.getpid(), "command": what or " ".join(sys.argv),
            "since": time.strftime("%Y-%m-%d %H:%M:%S")}
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            other = holder(path)
            if other and other["pid"] != os.getpid():
                sys.exit(f"Another GPU campaign is running (PID {other['pid']}: "
                         f"{other.get('command')}, since {other.get('since')}): E0a and E0b use "
                         f"the same GPU, lane and ROS_DOMAIN_ID; run them one after the other "
                         f"({path})")
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)  # stale (or ours): taken over
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(info, f)
        return
    sys.exit(f"Could not take the GPU lock {path}")


def release(path: str = LOCK) -> None:
    """Remove the lock if this process holds it."""
    try:
        with open(path, encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        return
    if int(info.get("pid", 0)) == os.getpid():
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)


@contextlib.contextmanager
def held(what: str, path: str = LOCK):
    acquire(what, path)
    try:
        yield
    finally:
        release(path)
