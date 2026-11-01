/**
 * Strategy metadata for the Strategies page: what each dialogue strategy does,
 * which canned phrases belong to it, and which Frame Builder kind builds its
 * payload. The phrase keys mirror the engine's per-language set (the `phrases`
 * of interaction_intelligence/config/languages/<language>.yaml) plus the
 * slot-filling `ack` list. Anything not listed here still shows under "Other".
 */

// Human labels + an optional hint per phrase key (the raw text is what the
// robot says when the LLM is unavailable, in the active dialogue language).
export const PHRASE_LABELS = {
  opening: 'Opening / greeting',
  resume: 'Resume (continuing a task)',
  unclear: "Didn't understand",
  timeout: 'User silent: first prompt (take your time)',
  silence_ready: 'User silent: later prompt (whenever you are ready)',
  good_question: 'Acknowledge a user question',
  intent_response: 'Generic intent reply',
  cancel_ask: 'Cancel — confirm',
  cancel_ok: 'Cancel — confirmed',
  cancel_no: 'Cancel — dismissed (then the pending question)',
  close_user_silent: 'Closing — the user stayed silent',
  close_breakdown: 'Closing — communication breakdown',
  close_turn_limit: 'Closing — turn limit reached',
  close_error: 'Closing — failure of the system',
  confirm_q: 'Confirm collected data',
  slot_skipped: 'A slot left blank',
  confirmed_closing: 'Closing of a confirmed form (if the model asked again)',
  explain_open: 'Explanation opening',
  teach_back: 'Teach-back: ask to say it back',
  teach_back_again: 'Teach-back: ask again after a repair',
  key_points_summary: 'Closing reminder of the key points ({points})',
  understand_ok: 'Understood — proceed',
  understand_fail: 'Gave up explaining',
  quiz_opening: 'Quiz opening',
  quiz_summary: 'Quiz summary',
  quiz_correct: 'Correct answer',
  quiz_wrong: 'Wrong answer ({expected})',
  quiz_unanswered: 'A question left unanswered',
  procedure_opening: 'Procedure opening',
  procedure_step: 'Present a step ({n}, {total}, {step})',
  procedure_remind: 'Remind to report the step',
  procedure_summary: 'Procedure summary',
  roleplay_intro: 'Roleplay introduction',
  roleplay_reply: 'Character line (LLM down)',
  roleplay_coach: 'Hint for the user',
  roleplay_feedback: 'Closing feedback',
  roleplay_scores: 'Scores sentence ({scores})',
  roleplay_score_item: 'One score ({criterion}, {score})',
  qa_invite: 'Invitation to ask (without greeting)',
  qa_more: 'Anything else?',
  qa_abstain: 'Not in the information',
  qa_decline: 'Outside the task',
  qa_bye: 'Closing when there are no more questions',
  questionnaire_intro: 'Introduction without instructions ({total})',
  questionnaire_item: 'Before each item ({n}, {total})',
  questionnaire_options: 'The options ({options})',
  questionnaire_or: 'The word before the last option',
  questionnaire_end: 'Closing of the questionnaire',
}

// The slot-filling acknowledgments are a LIST (a random one is spoken after a
// slot is filled), edited as one-per-line, stored as an array.
export const ACK_KEY = 'ack'

export const STRATEGIES = [
  {
    id: 'shared',
    label: 'Shared',
    frameKind: null,
    summary: 'Lines any strategy can use.',
    desc: 'Greeting, resume, "I didn’t catch that", the lines while the user is silent, the closing lines of every ending and the whole cancel flow. These apply to every strategy.',
    keys: [
      'opening', 'resume', 'unclear', 'timeout', 'silence_ready', 'good_question',
      'intent_response', 'cancel_ask', 'cancel_ok', 'cancel_no',
      'close_user_silent', 'close_breakdown', 'close_turn_limit', 'close_error',
    ],
  },
  {
    id: 'slot_filling',
    label: 'Slot filling',
    frameKind: 'slot_filling',
    summary: 'Collects structured data slot by slot, then confirms.',
    desc: 'Asks for each slot the task needs, extracts values from free speech and confirms the result. Define its slots (and optional context resources) in the Frame Builder.',
    keys: ['confirm_q', 'slot_skipped', 'confirmed_closing'],
    ack: true,
  },
  {
    id: 'explanation',
    label: 'Explanation',
    frameKind: 'explanation',
    summary: 'Explains reference material and checks understanding.',
    desc: 'Presents material to the user and verifies they understood, rephrasing on demand. Provide the material as resources in the Frame Builder.',
    keys: ['explain_open', 'teach_back', 'teach_back_again', 'understand_ok', 'understand_fail', 'key_points_summary'],
  },
  {
    id: 'quiz',
    label: 'Quiz',
    frameKind: 'quiz',
    summary: 'Asks a question bank and scores the answers.',
    desc: 'Runs through a bank of questions, judges each answer and summarises the score. Build the question bank in the Frame Builder.',
    keys: ['quiz_opening', 'quiz_summary', 'quiz_correct', 'quiz_wrong', 'quiz_unanswered'],
  },
  {
    id: 'procedure',
    label: 'Procedure',
    frameKind: 'procedure',
    summary: 'Guides the user through ordered steps, one at a time.',
    desc: 'Presents each step, waits until the user reports it done, answers questions, helps when they are stuck and lets them repeat, go back or skip. Define the steps in the Frame Builder.',
    keys: ['procedure_opening', 'procedure_step', 'procedure_remind', 'procedure_summary'],
  },
  {
    id: 'roleplay',
    label: 'Roleplay',
    frameKind: 'roleplay',
    summary: 'Acts out a scene so the user can practise, then scores them.',
    desc: 'The robot plays a character (an interviewer, a waiter, a patient...) while the user practises. They can ask for a hint at any moment; at the end the robot steps out of the role and scores each criterion of the rubric. Define the scene in the Frame Builder.',
    keys: ['roleplay_intro', 'roleplay_reply', 'roleplay_coach', 'roleplay_feedback', 'roleplay_scores', 'roleplay_score_item'],
  },
  {
    id: 'grounded_qa',
    label: 'Grounded Q&A',
    frameKind: null,
    summary: 'Answers the user\'s questions only from the given information.',
    desc: 'The user asks; the robot answers only with facts of the material (each answer verified against it), says when it does not have the information (offering the handover, if given) and declines what is outside the task.',
    keys: ['qa_invite', 'qa_more', 'qa_abstain', 'qa_decline', 'qa_bye'],
  },
  {
    id: 'questionnaire',
    label: 'Questionnaire',
    frameKind: null,
    summary: 'Administers a standardized questionnaire, word for word.',
    desc: 'Reads each item and its options as written, records the option the user chooses (never choosing for them), gives the instrument\'s clarifications and computes the score, which it does not say.',
    keys: ['questionnaire_intro', 'questionnaire_item', 'questionnaire_options', 'questionnaire_or', 'questionnaire_end'],
  },
]

export const phraseLabel = (key) => PHRASE_LABELS[key] || key
