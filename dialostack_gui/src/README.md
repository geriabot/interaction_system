# DialoStack GUI architecture

This page describes the internal design of the web GUI, for those who change
it. The GUI is a single-page React app (Vite + Tailwind) to configure, launch
and monitor the conversations of the interaction system.
[ARCHITECTURE.md](../../docs/ARCHITECTURE.md#the-web-gui-dialostack_gui)
describes what each page does, and
[INSTALLATION.md](../../docs/INSTALLATION.md#the-web-gui) how to start it. The
app talks to two backends:

- The GUI server (`../server.py`), over HTTP (`lib/api.js`). It generates the
  ROS parameter YAML (for the LLM gateway, the interaction intelligence and the
  orchestrator), serves the engine defaults (prompts, phrases), starts and stops
  the engine (`interaction_system main.launch.py`), and exposes microphone and
  system helpers. The speech choices become launch arguments
  (`speech:=simple_hri stt:=… tts:=… whisper_model:=…`, or `speech:=none` for
  typed text only). All the YAML and parameter generation lives in the server,
  never in the frontend.
- rosbridge, over WebSocket (roslib) for live ROS topics and the dialog action,
  via the hooks in `hooks/`.

```
src/
├── main.jsx            Entry: mounts providers (Engine, ROS, Config) + router
├── App.jsx             Shell: sidebar + routes + the shared dialogue timeline
├── components/         Shared UI, built from here, not redefined per page
│   ├── ui.jsx          Form atoms (Field, Select, NumberField…), ModifiedTag, badges
│   ├── FileImportButton.jsx, Sidebar.jsx, ConnectionBadge.jsx, EngineNotices.jsx
├── contexts/           Cross-page global state
│   ├── ConfigContext   The whole config object (localStorage-backed) + updateSection
│   ├── EngineContext   Engine status polling, start/stop/killAll, keepalive
│   └── ROSContext      rosbridge connection
├── hooks/              Reusable React/IO logic
│   ├── useTopic / usePublisher / useActionClient   ROS plumbing
│   ├── useDialogTimeline                            Topics → timeline reducer actions
│   ├── usePersistentState / usePopover / useFlash / useLlmError / useImageTopic(s) / useTheme
├── lib/                Pure logic + constants (unit-tested with *.test.js)
│   ├── api.js          The ONLY HTTP client (timeouts, typed ApiError)
│   ├── ros.js          Topic/action names + types (single source)
│   ├── formatters.js   Time/duration formatting (shared by Monitor + export)
│   ├── prompts.js / strategies.js   Editor metadata + validation/grouping
│   ├── frames.js       Frame Builder data layer + Builder↔page handoff
│   ├── sessionExport.js  Timeline → CSV/JSON/Markdown
│   ├── configProfiles.js  Named configuration profiles (Configuration page)
│   ├── download.js / status.js  File downloads; how the ROS and engine states are shown
│   ├── storageKeys.js  localStorage keys (single source)
│   └── timeline/reducer.js  Pure Monitor state machine (no React/ROS, fully tested)
└── pages/              One folder per route
    ├── Monitor/        Chat timeline, launch drawer, composer, frame sidebar
    ├── FrameBuilder/   Build a frame per dialogue behavior (slots / quiz / resources)
    ├── PromptsEditor/  Override the LLM prompt templates
    ├── StrategiesEditor/  Edit each dialogue behavior's fixed phrases (per language)
    └── ConfigEditor/   Parameter tabs (LLM, Dialogue, STT, TTS) + engine panel
```

## Data flow

### From the configuration to the engine

Editors write into `config.<section>` via
`updateSection` (ConfigContext, persisted to localStorage). On engine start,
`EngineContext` POSTs the whole config. `server.py` turns it into a ROS params
file plus the speech launch arguments and launches the engine. The frontend
never builds YAML.

### Speech

Speech is the simple_hri package, started by `main.launch.py` on the engine
machine. It listens when the dialogue asks and plays on that machine's speaker,
so the page carries no audio. The STT tab chooses simple_hri or none (typed text
only), the STT engine (faster-whisper or Whisper) and the Whisper model; the TTS
tab chooses the TTS engine (Piper or MMS-TTS). The STT
language and the TTS voice follow the dialogue language (English or Spanish,
the ones `main.launch.py` knows). The Monitor's composer publishes typed text on
`/transcription`, and `/say <text>` speaks through simple_hri's `/tts_action`.
`ConfigContext.mergeConfig` drops the fields that older versions of the GUI
saved, so old configurations and profiles still load.

### From ROS to the screen

`useDialogTimeline` subscribes to the dialog topics/feedback
and dispatches actions into the pure `timeline/reducer.js`. The Monitor renders
the resulting `events`. Time and parsing enter only through actions, so the
reducer is testable without React or ROS.

### Editable engine resources

The editable engine resources (prompts, phrases) follow one pattern: defaults
come from the engine (`/api/{prompts,phrases}`), the user edits *overrides*
stored in `config.{prompts,phrases}`, and on start the server injects only the
valid, changed ones as node parameters (defaults always win otherwise).

## How to…

- To add a config parameter, default it in `ConfigContext` `DEFAULT_CONFIG`,
  add a field in the matching `ConfigEditor/<Tab>.jsx` calling
  `update({ key: value })`, and map it to a ROS param in `server.py`'s
  `build_params` (or to a `main.launch.py` argument in `launch_args`).
- To add a page, create `pages/<Name>/index.jsx`, route it in `App.jsx`, and
  add it to `PAGES` in `components/Sidebar.jsx`.
- To add a ROS topic, add it to `TOPICS` in `lib/ros.js`, then
  `useTopic(name, type, cb)` (or `usePublisher`) where needed.
- To add an editable phrase or prompt, add it to the engine's defaults
  (`interaction_intelligence`) and expose it through the existing
  `/api/{prompts,phrases}` endpoints; it then shows up in the corresponding
  editor automatically.

## Conventions

- Pure logic and constants live in `lib/`. UI primitives live in `components/ui.jsx`,
  IO and stateful logic in `hooks/`, and global state in `contexts/`.
- Use `usePersistentState` for anything that should survive a reload.
- All server calls go through `lib/api.js` (don't `fetch` directly).
- `npm test` runs the `lib/*.test.js` unit tests. `npm run build` compiles the app.
  Keep both green.
