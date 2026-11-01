/** STT tab: the speech capability (simple_hri or none) and simple_hri's speech recognition. */
import { Mic, Radio } from 'lucide-react'
import { Field, SectionCard, Select } from '../../components/ui'

// Same values as server.py (WHISPER_MODELS), which falls back to small otherwise.
const WHISPER_MODELS = ['tiny', 'base', 'small', 'medium', 'large-v3']

export function STTTab({ stt, update, speech, updateSpeech }) {
  const enabled = speech.capability !== 'none'

  return (
    <div className="space-y-4">
      <SectionCard
        icon={Radio}
        title="Speech capability"
        description="How the robot listens and speaks"
      >
        <Field label="Speech"
          hint="simple_hri listens when the dialogue asks and speaks through the microphone and speaker of the machine running the engine. Without speech the user talks by typing in the Monitor">
          <Select value={speech.capability} onChange={e => updateSpeech({ capability: e.target.value })}>
            <option value="simple_hri">simple_hri (microphone + speaker)</option>
            <option value="none">None (typed text only)</option>
          </Select>
        </Field>
        <p className="text-xs text-slate-500 leading-relaxed">
          {enabled
            ? 'The STT language and the TTS voice follow the dialogue language. The models are downloaded the first time into ./models of the directory the GUI was started from.'
            : 'No speech nodes are started: type in the Monitor composer and read the robot\'s replies in the timeline.'}
        </p>
      </SectionCard>

      {enabled && (
        <SectionCard
          icon={Mic}
          title="Speech recognition"
          description="simple_hri's speech-to-text"
        >
          <div className="grid grid-cols-2 gap-4">
            <Field label="Engine"
              hint="faster-whisper with Silero VAD is faster and lighter; Whisper uses webrtcvad. Both run on the GPU when there is one">
              <Select value={stt.engine} onChange={e => update({ engine: e.target.value })}>
                <option value="faster_whisper">faster-whisper</option>
                <option value="whisper">Whisper</option>
              </Select>
            </Field>
            <Field label="Whisper model"
              hint="Larger = more accurate but slower and more memory. small is a good balance">
              <Select value={stt.whisper_model} onChange={e => update({ whisper_model: e.target.value })}>
                {WHISPER_MODELS.map(m => <option key={m} value={m}>{m}</option>)}
              </Select>
            </Field>
          </div>
        </SectionCard>
      )}
    </div>
  )
}
