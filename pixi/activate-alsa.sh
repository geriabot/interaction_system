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

# Pixi activation script (sourced, not executed): lets the environment's ALSA reach the host's
# sound server (PipeWire or PulseAudio).
#
# Why: the environment ships its own alsa-lib (conda-forge), which reads the host's ALSA
# configuration (where "default" is the sound server) but looks for the plugins in its own
# prefix, where the host's pipewire/pulse plugins are not. Then "default" falls back to the first
# card's raw device: on many laptops that is a capture that records silence (the built-in
# microphone is another device) or that does not record at 16 kHz, the rate of the speech
# recognition.
#
# How: point ALSA_PLUGIN_DIR to the host's ALSA plugins when they include the PipeWire or the
# PulseAudio one, unless it is already set. Without them nothing changes.

if [ -z "${ALSA_PLUGIN_DIR:-}" ]; then
  for _pixi_alsa_dir in /usr/lib/x86_64-linux-gnu/alsa-lib /usr/lib64/alsa-lib /usr/lib/alsa-lib; do
    if [ -f "$_pixi_alsa_dir/libasound_module_pcm_pipewire.so" ] ||
       [ -f "$_pixi_alsa_dir/libasound_module_pcm_pulse.so" ]; then
      export ALSA_PLUGIN_DIR="$_pixi_alsa_dir"
      break
    fi
  done
  unset _pixi_alsa_dir
fi
