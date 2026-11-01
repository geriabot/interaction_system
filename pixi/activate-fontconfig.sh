# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Pixi activation script (sourced, not executed): isolates the fontconfig
# cache of the pixi environment from the one of the host system.
#
# Why: the environment ships its own fontconfig (conda-forge), which usually
# differs from the host one (e.g. 2.18 in pixi vs 2.17 on the system). Both
# write caches with the same format suffix (*.cache-12) to ~/.cache/fontconfig,
# but the files are not compatible between versions. When a Qt application of
# the environment (Gazebo GUI, rviz2, rqt...) reads a cache written by the host
# fontconfig, it crashes with a segfault in FcCharSetFindLeafForward.
#
# How: the stock $CONDA_PREFIX/etc/fonts/fonts.conf lists three cache
# directories: the environment's own one ($CONDA_PREFIX/var/cache/fontconfig),
# the XDG one (~/.cache/fontconfig) and the legacy ~/.fontconfig. We generate a
# copy of that file without the two shared ones, so the environment only uses
# its own cache, and point FONTCONFIG_FILE to it. Everything else (font
# directories, conf.d rules) is kept as is.

_pixi_fc_stock="$CONDA_PREFIX/etc/fonts/fonts.conf"
_pixi_fc_isolated="$CONDA_PREFIX/etc/fonts/fonts-isolated-cache.conf"

# Environments without fontconfig have nothing to fix
if [ -f "$_pixi_fc_stock" ]; then
  # Regenerate only when missing or when the stock file is newer
  # (e.g. fontconfig was updated by `pixi install`)
  if [ ! -f "$_pixi_fc_isolated" ] || [ "$_pixi_fc_stock" -nt "$_pixi_fc_isolated" ]; then
    sed -e '/<cachedir prefix="xdg">fontconfig<\/cachedir>/d' \
        -e '/<cachedir>~\/.fontconfig<\/cachedir>/d' \
        "$_pixi_fc_stock" > "$_pixi_fc_isolated"
  fi
  export FONTCONFIG_FILE="$_pixi_fc_isolated"
fi

unset _pixi_fc_stock _pixi_fc_isolated
