/**
 * The dialogue languages offered by the Configuration editor: those with a
 * language file in the engine (interaction_intelligence/config/languages, sent
 * by the server with the phrases), from which main.launch.py also sets the STT
 * language and the TTS voice.
 */

// The languages the engine ships, used until it answers (and if it cannot)
export const DEFAULT_LANGUAGES = ['English', 'Spanish']

/** The languages to offer: the engine's, with the selected one always listed
 * (a configuration saved with a language the engine no longer has). */
export function languageOptions(engine, selected) {
  const list = Array.isArray(engine) && engine.length ? [...engine] : [...DEFAULT_LANGUAGES]
  if (selected && !list.includes(selected)) list.push(selected)
  return list
}
