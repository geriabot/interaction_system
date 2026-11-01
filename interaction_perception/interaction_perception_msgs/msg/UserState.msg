# The state of the user the robot is talking to, as perceived by the perception subsystem
builtin_interfaces/Time stamp
# Facial emotion: neutral | happy | sad | angry | frustrated | ... (empty: no estimate)
string emotion
float32 confidence
# Where the estimate comes from: vision, audio, inferred
string source
# Whether the user is speaking (lip activity)
bool speaking
