# Copyright 2026 Rodrigo Pérez-Rodríguez
#
# Licensed under the MIT License. See the LICENSE file of the repository.

"""
The campaigns of real sessions and their configurations, without ROS (real_sessions.py, the
control panel and its Tests tab use them).

A *configuration* (configs/<name>.yaml) is how the robot sounds and the hardware of its speech:
the Piper voice (`voice`: NAME, or NAME:SPEAKER for a voice with several speakers), its speed,
expressiveness and rhythm variation (`length_scale`, `noise_scale`, `noise_w_scale`; empty: the
voice's own), the seconds the robot may take to answer before a conversational filler
(`filler_after_s`; empty: the scenario's, else the system's), the microphone (`input_device`) and
the speech-recognition model (`whisper_model`). What the participants hear is a condition of the
study, the same for all of them: so it is named, and frozen once a campaign uses it.

A *campaign* (campaigns/<name>.yaml) is a set of real sessions: a sessions file (the study design:
conditions, scenarios, cards) and one configuration. Its participants are stored in their own
folder, <out>/<campaign>/<P07>/ (out: ~/.ros/interaction_real). A pilot is a campaign.

The rules (doc/REAL_SESSIONS.md#campaigns-and-configurations), derived from the files, with no
other state:

  - a configuration that no campaign references is a *draft*: it can be edited, renamed and tried
    freely; once a campaign references it, it is *frozen*: never edited in place (save_config
    refuses it); duplicate() makes a new draft with everything copied;
  - a campaign records the hash of its configuration file when it is associated; while it has no
    stored trial it may switch to another configuration (the hash is taken again), never after;
  - every stored trial records the name and the hash of its configuration; integrity() says why a
    campaign must refuse its trials and checks: its configuration file changed (by hand), is
    missing, or a stored trial ran with another configuration.

    python3 real/campaigns.py list
    python3 real/campaigns.py new-campaign pilot --config default [--sessions-file sessions_es.yaml]
    python3 real/campaigns.py switch pilot default-2
    python3 real/campaigns.py duplicate default [--as slower]
    python3 real/campaigns.py set default-2 length_scale=1.1 voice=es_ES-sharvard-medium:1
"""

import argparse
import glob
import json
import os
import re
import sys
import time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sessions_common import (OUT, PIPER_SETTINGS, file_hash, load, parse_voice,  # noqa: E402
                             participants)

CONFIGS = os.path.join(HERE, "configs")
CAMPAIGNS = os.path.join(HERE, "campaigns")
# A name of a configuration or a campaign: a folder and a file name (never P07: the folder of a
# campaign sits next to the participants stored before campaigns existed)
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
CONFIG_KEYS = ("voice", "length_scale", "noise_scale", "noise_w_scale", "filler_after_s",
               "input_device", "whisper_model")
# The models of faster-whisper offered by the panel (any name of its catalog is accepted)
WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v2", "large-v3", "turbo")
WHISPER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
# The speech hardware of a test without a configuration (a check or try-scenario without
# --config): those of the starting configuration (configs/default.yaml). "default" goes through
# the system's sound server, which converts the sample rate (PortAudio's own default can be a raw
# ALSA device that does not record at 16 kHz)
SPEECH_DEFAULTS = {"input_device": "default", "whisper_model": "small"}
LANGUAGE_CODES = {"Spanish": "es", "English": "en"}
PRESESSION = "presession.json"     # in <out>/<campaign>/: the last pre-session check

