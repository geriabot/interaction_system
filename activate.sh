# Prepares a terminal for the native installation with a Python virtual environment
# (docs/INSTALL_NATIVE.md): system ROS 2, the workspace overlay and the environment's packages.
# Source this file, do not execute it:
#
#     source <workspace>/src/interaction_system/activate.sh
#
# Edit the two variables below once for your computer. Nothing is detected automatically.

# ── edit these ───────────────────────────────────────────────────────────────

# The ROS 2 distribution installed under /opt/ros/<distro> (a ROS_DISTRO already set in the
# environment is kept).
: "${ROS_DISTRO:=jazzy}"

# Absolute path of the virtual environment where requirements.txt was installed.
# Empty ("") when the packages were installed in the system Python (--break-system-packages).
DIALOSTACK_VENV=""

# ─────────────────────────────────────────────────────────────────────────────

# 1) ROS 2.
if [ -f "/opt/ros/${ROS_DISTRO}/setup.bash" ]; then
  source "/opt/ros/${ROS_DISTRO}/setup.bash"
else
  echo "DialoStack: /opt/ros/${ROS_DISTRO}/setup.bash not found — set ROS_DISTRO at the top of activate.sh" >&2
fi

# 2) The workspace overlay. This file is in <workspace>/src/interaction_system/, so the root of
#    the workspace is two levels up. Sourced only once it has been built.
_dialostack_ws="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)"
if [ -f "${_dialostack_ws}/install/setup.bash" ]; then
  source "${_dialostack_ws}/install/setup.bash"
fi

# 3) The virtual environment. Sourcing the overlay reorders PYTHONPATH, so the environment's
#    packages must go back in front, or the nodes fail with ModuleNotFoundError (sounddevice,
#    torch, faster-whisper...). Skipped when DIALOSTACK_VENV is empty.
if [ -n "${DIALOSTACK_VENV}" ]; then
  [ -f "${DIALOSTACK_VENV}/bin/activate" ] && source "${DIALOSTACK_VENV}/bin/activate"
  for _sp in "${DIALOSTACK_VENV}"/lib/python*/site-packages; do
    [ -d "${_sp}" ] && export PYTHONPATH="${_sp}:${PYTHONPATH:-}"
  done
fi

echo "DialoStack env ready — ROS ${ROS_DISTRO}"
