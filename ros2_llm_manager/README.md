# ros2_llm_manager: the LLM gateway

One ROS 2 node that puts LLM inference behind a single action and a few session services. It
serves local models through Ollama and cloud models through Google Gemini, Anthropic Claude,
OpenAI or any OpenAI-compatible server, with optional stateful (history-keeping) sessions and
JSON-schema-constrained output. The interaction system no longer starts it: its questions are
asked to behavior_architecture's interpretation server
([ARCHITECTURE.md](../docs/ARCHITECTURE.md#the-interpretation-server)), whose routes are written
from this node's parameters (the model binding,
[CONFIGURATION.md](../docs/CONFIGURATION.md#llm_manager_node)). Any node can still use the
gateway on its own.

## Nodes

| Node (ros2 run name) | Description |
| --- | --- |
| `llm_manager_node` | The action server and the session services. Its action (`LlmInference`), services (`CreateSession`, `DeleteSession`, `ListSessions`) and error message (`LlmError`) are defined in `ros2_llm_interfaces` |
| `llm_cli_client` | Interactive terminal client to test the connection, the sessions and the streaming |

## Usage

```bash
ros2 launch ros2_llm_manager llm_manager.launch.py                              # config/params.yaml
ros2 launch ros2_llm_manager llm_manager.launch.py params:=/path/to/params.yaml
ros2 run ros2_llm_manager llm_manager_node --ros-args --params-file config/params.yaml
```

With the node running, `ros2 run ros2_llm_manager llm_cli_client` opens the client: `/new
[provider] [model]` opens a session, `/stateless` toggles the history for the next session,
`/list` and `/info` inspect the sessions, any other text is sent as a prompt to the active
session, `/help` lists the other commands, and `/quit` closes every session and exits.

## Providers

| Provider | Enable with | Python package |
| --- | --- | --- |
| Ollama | `ollama.enable: true` (shipped) | `httpx` |
| Google Gemini | `gemini.enable: true` | `google-genai` |
| Anthropic Claude | `claude.enable: true` | `anthropic` |
| OpenAI, or an OpenAI-compatible server (`openai.base_url`) | `openai.enable: true` | `openai` |

The packages are pinned in [`config/requirements.txt`](config/requirements.txt). The pixi
environment includes `httpx`, `google-genai` and `openai`; install `anthropic` in it to use
Claude. With Claude, the leading part of each prompt that repeats across turns
(`cache_prefix_len` of the goal) is marked for prompt caching.

Adding a provider means implementing `BaseProvider` (`connect`, `ensure_model_ready`,
`generate`) in `ros2_llm_manager/providers/` and registering it in the node's startup.

## Configuration

[`config/params.yaml`](config/params.yaml) ships the same values as the `llm_manager_node`
section of the stack's `app_params.yaml`: Ollama only, with `qwen2.5:7b`.
[CONFIGURATION.md](../docs/CONFIGURATION.md#llm_manager_node) lists every parameter and the
environment variables of the API keys.