# The Piper voices offered by the panels (huggingface.co/rhasspy/piper-voices; simple_hri downloads
# a missing one into its models folder the first time): (value of --voice, language, gender,
# label). NAME:SPEAKER is a speaker of a voice with several. The gender is that of the voice's card,
# checked on the downloaded ones by the median pitch of a question (2026-10-30: men 112-155 Hz,
# women 180-202 Hz). A configuration may hold any voice of the catalog, listed here or not.
VOICES = [
    ("es_ES-sharvard-medium", "es", "man", "es_ES-sharvard-medium, speaker 0: the default"),
    ("es_ES-sharvard-medium:1", "es", "woman", "es_ES-sharvard-medium, speaker 1"),
    ("es_ES-davefx-medium", "es", "man", "es_ES-davefx-medium"),
    ("es_ES-carlfm-x_low", "es", "man", "es_ES-carlfm-x_low (16 kHz)"),
    ("es_MX-claude-high", "es", "woman", "es_MX-claude-high"),
    ("es_MX-ald-medium", "es", "man", "es_MX-ald-medium"),
    ("es_AR-daniela-high", "es", "woman", "es_AR-daniela-high"),
    ("en_US-lessac-medium", "en", "woman", "en_US-lessac-medium: the default"),
    ("en_US-lessac-high", "en", "woman", "en_US-lessac-high"),
    ("en_US-amy-medium", "en", "woman", "en_US-amy-medium"),
    ("en_US-ryan-high", "en", "man", "en_US-ryan-high"),
    ("en_US-hfc_female-medium", "en", "woman", "en_US-hfc_female-medium"),
    ("en_US-hfc_male-medium", "en", "man", "en_US-hfc_male-medium"),
    ("en_GB-alba-medium", "en", "woman", "en_GB-alba-medium"),
    ("en_GB-northern_english_male-medium", "en", "man", "en_GB-northern_english_male-medium"),
    ("en_GB-cori-high", "en", "woman", "en_GB-cori-high"),
]
# The settings of the voice (PIPER_SETTINGS: the allowed range): (label, hint)
SETTINGS = {
    "length_scale": ("Speed (length_scale)",
                     "< 1 faster, > 1 slower; usually 0.8-1.3 (default 1)"),
    "noise_scale": ("Expressiveness (noise_scale)",
                    "the variation of the voice: higher is livelier, lower is flatter; usually "
                    "0.3-1.0 (es_ES-sharvard-medium: 0.667)"),
    "noise_w_scale": ("Rhythm variation (noise_w_scale)",
                      "the variation of the sound durations: higher is less regular; usually "
                      "0.3-1.2 (es_ES-sharvard-medium: 0.8)"),
}
# Ready values that fill the settings (empty: the voice's own)
PRESETS = [
    ("voice default", {}),
    ("more expressive", {"noise_scale": 0.9, "noise_w_scale": 1.0}),
    ("slower and expressive", {"length_scale": 1.15, "noise_scale": 0.9, "noise_w_scale": 1.0}),
    ("very expressive", {"noise_scale": 1.0, "noise_w_scale": 1.2}),
    ("steadier (less variation)", {"noise_scale": 0.4, "noise_w_scale": 0.5}),
]


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def check_name(name: str, what: str) -> str:
    name = str(name or "").strip()
    if not NAME.match(name) or re.fullmatch(r"p\d+", name):
        raise ValueError(f"The name of a {what}: lowercase letters, digits, - and _ (up to 40, "
                         f"starting with a letter or a digit; not p07), not {name!r}")
    return name


# ------------------------------------------------------------------ configurations
def config_path(name: str) -> str:
    return os.path.join(CONFIGS, f"{name}.yaml")


