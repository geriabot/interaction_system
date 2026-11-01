#!/usr/bin/env bash
# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

# Prepares the colcon workspace for pixi, once, after cloning the repository into
# <workspace>/src/interaction_system: links the repository's pixi.toml and pixi.lock at the root of
# the workspace, so that pixi runs from there (pixi install, pixi shell, pixi run build) and its
# environment (.pixi) lives at the root of the workspace. The files stay in the repository: what
# pixi writes to the lock goes to the repository's copy, through the link.
#
#   ./src/interaction_system/pixi/setup_workspace.sh      (from anywhere)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WS="$(cd "$REPO/../.." && pwd)"
REL="$(realpath --relative-to="$WS" "$REPO")"

if [ "$(basename "$(dirname "$REPO")")" != "src" ]; then
  echo "The repository must be in <workspace>/src/ (it is in $REPO)" >&2
  exit 1
fi

for file in pixi.toml pixi.lock; do
  target="$WS/$file"
  if [ -L "$target" ]; then
    ln -sfn "$REL/$file" "$target"
  elif [ -e "$target" ]; then
    echo "$target exists and is not a link: move it away and run this again" >&2
    exit 1
  else
    ln -s "$REL/$file" "$target"
  fi
  echo "$target -> $REL/$file"
done
echo "Done. From $WS: pixi install, then pixi shell (or pixi run build)."
