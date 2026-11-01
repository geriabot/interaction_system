/** Dialogue tab: language, prompts, timeouts/limits and conversation logging. */
import { useEffect, useState } from 'react'
import { FileText, Languages, Timer } from 'lucide-react'
import { Field, NumberField, SectionCard, Select, SliderField, ToggleField, inputCls } from '../../components/ui'
import { api } from '../../lib/api'
import { languageOptions } from '../../lib/languages'

export function DialogTab({ dialog, update }) {
  const [engineLanguages, setEngineLanguages] = useState(null)
  useEffect(() => {
    let active = true
    api.phrases(dialog.language || 'English')
      .then((d) => active && setEngineLanguages(d.languages))
      .catch(() => {})
    return () => { active = false }
  }, [])
  const languages = languageOptions(engineLanguages, dialog.language)

  return (
    <div className="space-y-4">
      <SectionCard
        icon={Languages}
        title="Language"
        description="The language the robot speaks and reasons in"
      >
        <Field label="Dialogue language">
          <Select value={dialog.language} onChange={e => update({ language: e.target.value })}>
            {languages.map(l => <option key={l} value={l}>{l}</option>)}
          </Select>
        </Field>
        <ToggleField label="Acknowledgment phrases"
          hint={'Prefix slot-filling replies with a short line such as "Noted." or "Done.". Turn off with capable models, whose reply already reads naturally'}
          value={dialog.ack_enabled} onChange={v => update({ ack_enabled: v })} />
        <p className="text-xs text-slate-500 leading-relaxed">
          The canned phrases each strategy falls back to (greetings, acknowledgments,
          timeout, quiz feedback…) are edited in the <span className="text-slate-300">Strategies</span> page,
          per language.
        </p>
      </SectionCard>

      <SectionCard
        icon={Timer}
        title="Timeouts and limits"
        description="When to insist and when to abort the task"
      >
        <div className="grid grid-cols-3 gap-4">
          <NumberField label="Silence before a prompt" unit="s" min={5} max={120}
            hint="Silence of the user before each 'take your time' prompt"
            value={dialog.wait_timeout} onChange={v => update({ wait_timeout: v })} />
          <NumberField label="LLM timeout" unit="s" min={5} max={300}
            hint="Longest wait for one call to the LLM; a reply never waits more than the orchestrator's reply_budget (20 s)"
            value={dialog.llm_timeout} onChange={v => update({ llm_timeout: v })} />
          <NumberField label="TTS timeout" unit="s" min={5} max={120}
            value={dialog.tts_timeout} onChange={v => update({ tts_timeout: v })} />
          <NumberField label="Silence that ends it" unit="s" min={0} max={600}
            hint="Continuous silence after which the robot closes the dialogue (0 = never)"
            value={dialog.max_silence} onChange={v => update({ max_silence: v })} />
          <NumberField label="Turns without progress" min={0} max={20}
            hint="Turns in a row not understood before the robot closes the dialogue (0 = unlimited)"
            value={dialog.max_stalled} onChange={v => update({ max_stalled: v })} />
          <NumberField label="Attempts per element" min={1} max={10}
            hint="Askings of a slot, repetitions of a question, helps with a step, explanations"
            value={dialog.max_attempts} onChange={v => update({ max_attempts: v })} />
        </div>
        <SliderField
          label="Maximum turns in history"
          hint="How many turns the LLM session remembers before forgetting the old ones"
          value={dialog.history_max_turns}
          onChange={v => update({ history_max_turns: v })}
          min={20} max={500} step={10} />
      </SectionCard>

      <SectionCard
        icon={FileText}
        title="Logging"
        description="Conversation log on disk"
      >
        <div className="grid grid-cols-2 gap-4">
          <Field label="Log path" hint="Leave empty to disable logging">
            <input type="text" value={dialog.conversation_log_path}
              onChange={e => update({ conversation_log_path: e.target.value })}
              placeholder="/tmp/dialog_conversations.log"
              className={inputCls} />
          </Field>
          <NumberField label="Rotate when exceeding" unit="MB" min={0} max={1000}
            hint="0 = never rotate"
            value={dialog.conversation_log_max_mb} onChange={v => update({ conversation_log_max_mb: v })} />
        </div>
        <ToggleField label="Silent mode" hint="Suppresses the dialogue node logs in the console"
          value={dialog.silent_mode} onChange={v => update({ silent_mode: v })} />
      </SectionCard>
    </div>
  )
}