def number(value, key: str, low: float, high: float):
    """None (empty: the default) or a number in [low, high]; ValueError otherwise."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = float("nan")
    if not low <= value <= high:     # also NaN
        raise ValueError(f"{key}: a number from {low:g} to {high:g}, or empty for the default")
    return value


def validate_config(values: dict) -> dict:
    """The values of a configuration, checked and in the order of CONFIG_KEYS; ValueError (with
    what to fix) otherwise."""
    unknown = set(values) - set(CONFIG_KEYS)
    if unknown:
        raise ValueError(f"Unknown keys of a configuration: {sorted(unknown)} (known: {CONFIG_KEYS})")
    out = {}
    voice = str(values.get("voice") or "").strip()
    if not voice:
        raise ValueError("voice: a Piper voice of the catalog, NAME or NAME:SPEAKER (a "
                         "configuration names its voice: the participants hear it)")
    parse_voice(voice)
    out["voice"] = voice
    for key, (low, high) in PIPER_SETTINGS.items():
        out[key] = number(values.get(key), key, low, high)
    out["filler_after_s"] = number(values.get("filler_after_s"), "filler_after_s", 0.0, 60.0)
    device = str(values.get("input_device") or "").strip()
    if not device or len(device) > 120 or "\n" in device or device.isdigit():
        raise ValueError("input_device: the name of the microphone (a sounddevice name, as "
                         "`python3 -m sounddevice` lists it), or default (the system's sound "
                         "server); not an index (ROS would read it as a number)")
    out["input_device"] = device
    model = str(values.get("whisper_model") or "").strip()
    if not WHISPER.match(model):
        raise ValueError(f"whisper_model: a model of faster-whisper, e.g. {', '.join(WHISPER_MODELS)}")
    out["whisper_model"] = model
    return out


def voice_language(voice: str) -> str:
    """The language code of a voice (es of es_ES-davefx-medium)."""
    return parse_voice(voice)[0].split("_", 1)[0]


HEADER = """\
# A configuration of the real sessions: how the robot sounds and the hardware of its speech
# (real/campaigns.py; doc/REAL_SESSIONS.md#campaigns-and-configurations). While no campaign uses
# it, it is a draft; once a campaign uses it, it is frozen: never edit this file then (the
# campaign would refuse its trials): duplicate it (the control panel's Configurations, or
# real/campaigns.py duplicate) and edit the copy.
#
#   voice           a Piper voice of the catalog: NAME, or NAME:SPEAKER for a voice with several
#   length_scale    speed: < 1 faster, > 1 slower (0.5-2.0); empty: the voice's own (1)
#   noise_scale     expressiveness, the variation of the voice (0-1.5); empty: the voice's own
#   noise_w_scale   rhythm, the variation of the sound durations (0-1.5); empty: the voice's own
#   filler_after_s  the seconds the robot may take to answer before a conversational filler
#                   ("Mmm..."); 0: none; empty: the scenario's, else the system's
#   input_device    the microphone (a sounddevice name); default: the system's sound server,
#                   which converts the sample rate
#   whisper_model   the speech-recognition model of faster-whisper (tiny, base, small, medium...)
"""


def write_config(name: str, values: dict, meta: dict) -> None:
    os.makedirs(CONFIGS, exist_ok=True)
    data = {**validate_config(values), **{k: v for k, v in meta.items() if v not in (None, "")}}
    with open(config_path(name), "w", encoding="utf-8") as f:
        f.write(HEADER + "\n")
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, width=100)


def load_config(name: str) -> dict:
    """{name, path, hash, values, note, created, duplicated_from} of a configuration; ValueError
    when it is missing or wrong."""
    name = check_name(name, "configuration")
    path = config_path(name)
    if not os.path.isfile(path):
        raise ValueError(f"No configuration {name} (real/configs/{name}.yaml)")
    data = load(path) or {}
    meta = {k: data.pop(k, None) for k in ("note", "created", "duplicated_from")}
    try:
        values = validate_config(data)
    except ValueError as exc:
        raise ValueError(f"Configuration {name} (real/configs/{name}.yaml): {exc}") from None
    return {"name": name, "path": path, "hash": file_hash(path), "values": values, **meta}


def config_names() -> list[str]:
    return sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(CONFIGS, "*.yaml"))
                  if NAME.match(os.path.basename(p)[:-5]))


def used_by(name: str) -> list[str]:
    """The campaigns that reference a configuration (it is frozen when there is any)."""
    return [c for c in campaign_names()
            if (read_campaign_file(c) or {}).get("configuration") == name]


def configurations() -> list[dict]:
    """Every configuration with its state: draft (no campaign uses it) or frozen (used_by)."""
    out = []
    for name in config_names():
        users = used_by(name)
        try:
            item = load_config(name)
            item.pop("path")
            error = ""
        except (ValueError, OSError, yaml.YAMLError) as exc:
            item, error = {"name": name, "values": {}, "hash": ""}, str(exc)
        out.append({**item, "state": "frozen" if users else "draft", "used_by": users,
                    "error": error})
    return out


def save_config(name: str, values: dict, previous: str = "", note: str = "") -> dict:
    """Write a draft: a new one (previous empty), or the draft `previous` edited, renamed to
    `name` when it differs. A frozen configuration is refused: duplicate it and edit the copy."""
    name = check_name(name, "configuration")
    meta = {"note": str(note or "").strip(), "created": now()}
    if previous:
        old = load_config(previous)
        users = used_by(previous)
        if users:
            raise ValueError(f"Configuration {previous} is frozen (used by the campaign"
                             f"{'s' if len(users) > 1 else ''} {', '.join(users)}): it is never "
                             "edited; duplicate it and edit the copy")
        meta.update(created=old.get("created") or now(), duplicated_from=old.get("duplicated_from"))
        if name != previous and os.path.exists(config_path(name)):
            raise ValueError(f"A configuration {name} exists already: choose another name")
    elif os.path.exists(config_path(name)):
        raise ValueError(f"A configuration {name} exists already: choose another name")
    write_config(name, values, meta)
    if previous and name != previous:
        os.remove(config_path(previous))
    return load_config(name)


def free_name(base: str) -> str:
    """<base>-2, -3...: the first name that no configuration has."""
    stem = re.sub(r"-\d+$", "", base)
    n = 2
    while os.path.exists(config_path(f"{stem}-{n}")):
        n += 1
    return f"{stem}-{n}"


def duplicate(name: str, new_name: str = "") -> dict:
    """A new draft with everything of `name` copied (its values and note), named `new_name` or
    <name>-2 (the first free)."""
    source = load_config(name)
    new_name = check_name(new_name, "configuration") if new_name else free_name(name)
    if os.path.exists(config_path(new_name)):
        raise ValueError(f"A configuration {new_name} exists already: choose another name")
    write_config(new_name, source["values"], {"note": source.get("note"), "created": now(),
                                              "duplicated_from": name})
    return load_config(new_name)


# ------------------------------------------------------------------ campaigns
def campaign_path(name: str) -> str:
    return os.path.join(CAMPAIGNS, f"{name}.yaml")


def campaign_names() -> list[str]:
    return sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(CAMPAIGNS, "*.yaml"))
                  if NAME.match(os.path.basename(p)[:-5]))


def read_campaign_file(name: str) -> dict | None:
    try:
        return load(campaign_path(name))
    except (OSError, yaml.YAMLError):
        return None


def campaign_out(name: str, out: str = OUT) -> str:
    """The folder of the participants of a campaign."""
    return os.path.join(out, name)


def sessions_files() -> list[str]:
    """The sessions files of real/ (those with conditions and scenarios), relative to real/."""
    out = []
    for path in sorted(glob.glob(os.path.join(HERE, "*.yaml"))):
        try:
            data = load(path) or {}
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict) and "conditions" in data and "scenarios" in data:
            out.append(os.path.basename(path))
    return out


def sessions_language(sessions_path: str) -> str:
    data = load(sessions_path) or {}
    return (data.get("launch") or {}).get("language") or LANGUAGE_CODES.get(
        data.get("language", "Spanish"), "es")


def check_voice_language(config: dict, sessions_path: str) -> None:
    lang = sessions_language(sessions_path)
    if voice_language(config["values"]["voice"]) != lang:
        raise ValueError(f"The voice of {config['name']} ({config['values']['voice']}) is not of "
                         f"the language of {os.path.basename(sessions_path)} ({lang})")


def load_campaign(name: str) -> dict:
    """{name, path, sessions_file, sessions_path, configuration, configuration_hash, created,
    associated, config (None when it cannot be read: integrity() says why)}."""
    name = check_name(name, "campaign")
    data = read_campaign_file(name)
    if data is None:
        raise ValueError(f"No campaign {name} (real/campaigns/{name}.yaml)")
    campaign = {"name": name, "path": campaign_path(name), **{k: data.get(k) for k in (
        "sessions_file", "configuration", "configuration_hash", "created", "associated")}}
    campaign["sessions_path"] = os.path.join(HERE, str(campaign["sessions_file"] or ""))
    try:
        campaign["config"] = load_config(campaign["configuration"])
    except (ValueError, OSError, yaml.YAMLError):
        campaign["config"] = None
    return campaign


def stored_records(name: str, out: str = OUT) -> list[str]:
    """The trial records stored in a campaign, also those replaced (a trial run again)."""
    folder = campaign_out(name, out)
    return sorted(glob.glob(os.path.join(folder, "P*", "trial_*.json"))
                  + glob.glob(os.path.join(folder, "P*", "replaced", "*", "trial_*.json")))


def integrity(campaign: dict, out: str = OUT) -> str:
    """Why the campaign must refuse its trials and checks ('' when it may run them)."""
    name, conf = campaign["name"], campaign["configuration"]
    if not os.path.isfile(campaign["sessions_path"]):
        return f"its sessions file {campaign['sessions_file']} is missing"
    if not os.path.isfile(config_path(str(conf))):
        return f"its configuration {conf} (real/configs/{conf}.yaml) is missing"
    if campaign["config"] is None:
        try:
            load_config(conf)
        except (ValueError, OSError, yaml.YAMLError) as exc:
            return str(exc)
    now_hash = file_hash(config_path(conf))
    if now_hash != campaign["configuration_hash"]:
        return (f"the file of its configuration {conf} changed after it was associated (hash "
                f"{now_hash}, recorded {campaign['configuration_hash']}): a configuration used "
                "by a campaign is never edited. Restore the file (e.g. from git), or, while the "
                "campaign has no stored trial, switch it to another configuration (duplicate "
                f"{conf} and edit the copy)")
    for path in stored_records(name, out):
        try:
            with open(path, encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, ValueError):
            return f"a stored trial cannot be read: {path}"
        if (record.get("configuration"), record.get("configuration_hash")) != (conf, now_hash):
            return (f"{os.path.relpath(path, campaign_out(name, out))} was stored with the "
                    f"configuration {record.get('configuration')} ({record.get('configuration_hash')}), "
                    f"not {conf} ({now_hash}): the campaign file was changed by hand")
    return ""


def write_campaign(campaign: dict) -> None:
    os.makedirs(CAMPAIGNS, exist_ok=True)
    data = {k: campaign[k] for k in ("name", "sessions_file", "configuration",
                                     "configuration_hash", "created", "associated")}
    with open(campaign_path(campaign["name"]), "w", encoding="utf-8") as f:
        f.write("# A campaign of real sessions (real/campaigns.py; "
                "doc/REAL_SESSIONS.md#campaigns-and-configurations).\n"
                "# Written by the control panel or campaigns.py: switch its configuration with them "
                "(only before its first\n# stored trial), never by hand.\n")
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, width=100)


def create_campaign(name: str, configuration: str, sessions_file: str = "sessions_es.yaml",
                    out: str = OUT) -> dict:
    """A new campaign; its configuration is frozen from now on."""
    name = check_name(name, "campaign")
    if os.path.exists(campaign_path(name)):
        raise ValueError(f"A campaign {name} exists already")
    if stored_records(name, out) or participants(campaign_out(name, out)):
        raise ValueError(f"{campaign_out(name, out)} already holds participants: choose another name")
    if sessions_file not in sessions_files():
        raise ValueError(f"Unknown sessions file {sessions_file!r} (real/: {sessions_files()})")
    config = load_config(configuration)
    check_voice_language(config, os.path.join(HERE, sessions_file))
    when = now()
    write_campaign({"name": name, "sessions_file": sessions_file, "configuration": config["name"],
                    "configuration_hash": config["hash"], "created": when, "associated": when})
    return load_campaign(name)


def switch(name: str, configuration: str, out: str = OUT) -> dict:
    """Another configuration for a campaign with no stored trial (its hash taken again); the
    configuration left becomes a draft again when no other campaign uses it."""
    campaign = load_campaign(name)
    done = stored_records(name, out)
    if done:
        raise ValueError(f"Campaign {name} has {len(done)} stored trial{'s' if len(done) > 1 else ''}: "
                         "its configuration can no longer change (create another campaign)")
    config = load_config(configuration)
    check_voice_language(config, campaign["sessions_path"])
    campaign.update(configuration=config["name"], configuration_hash=config["hash"],
                    associated=now())
    write_campaign(campaign)
    return load_campaign(name)


def presession_path(name: str, out: str = OUT) -> str:
    return os.path.join(campaign_out(name, out), PRESESSION)


def record_presession(campaign: dict, results: dict, out: str = OUT) -> dict:
    """Keep the last pre-session check of a campaign ({speak, listen, llm: {status, detail}}) and,
    when every part is OK, its last pass."""
    path = presession_path(campaign["name"], out)
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    entry = {"time": now(), "configuration": campaign["configuration"],
             "configuration_hash": campaign["configuration_hash"], "results": results,
             "passed": bool(results) and all(r.get("status") == "OK" for r in results.values())}
    state["last"] = entry
    if entry["passed"]:
        state["last_pass"] = entry
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    return state


def presession_state(campaign: dict, out: str = OUT, today: str = "") -> dict:
    """{last, last_pass, passed_today}: passed_today when the last pass is of today and of the
    campaign's configuration as it is now."""
    try:
        with open(presession_path(campaign["name"], out), encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    last_pass = state.get("last_pass") or {}
    today = today or time.strftime("%Y-%m-%d")
    state["passed_today"] = bool(last_pass) and last_pass.get("time", "")[:10] == today and (
        last_pass.get("configuration"), last_pass.get("configuration_hash")) == (
        campaign["configuration"], campaign["configuration_hash"])
    return state


def campaigns(out: str = OUT) -> list[dict]:
    """Every campaign as the panel shows it: its files, its configuration, how many participants
    and stored trials, why it refuses its trials (integrity) and its pre-session check."""
    rows = []
    for name in campaign_names():
        try:
            campaign = load_campaign(name)
        except ValueError as exc:
            rows.append({"name": name, "error": str(exc)})
            continue
        config = campaign.pop("config")
        stored = len(stored_records(name, out))
        rows.append({**{k: v for k, v in campaign.items() if k not in ("path", "sessions_path")},
                     "values": (config or {}).get("values", {}), "error": "",
                     "integrity": integrity({**campaign, "config": config}, out),
                     "participants": len(participants(campaign_out(name, out))),
                     "stored": stored, "can_switch": stored == 0,
                     "presession": presession_state(campaign, out)})
    return rows


def legacy_participants(out: str = OUT) -> list[str]:
    """Participants stored before campaigns existed (<out>/P07/): never moved nor deleted; they
    belong to no campaign and no trial of theirs runs again."""
    return participants(out)


def apply(sessions: dict, config: dict, campaign: str = "") -> dict:
    """The sessions file with a configuration: the microphone and the speech-recognition model in
    `launch`, the voice and its settings in `piper` (the stack then runs without its speech nodes,
    speech:=none, and real_sessions.py starts simple_hri with them), the filler delay, and what
    the records keep of it (`configuration`, `campaign`). The language of the voice is checked
    when a campaign is created or switched (check_voice_language), and by try-scenario against
    its scenario file."""
    values = config["values"]
    out = dict(sessions)
    out["launch"] = {**sessions.get("launch", {}), "speech": "none",
                     "input_device": values["input_device"],
                     "whisper_model": values["whisper_model"]}
    out["piper"] = {"voice": values["voice"], **{k: values[k] for k in PIPER_SETTINGS
                                                 if values.get(k) is not None}}
    out["filler_after_s"] = values.get("filler_after_s")
    out["configuration"] = {"name": config["name"], "hash": config["hash"], "values": values}
    out["campaign"] = campaign
    return out


def describe(values: dict) -> str:
    """A configuration in words, e.g. "es_ES-sharvard-medium:1, noise_scale 0.9; filler after
    0.5 s; microphone default; whisper small"."""
    settings = [f"{k} {values[k]:g}" for k in PIPER_SETTINGS if values.get(k) is not None]
    voice = ", ".join([values.get("voice", "?")] + settings)
    filler = values.get("filler_after_s")
    return (f"{voice}; filler after "
            f"{'the default' if filler is None else f'{filler:g} s'}; microphone "
            f"{values.get('input_device')}; whisper {values.get('whisper_model')}")


# ------------------------------------------------------------------ command line
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=OUT, help="The folder of the sessions' data")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="The configurations (draft or frozen) and the campaigns")
    p = sub.add_parser("new-campaign", help="A new campaign (its configuration is frozen)")
    p.add_argument("name")
    p.add_argument("--config", required=True, help="The configuration (real/configs/<name>.yaml)")
    p.add_argument("--sessions-file", default="sessions_es.yaml", help="A sessions file of real/")
    p = sub.add_parser("switch", help="Another configuration, only before the first stored trial")
    p.add_argument("campaign")
    p.add_argument("config")
    p = sub.add_parser("duplicate", help="A new draft copied from a configuration")
    p.add_argument("config")
    p.add_argument("--as", dest="new_name", default="", help="Its name (default <config>-2)")
    p = sub.add_parser("set", help="Change values of a draft (a frozen configuration is refused)")
    p.add_argument("config")
    p.add_argument("values", nargs="+", help="key=value (empty value: the default)")
    args = parser.parse_args()
    try:
        if args.command == "list":
            for c in configurations():
                print(f"configuration {c['name']}: {c['state']}"
                      + (f" (used by {', '.join(c['used_by'])})" if c["used_by"] else "")
                      + (f" - ERROR {c['error']}" if c["error"] else f" - {describe(c['values'])}"))
            for c in campaigns(args.out):
                if c["error"]:
                    print(f"campaign {c['name']}: ERROR {c['error']}")
                    continue
                print(f"campaign {c['name']}: {c['sessions_file']}, configuration "
                      f"{c['configuration']}, {c['participants']} participants, {c['stored']} "
                      f"trials stored; pre-session check passed today: "
                      f"{'yes' if c['presession']['passed_today'] else 'no'}"
                      + (f"\n  REFUSES ITS TRIALS: {c['integrity']}" if c["integrity"] else ""))
            legacy = legacy_participants(args.out)
            if legacy:
                print(f"participants stored before campaigns existed (no campaign): {', '.join(legacy)}")
        elif args.command == "new-campaign":
            c = create_campaign(args.name, args.config, args.sessions_file, args.out)
            print(f"Campaign {c['name']} created; configuration {c['configuration']} is frozen. "
                  f"Its participants: {campaign_out(c['name'], args.out)}")
        elif args.command == "switch":
            c = switch(args.campaign, args.config, args.out)
            print(f"Campaign {c['name']} now uses {c['configuration']} ({c['configuration_hash']})")
        elif args.command == "duplicate":
            c = duplicate(args.config, args.new_name)
            print(f"Draft {c['name']} created from {args.config}: {config_path(c['name'])}")
        else:
            values = load_config(args.config)["values"]
            for item in args.values:
                key, sep, value = item.partition("=")
                if not sep:
                    raise ValueError(f"key=value, not {item!r}")
                values[key.strip()] = value.strip() or None
            c = save_config(args.config, values, previous=args.config,
                            note=load_config(args.config).get("note") or "")
            print(f"{c['name']}: {describe(c['values'])}")
    except ValueError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
