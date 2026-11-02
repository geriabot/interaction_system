# Serving the models from a GPU server

The computer that runs the robot's software does not need a GPU: the LLM can run in Ollama on a
server with one, reached through an SSH tunnel so that the gateway finds it at `localhost:11434`,
its default address. This page sets up Ollama in the user's home on a shared server, without
`sudo`. [MODELS.md](MODELS.md) explains which model answers each question.

## Requirements

- An NVIDIA GPU with its driver installed (`nvidia-smi` works). The default model,
  `qwen2.5:7b`, needs about 8 GB of GPU memory; a 14B model, about 12 GB; two models loaded at
  once ([routes](MODELS.md#one-model-per-question-routes)) need room for both.
- `curl`, and `tar` with zstd support (`unzstd` or `tar --zstd`): Ollama releases are `.tar.zst`
  files.
- SSH access to the server from the robot's computer.
- Disk for the models: 5 to 10 GB each for models of 7 to 14 billion parameters.

## Installing Ollama without sudo

On the server:

```sh
mkdir -p ~/ollama && cd ~/ollama
curl -s https://api.github.com/repos/ollama/ollama/releases/latest | grep browser_download_url | grep linux-amd64
curl -L <the plain linux-amd64 file, not -rocm> -o ollama.tar.zst
tar --use-compress-program=unzstd -xf ollama.tar.zst && rm ollama.tar.zst   # or tar --zstd -xf
echo 'export PATH=$HOME/ollama/bin:$PATH' >> ~/.bashrc && source ~/.bashrc
ollama --version
```

For a given version, use `.../releases/tags/v<version>` instead of `.../releases/latest`. Removing
`~/ollama` and the models directory undoes the installation.

## Starting it

```sh
mkdir -p ~/ollama_models
nohup env OLLAMA_MODELS=~/ollama_models OLLAMA_MAX_LOADED_MODELS=2 OLLAMA_NUM_PARALLEL=1 \
  OLLAMA_CONTEXT_LENGTH=8192 ollama serve > ~/ollama.log 2>&1 &
echo $! > ~/ollama.pid
ollama pull qwen2.5:7b                         # the default model; the experiments use the one chosen in E0a
```

| Variable | Value | Why |
|---|---|---|
| `OLLAMA_MODELS` | `~/ollama_models` | Where the models are stored (without it, `~/.ollama/models`). Start the server with the same value every time, or `ollama list` shows none |
| `OLLAMA_MAX_LOADED_MODELS` | `2` | The models kept in GPU memory at once, so that the main model and a [routed](MODELS.md#one-model-per-question-routes) one both stay loaded |
| `OLLAMA_NUM_PARALLEL` | `1` | Requests served at once by each model. Each parallel slot reserves its own context memory, and one conversation asks one question at a time |
| `OLLAMA_CONTEXT_LENGTH` | `8192` | The context of each loaded model, in tokens, for clients of the server that do not send their own |

With `OLLAMA_MAX_LOADED_MODELS=1`, Ollama swaps the two models at every change of model, which
adds the load time to the latency.

The gateway sends its own context length with every request (`ollama.num_ctx`, 8192 by default,
[CONFIGURATION.md](CONFIGURATION.md#llm_manager_node)), which takes precedence over
`OLLAMA_CONTEXT_LENGTH`. Ollama cuts a prompt that does not fit, and the prompts grow with the
resources of the request (a question bank, the material of grounded question answering). The
gateway logs the prompt tokens of every answer (`tokens=<prompt>+<answer>`). A longer context
takes more GPU memory.

`ollama serve` listens on `127.0.0.1:11434` only, so the server is not exposed to the network; the
tunnel reaches it from the robot's computer.

Nothing starts by itself after a restart of the server: run the start command again.

## The tunnel

On the robot's computer, open the tunnel and leave the terminal open:

```sh
ssh -N -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -L 11434:localhost:11434 <user>@<server>
curl -s localhost:11434/api/version            # in another terminal: Ollama answers
curl -s localhost:11434/api/tags               # the models on the server
```

`ServerAliveInterval` closes the tunnel when the connection dies, instead of leaving it holding the
port. `ExitOnForwardFailure` makes the tunnel fail at once when the port is taken, e.g. by an
Ollama running on the robot's computer or by an old tunnel: stop it, or forward another port
(`-L 11435:localhost:11434`) and set `ollama.port` of `llm_manager_node` to it
([CONFIGURATION.md](CONFIGURATION.md#llm_manager_node)).

## On a shared server

A shared GPU must not be used while someone else's work runs on it.

Before running, check that the GPU and the Ollama server are free:

```sh
nvidia-smi                                     # on the server: no other processes, the memory free
curl -s localhost:11434/api/ps                 # through the tunnel: no models loaded by other work
```

The experiment runner makes the same check before each of its parallel runs, and refuses to run on a busy server
([evaluation/README.md](../evaluation/README.md#hardware-profiles-and-parallel-runs)).

Afterwards, free the GPU. The gateway asks Ollama to keep its models loaded indefinitely
(`keep_alive: -1`), so they stay in GPU memory after the stack stops:

```sh
ollama ps                                      # on the server: the loaded models
ollama stop <model>                            # unload one
kill $(cat ~/ollama.pid)                       # or stop the server
```

Then close the tunnel (Ctrl+C in its terminal). Keep the list of models short on a shared disk:
`ollama list`, and `ollama rm <model>` for those no longer needed.

## Problems and fixes

| Symptom | Cause | Fix |
|---|---|---|
| `curl localhost:11434/api/version` does not answer | The tunnel is closed, or Ollama is not running on the server | Open the tunnel; on the server, `curl -s localhost:11434/api/version`, and start Ollama if it does not answer |
| The tunnel exits with `Address already in use` and `Could not request local forwarding` | Port 11434 of the robot's computer is taken | `ss -ltnp \| grep 11434` shows the process that holds it; stop it, or forward another port |
| The gateway logs `model not found`, or the first answer fails | The model is not pulled on the server | `ollama pull <model>` on the server |
| `ollama list` is empty although models were pulled | The server was started with another `OLLAMA_MODELS` | Start it with the same value every time |
| The answers are very slow | The model runs partly or wholly on the CPU (`ollama ps` does not show `100% GPU`): the GPU is busy, or the models do not fit together | Free the GPU; use a smaller model, a shorter context, or fewer loaded models |
| `nvidia-smi`: `Failed to initialize NVML: Driver/library version mismatch` | The driver was updated and the server not restarted | Restart the server |
