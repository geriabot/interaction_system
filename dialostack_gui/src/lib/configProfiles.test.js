import { describe, expect, it } from 'vitest'
import { DEFAULT_CONFIG } from '../contexts/ConfigContext'
import {
  applyProfileConfig, describeProfile, matchesProfile,
  profileFromJson, profileToJson, snapshotConfig,
} from './configProfiles'

const english = {
  ...DEFAULT_CONFIG,
  dialog: { ...DEFAULT_CONFIG.dialog, language: 'English', timeout_prompt: 'Take your time, I am still here.' },
  stt: { ...DEFAULT_CONFIG.stt, engine: 'whisper', whisper_model: 'medium' },
  tts: { ...DEFAULT_CONFIG.tts, engine: 'mms' },
  llm: { ...DEFAULT_CONFIG.llm, gemini_api_key: 'secret-en' },
}

describe('snapshotConfig', () => {
  it('never stores the API key', () => {
    expect(snapshotConfig(english).llm.gemini_api_key).toBeUndefined()
    expect(english.llm.gemini_api_key).toBe('secret-en') // input untouched
  })
})

describe('applyProfileConfig', () => {
  it('switches language, STT and TTS in one go and keeps the current key', () => {
    const current = { ...DEFAULT_CONFIG, llm: { ...DEFAULT_CONFIG.llm, gemini_api_key: 'mine' } }
    const next = applyProfileConfig(current, snapshotConfig(english))
    expect(next.dialog.language).toBe('English')
    expect(next.stt).toEqual({ engine: 'whisper', whisper_model: 'medium' })
    expect(next.tts.engine).toBe('mms')
    expect(next.llm.gemini_api_key).toBe('mine')
  })

  it('fills sections and keys missing from an old profile with defaults', () => {
    const next = applyProfileConfig({}, { stt: { engine: 'whisper' } })
    expect(next.stt.engine).toBe('whisper')
    expect(next.stt.whisper_model).toBe(DEFAULT_CONFIG.stt.whisper_model)
    expect(next.speech).toEqual(DEFAULT_CONFIG.speech)
  })

  it('loads a profile of the speech_io GUI, dropping the removed fields', () => {
    const old = {
      dialog: { language: 'Spanish' },
      stt: { model_size: 'medium', language: 'es', vad_threshold: 0.6, input_device: '' },
      tts: { model_path: '/models/es_ES-davefx-medium.onnx', sample_rate: 22050 },
      audio: { mode: 'browser', in_topic: '/audio_in' },
      prompts: { greeting: 'Hola' },
    }
    const next = applyProfileConfig({}, old)
    expect(next.dialog.language).toBe('Spanish')
    expect(next.stt).toEqual(DEFAULT_CONFIG.stt)
    expect(next.tts).toEqual(DEFAULT_CONFIG.tts)
    expect(next.audio).toBeUndefined()
    expect(next.prompts).toEqual({ greeting: 'Hola' })   // free map: every key is kept
    expect(matchesProfile(next, old)).toBe(true)
  })
})

describe('matchesProfile', () => {
  it('ignores secrets and detects real changes', () => {
    const profile = snapshotConfig(english)
    const current = applyProfileConfig({ llm: { gemini_api_key: 'other' } }, profile)
    expect(matchesProfile(current, profile)).toBe(true)
    current.stt = { ...current.stt, whisper_model: 'tiny' }
    expect(matchesProfile(current, profile)).toBe(false)
  })
})

describe('describeProfile', () => {
  it('summarises language, provider and speech engines', () => {
    expect(describeProfile(english)).toBe('English · gemini · whisper medium · mms')
  })

  it('says when the profile has no speech', () => {
    expect(describeProfile({ ...english, speech: { capability: 'none' } }))
      .toBe('English · gemini · typed text only')
  })
})

describe('profile JSON', () => {
  it('round-trips without leaking the key', () => {
    const text = profileToJson({ name: 'English', config: english })
    expect(text).not.toContain('secret-en')
    const back = profileFromJson(text)
    expect(back.name).toBe('English')
    expect(back.config.stt.engine).toBe('whisper')
  })

  it('accepts a bare config object and drops unknown sections', () => {
    const back = profileFromJson(JSON.stringify({ stt: { engine: 'whisper' }, junk: 1 }), 'x')
    expect(back).toEqual({ name: 'x', config: { stt: { engine: 'whisper' } } })
  })

  it('rejects files that are not a profile', () => {
    expect(() => profileFromJson('nope')).toThrow(/valid JSON/)
    expect(() => profileFromJson('{"foo":1}')).toThrow(/known configuration/)
  })
})
