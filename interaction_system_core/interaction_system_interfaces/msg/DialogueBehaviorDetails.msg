# A dialogue behavior of the catalog, as the intelligence receives it
int8 behavior_id
string name
# The dialog_mode it implements (slot_filling, explanation, quiz, procedure, roleplay,
# grounded_qa, questionnaire)
string mode
# What the LLM reads to choose it
string explanation
# What the request must provide for it (question_bank, steps, scene, material, instrument,
# reference)
string[] needs
