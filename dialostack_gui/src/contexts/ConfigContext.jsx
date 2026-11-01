/**
 * Configuration context: holds the engine config (LLM, dialogue, speech, STT,
 * TTS, prompt overrides), persisting it to localStorage and exposing
 * per-section update/reset helpers.
 */
import { createContext, useContext, useState, useCallback } from 'react'
import { STORAGE } from '../lib/storageKeys'

export const DEFAULT_CONFIG = {
  llm: {
    provider: 'gemini',
    model: 'gemini-2.5-flash',
    timeout: 60,
    max_concurrent: 4,
    silent_mode: false,
    gemini_api_key: '',
    claude_api_key: '',
    claude_effort: 'low',
    openai_api_key: '',
    openai_reasoning_effort: 'none',
    openai_base_url: '',
    ollama_host: '127.0.0.1',
    ollama_port: 11434,
    ollama_think: false,
  },
  dialog: {
    language: 'English',
    wait_timeout: 15,
    llm_timeout: 15,
    tts_timeout: 60,
    max_silence: 90,
    max_stalled: 4,
    max_attempts: 3,
    timeout_prompt: '',
    history_max_turns: 200,
    ack_phrases: [],
    ack_enabled: true,
    silent_mode: false,
    conversation_log_path: '/tmp/dialog_conversations.log',
    conversation_log_max_mb: 10.0,
  },
  // The speech capability: simple_hri listens and speaks on the engine
  // machine's own microphone and speaker; 'none' starts no speech nodes, so the
  // conversation is typed text only (the Monitor's composer).
  speech: {
    capability: 'simple_hri',
  },
  // simple_hri engines. The STT language and the TTS voice follow the dialogue
  // language.
  stt: {
    engine: 'faster_whisper',
    whisper_model: 'small',
  },
  tts: {
    engine: 'piper',
  },
  // Prompt overrides: { <key>: template }. Only contains the prompts the user
  // has modified; the rest always come from prompts.yaml (the defaults).
  prompts: {},
  // Per-language canned-phrase overrides: { <language>: { <key>: str | ack: [] } }.
  // Edited in the Strategies page; only modified phrases are stored.
  phrases: {},
}

const LS_KEY = STORAGE.config

// Sections that are free maps (any key is valid), not a fixed set of fields.
const FREE_SECTIONS = ['prompts', 'phrases']

/**
 * A full config from a stored or imported one: every section of DEFAULT_CONFIG,
 * missing keys filled with the defaults and unknown ones dropped, so a config
 * saved by an older GUI (e.g. with the removed audio routing) still loads.
 */
export function mergeConfig(source) {
  return Object.fromEntries(
    Object.entries(DEFAULT_CONFIG).map(([section, defaults]) => {
      const saved = source?.[section]
      const values = saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {}
      const known = FREE_SECTIONS.includes(section)
        ? values
        : Object.fromEntries(Object.entries(values).filter(([k]) => k in defaults))
      return [section, { ...defaults, ...known }]
    })
  )
}

function loadConfig() {
  try {
    const raw = localStorage.getItem(LS_KEY)
    return raw ? mergeConfig(JSON.parse(raw)) : DEFAULT_CONFIG
  } catch {
    return DEFAULT_CONFIG
  }
}

const ConfigContext = createContext(null)

export function ConfigProvider({ children }) {
  const [config, setConfig] = useState(loadConfig)

  const updateSection = useCallback((section, values) => {
    setConfig(prev => {
      const next = { ...prev, [section]: { ...prev[section], ...values } }
      try { localStorage.setItem(LS_KEY, JSON.stringify(next)) } catch {}
      return next
    })
  }, [])

  // Replace the whole config at once (applying a configuration profile).
  const replaceConfig = useCallback((next) => {
    try { localStorage.setItem(LS_KEY, JSON.stringify(next)) } catch {}
    setConfig(next)
  }, [])

  const resetConfig = useCallback(() => {
    try { localStorage.removeItem(LS_KEY) } catch {}
    setConfig(DEFAULT_CONFIG)
  }, [])

  return (
    <ConfigContext.Provider value={{ config, updateSection, replaceConfig, resetConfig }}>
      {children}
    </ConfigContext.Provider>
  )
}

export function useConfig() {
  const ctx = useContext(ConfigContext)
  if (!ctx) throw new Error('useConfig must be used within ConfigProvider')
  return ctx
}
