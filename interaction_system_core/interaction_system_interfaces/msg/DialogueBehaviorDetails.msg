# A dialogue behavior of the catalog, as offered to the intelligence
int8 behavior_id
string name
# The dialog_mode it implements (slot_filling, explanation, quiz, procedure, roleplay)
string mode
# What the LLM reads to choose it
string explanation
# What the request must provide for it (question_bank, steps, scene)
string[] needs
