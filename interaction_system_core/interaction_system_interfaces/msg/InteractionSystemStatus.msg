# The state of the interaction system, published on /interaction_system/status
# - IDLE: no dialogue
# - REASONING: a request is being prepared (choosing the dialogue behavior)
# - RUNNING: a dialogue is running
# - FAILED: the last dialogue did not succeed (until the next request)
int8 IDLE=0
int8 REASONING=1
int8 RUNNING=2
int8 FAILED=3

int8 status
# The dialogue behavior running (e.g. SlotFilling), and the phase of its dialogue
string behavior
string phase
