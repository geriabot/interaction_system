# Serving the models from a GPU server

The computer that runs the robot's software does not need a GPU: the LLM can run in Ollama on a
server with one, reached through an SSH tunnel at `localhost:11439` of the robot's computer, and
`ollama_host:=localhost:11439` tells the gateway to use it. Port 11434 stays for an Ollama on the
robot's computer itself, the gateway's default. The server runs [gpu_server](https://github.com/geriabot/gpu_server). [MODELS.md](MODELS.md) explains which model answers each question.

## Requirements

- An NVIDIA GPU with its driver installed (`nvidia-smi` works). The default model,
  `qwen2.5:7b`, needs about 8 GB of GPU memory; a 14B model, about 12 GB; two models loaded at
  once ([routes](MODELS.md#one-model-per-question-routes)) need room for both.
- `curl`, and `tar` with zstd support (`unzstd` or `tar --zstd`): Ollama releases are `.tar.zst`
  files.
- SSH access to the server from the robot's computer.
- Disk for the models: 5 to 10 GB each for models of 7 to 14 billion parameters.

## The server: gpu_server

The server side is [gpu_server](https://github.com/geriabot/gpu_server), shared by the systems
that use the same GPU server: it installs Ollama in the user's home without `sudo`, and runs one
Ollama per system (an *instance*), each on its own port and with its own settings, started and
stopped by its recorded PID. Its README explains the installation, the rules of a shared server
and the problems. On the server:

```sh
git clone https://github.com/geriabot/gpu_server.git ~/gpu_server
bash ~/gpu_server/install.sh                   # Ollama in ~/ollama, once
bash ~/gpu_server/start.sh interaction         # the instance of the interaction system
bash ~/gpu_server/pull_models.sh interaction   # its models
bash ~/gpu_server/status.sh                    # the GPU and every instance
bash ~/gpu_server/stop.sh interaction          # afterwards
```

The interaction system has two instances (`instances/*.env` of gpu_server):

| Instance | Server port | Models | Use |
|---|---|---|---|
| `interaction` | 11436 | `qwen3:8b`, alone on the GPU | The system with one model, as in the real sessions |
| `interaction_sim` | 11436 | `qwen3:8b` and `gemma3:12b`, both loaded | The system and the simulated user of the experiments |

Both use the same port, so only one of them runs at a time. To serve another model, pull it into
the instance (`pull_models.sh interaction <model>`) and name it in `ollama.model`
([MODELS.md](MODELS.md)).

The gateway sends its own context length with every request (`ollama.num_ctx`, 8192 by default,
[CONFIGURATION.md](CONFIGURATION.md#llm_manager_node)), which takes precedence over
`OLLAMA_CONTEXT_LENGTH`. Ollama cuts a prompt that does not fit, and the prompts grow with the
resources of the request (a question bank, the material of grounded question answering). The
gateway logs the prompt tokens of every answer (`tokens=<prompt>+<answer>`). A longer context
takes more GPU memory.

Each instance listens on `127.0.0.1` of the server only, so it is not exposed to the network; the
tunnel reaches it from the robot's computer. Nothing starts by itself after a restart of the
server: run `start.sh` again.

## The tunnel

Two local ports on the robot's computer keep the two Ollamas apart:

| Local port | What answers there |
|---|---|
| 11434 | Ollama on this computer (the official installer's service), the gateway's default |
| 11439 | Ollama on the GPU server, through the tunnel |

On the robot's computer, open the tunnel and leave the terminal open:

```sh
ssh -N -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -L 11439:localhost:11436 <user>@<server>
curl -s localhost:11439/api/version            # in another terminal: the server's Ollama answers
curl -s localhost:11439/api/tags               # the models on the server
```

Then start the stack with the launch argument that points the gateway to the tunnel:

```sh
ros2 launch interaction_system main.launch.py ollama_host:=localhost:11439
```

Without `ollama_host`, the gateway uses the address of its parameter files, `127.0.0.1:11434`: the
Ollama of this computer ([LAUNCH_ARGUMENTS.md](LAUNCH_ARGUMENTS.md),
[CONFIGURATION.md](CONFIGURATION.md#llm_manager_node)).

`ServerAliveInterval` closes the tunnel when the connection dies, instead of leaving it holding the
port. `ExitOnForwardFailure` makes the tunnel fail at once when its port is taken, usually by an
old tunnel: `ss -ltnp | grep 11439` shows the process that holds it. Stop it, or forward another
free port (`-L 11440:localhost:11436`) and pass the same one (`ollama_host:=localhost:11440`).
Do not use 11434 as the tunnel's local port: it would take the place of this computer's Ollama,
whose service then fails to start (`ss -ltnp | grep 11434` shows whether `ollama` or `ssh` holds
it).

## On a shared server

A shared GPU must not be used while someone else's work runs on it.

Before running, check that the GPU and the Ollama server are free:

```sh
nvidia-smi                                     # on the server: no other processes, the memory free
curl -s localhost:11439/api/ps                 # through the tunnel: no models loaded by other work
```

The experiment runner makes the same check before each of its parallel runs, and refuses to run
on a busy server ([evaluation/README.md](../evaluation/README.md#hardware-profiles-and-parallel-runs)).

Afterwards, free the GPU. The gateway asks Ollama to keep its models loaded indefinitely
(`keep_alive: -1`), so they stay in GPU memory after the stack stops:

```sh
bash ~/gpu_server/status.sh                    # on the server: the loaded models of every instance
bash ~/gpu_server/unload.sh interaction        # unload the models of the instance
bash ~/gpu_server/stop.sh interaction          # or stop the instance
```

Then close the tunnel (Ctrl+C in its terminal). Keep the list of models short on a shared disk
(gpu_server's README says how to remove the ones no longer needed).

## Problems and fixes

| Symptom | Cause | Fix |
|---|---|---|
| `curl localhost:11439/api/version` does not answer | The tunnel is closed, or Ollama is not running on the server | Open the tunnel; on the server, `bash ~/gpu_server/status.sh`, and `start.sh interaction` if it is not running |
| The tunnel exits with `Address already in use` and `Could not request local forwarding` | The tunnel's local port is taken, usually by an old tunnel | `ss -ltnp \| grep 11439` shows the process that holds it; stop it, or forward another free port and pass it in `ollama_host` |
| The robot uses the models of this computer, not the server's | The stack was started without `ollama_host` | Start it with `ollama_host:=localhost:11439` |
| This computer's Ollama does not start (`bind: address already in use`) | A tunnel holds port 11434 | `ss -ltnp \| grep 11434`; close that tunnel and open it on 11439 |
| The gateway logs `model not found`, or the first answer fails | The model is not pulled on the server | `bash ~/gpu_server/pull_models.sh interaction <model>` on the server |
| The instance has no models although they were pulled | They were pulled into another models directory | Pull them with `pull_models.sh interaction`: each instance keeps its own directory |
| The answers are very slow | The model runs partly or wholly on the CPU (`status.sh` of gpu_server does not show it wholly on the GPU): the GPU is busy, or the models do not fit together | Free the GPU; use a smaller model, a shorter context, or fewer loaded models |
| `nvidia-smi`: `Failed to initialize NVML: Driver/library version mismatch` | The driver was updated and the server not restarted | Restart the server |
