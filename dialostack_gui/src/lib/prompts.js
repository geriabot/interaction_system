/**
 * Client-side validation of prompt overrides - mirror of the server logic
 * (server.py `_valid_prompt_override`) and the node boot.
 *
 * Golden safety rule: an edited template may only use placeholders that already
 * appear in the default template (union {language} and {address}, which the
 * PromptBuilder gives every template). Any other would raise a
 * KeyError in `str.format` at runtime. Literal braces must also be doubled
 * ({{ }}), just like in the engine.
 */

/** Placeholders of a template (doubled braces {{ }} are literals). */
export function placeholders(tpl) {
  const stripped = tpl.replace(/\{\{|\}\}/g, '')   // strip literals
  const names = new Set()
  const re = /\{([^{}]*)\}/g
  let m
  while ((m = re.exec(stripped))) {
    const name = m[1].split(/[.:![]/)[0].trim()
    if (name) names.add(name)
  }
  return names
}

/** Any unbalanced braces left after stripping doubles and {x} fields? */
function bracesBalanced(tpl) {
  const noLiterals = tpl.replace(/\{\{|\}\}/g, '')
  const noFields = noLiterals.replace(/\{[^{}]*\}/g, '')
  return !/[{}]/.test(noFields)
}

/**
 * Returns an error message if the override is not safe, or null if it is valid.
 */
export function validateOverride(template, defaultTpl) {
  if (!bracesBalanced(template)) {
    return 'Unbalanced braces: use {{ and }} for literal braces.'
  }
  const used = [...placeholders(template)]
  const malformed = used.filter((n) => !/^[A-Za-z_]\w*$/.test(n))
  if (malformed.length) {
    return `Malformed placeholder: ${malformed.map((n) => `{${n}}`).join(', ')}`
  }
  const allowed = new Set([...placeholders(defaultTpl), 'language', 'address'])
  const unknown = used.filter((n) => !allowed.has(n))
  if (unknown.length) {
    return `Placeholder not available here: ${unknown.map((n) => `{${n}}`).join(', ')}`
  }
  return null
}

// Grouping of the prompts by strategy for the side list. Keys returned by
// the server that are not here fall into "Other" (robust to changes).
export const PROMPT_GROUPS = [
  // Not prompts: rules the PromptBuilder inserts into them ({address}; list_slot_rule only
  // when generate_response asks for a list slot)
  { label: 'Shared rules', keys: ['address_rule', 'list_slot_rule'] },
  { label: 'General', keys: ['classify_task_mode'] },
  { label: 'Slot filling', keys: [
    'create_frame', 'audit_frame', 'opening', 'resume_opening', 'extract_slots',
    'generate_response', 'ask_confirmation', 'classify_intent', 'response_to_intent',
    'answer_confirmation_question',
  ] },
  { label: 'Quiz', keys: ['quiz_opening', 'evaluate_quiz_answer', 'quiz_rephrase_question', 'quiz_next_question', 'quiz_summary'] },
  { label: 'Explanation', keys: [
    'explanation_opening', 'extract_key_points', 'judge_teach_back',
    'answer_explanation_question', 'rephrase_explanation', 'understanding_confirmed',
    'repair_explanation', 'targeted_question',
  ] },
  { label: 'Procedure', keys: [
    'procedure_opening', 'procedure_step', 'classify_step_turn', 'procedure_answer_question',
    'procedure_help_step', 'procedure_remind', 'procedure_summary',
  ] },
  { label: 'Roleplay', keys: [
    'roleplay_intro', 'roleplay_reply', 'classify_roleplay_turn', 'roleplay_coach',
    'evaluate_roleplay', 'roleplay_feedback',
  ] },
  { label: 'Grounded Q&A', keys: ['qa_opening', 'answer_from_material', 'verify_answer'] },
  { label: 'Questionnaire', keys: ['map_scale_answer'] },
  { label: 'Cancellation', keys: [
    'detect_cancel_intent', 'classify_cancel_reply', 'ask_cancel_confirmation',
    'cancel_confirmed_response',
  ] },
]

/** Orders the available keys by group; uncatalogued ones go to "Other". */
export function groupPrompts(availableKeys) {
  const set = new Set(availableKeys)
  const used = new Set()
  const groups = PROMPT_GROUPS
    .map((g) => {
      const keys = g.keys.filter((k) => set.has(k))
      keys.forEach((k) => used.add(k))
      return { label: g.label, keys }
    })
    .filter((g) => g.keys.length)
  const rest = availableKeys.filter((k) => !used.has(k))
  if (rest.length) groups.push({ label: 'Other', keys: rest })
  return groups
}
