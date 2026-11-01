/** TTS tab: simple_hri's speech synthesis engine. */
import { Volume2 } from 'lucide-react'
import { Field, SectionCard, Select } from '../../components/ui'

export function TTSTab({ tts, update, speech }) {
  if (speech.capability === 'none') {
    return (
      <SectionCard icon={Volume2} title="Speech synthesis" description="Not started">
        <p className="text-xs text-slate-500 leading-relaxed">
          The speech capability is off (STT tab): the robot's replies are only shown in
          the Monitor.
        </p>
      </SectionCard>
    )
  }

  return (
    <div className="space-y-4">
      <SectionCard
        icon={Volume2}
        title="Speech synthesis"
        description="simple_hri's text-to-speech"
      >
        <Field label="Engine"
          hint="Piper runs on the CPU with a neural voice; MMS-TTS (facebook/mms-tts-*) runs on the GPU when there is one">
          <Select value={tts.engine} onChange={e => update({ engine: e.target.value })}>
            <option value="piper">Piper</option>
            <option value="mms">MMS-TTS</option>
          </Select>
        </Field>
        <p className="text-xs text-slate-500 leading-relaxed">
          The voice follows the dialogue language, as its language file sets it
          (<span className="font-mono">interaction_intelligence/config/languages</span>): Piper uses{' '}
          <span className="font-mono">en_US-lessac-medium</span> or{' '}
          <span className="font-mono">es_ES-sharvard-medium</span>, MMS-TTS{' '}
          <span className="font-mono">mms-tts-eng</span> or <span className="font-mono">mms-tts-spa</span>.
          The audio plays on the default speaker of the machine running the engine.
        </p>
      </SectionCard>
    </div>
  )
}
