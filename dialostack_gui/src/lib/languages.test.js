import { describe, expect, it } from 'vitest'
import { DEFAULT_LANGUAGES, languageOptions } from './languages'

describe('languageOptions', () => {
  it('offers the languages of the engine', () => {
    expect(languageOptions(['English', 'French', 'Spanish'], 'French')).toEqual(['English', 'French', 'Spanish'])
  })

  it('falls back to the shipped languages when the engine does not answer', () => {
    expect(languageOptions(null, 'English')).toEqual(DEFAULT_LANGUAGES)
    expect(languageOptions([], 'Spanish')).toEqual(DEFAULT_LANGUAGES)
  })

  it('keeps a selected language the engine does not list', () => {
    expect(languageOptions(['English', 'Spanish'], 'Catalan')).toEqual(['English', 'Spanish', 'Catalan'])
  })
})
