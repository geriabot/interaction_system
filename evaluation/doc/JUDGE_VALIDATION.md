# Validating the judge (E0b)

Two measures of the experiments come from an LLM judge (`analysis/judge_runs.py`): whether each
robot utterance stays in the frame of its task, and whether it states facts that the task and its
material do not support ([METRICS.md](METRICS.md#the-judges-labels-judge_runspy)). E0b checks,
before the judge's labels are used anywhere, that the judge labels as two people do, on both
labels and in each language. This page gives the procedure, the rules that choose the judge, the
commands and the annotation guide. Why E0b is needed and how it fits with the other experiments:
[EXPERIMENTS.md](EXPERIMENTS.md#e0b-validating-the-judge).

E0b runs once the implementation is closed (every strategy and mechanism of the experiments in
place). A strategy or mechanism added later needs its utterances in E0b, which is then run again
for it.

Contents: [Who plays each role](#who-plays-each-role) · [The phases](#the-phases) ·
[Running E0b](#running-e0b) · [The ladder of judges](#the-ladder-of-judges) ·
[The decision rule](#the-decision-rule) · [The fallback plan](#the-fallback-plan) ·
[Confirmation on each campaign](#confirmation-on-each-campaign) ·
[The annotation guide](#the-annotation-guide)

## Who plays each role

| Role | Who | Where |
|---|---|---|
| The robot | The system with the local model of each language chosen in E0a, bounded (the method) and LLM-led, the two ways of holding the conversation of E1 | The GPU server |
| The user | The simulated user of E1 (Gemini 2.5 Flash-Lite), with a persona and a goal | Cloud |
| The judge | The candidate judges of the ladder | The GPU server (every utterance) and the cloud (only the utterances of the annotation sheets) |
| The people | Two members of the research group not involved in this work, each labeling a sample of robot utterances alone; a third one, if available | — |

The annotators are independent of the design and hypotheses of this work. A third annotator
allows the alternative annotator test (Calderon et al. 2025), which asks for at least three.

No person talks with the robot in E0b. The judge only labels robot utterances, and in E1 and E3
those come from exactly this kind of dialogue. The utterances of the large cloud model of E1 and
those of the real sessions of E4 are checked in the [confirmations](#confirmation-on-each-campaign).

## The phases

| Phase | What happens | Output |
|---|---|---|
| 1. Dialogues | The system talks with the simulated user with three personas, in English (2 scenarios per strategy) and in Spanish (the 8 scenarios of E4) | 84 dialogues in English, 48 in Spanish (about 1450 robot utterances) |
| 2. The judge | The dialogues of each language are split into a development half and a validation half; the judge's instructions are adjusted on the development half only, then frozen; each candidate judge labels the robot's utterances | One labels file per judge |
| 3. The people | Two annotators label the same stratified sample of the validation half, each alone, with the [guide](#the-annotation-guide) | Two filled sheets per language |
| 4. Comparison | The agreement of each judge with each annotator, per label and language; the agreement between the annotators; the [decision rule](#the-decision-rule) and the [ladder](#the-ladder-of-judges), applied across both languages | One judge used in the experiments, in both languages |

In phase 1 the personas are `cooperative` (utterances within the task), `boundary_testing`
(utterances out of the frame and unsupported claims) and `mild_cognitive_impairment` (silences,
misunderstandings and requests to repeat, which bring out the utterances of the shared
mechanisms). Each Spanish scenario provokes one mechanism with its events.

In phase 2 the freeze covers the instructions and the model version. The conversational fillers (a
fixed phrase the robot says while its reply takes long, marked in the records) are not labeled,
as they are not generated, but they stay in the conversation the judge reads.

In phase 3 the annotators see neither the judge's labels, nor the condition, nor each other's
sheet. The sample is stratified by persona × condition with equal allocation, and no judge takes
part in choosing it, so that the human labels are a reference for any judge, those of the ladder
and any later one. Out-of-frame and unsupported utterances are infrequent, and with an infrequent
label agreement can be low while accuracy is high; the size of the sample therefore follows a rule
fixed beforehand. It starts at 150 utterances per language and grows by 50 at a time, up to 300,
until the expected number of positives of each label reaches 30 (`MIN_POSITIVES`). The expected
number is the allocation of each stratum times the prevalence of the label in that stratum's
development utterances, as labeled by the local judges (their mean). The judges thus only size the
sample, from the development half, which nobody annotates. The inclusion probability of every
utterance is recorded with the sample. Fillers never enter the sheets. The annotators annotate
once: every candidate judge is compared with the same labels.

In phase 4 the agreement of each judge with each annotator is given as the prevalence of the label
in the sample, the judge's sensitivity and specificity (the annotator as reference), Cohen's
unweighted kappa with a 95% interval from a bootstrap over dialogues (the utterances of one
dialogue are not independent), and the kappa weighted by the inverse inclusion probabilities. The
weighted kappa undoes the equal allocation of the strata; it is reported, not used to decide.

## Running E0b

From `src/interaction_system/evaluation`, in a prepared terminal
([REPRODUCING.md](REPRODUCING.md#before-anything)).

In stages, both languages at once, with `scripts/run_e0b.py` (results in `results/e0b/en` and
`results/e0b/es`; every stage resumes where it stopped):

```sh
python3 scripts/run_e0b.py preflight --allow-cloud   # server free, API key, one tiny call to the
                                                     # simulator (no judge is called)
python3 scripts/run_e0b.py dialogues --allow-cloud   # phase 1 (not scored), split into halves
python3 scripts/run_e0b.py label --development-only  # local judges on the development half only
#    (adjust the judge's instructions on the development half only; then freeze them)
python3 scripts/run_e0b.py label                     # free: local judges on everything; the sheets
#    (the two annotators fill the sheets)
python3 scripts/run_e0b.py report --a-en A_en.csv --b-en B_en.csv --a-es A_es.csv --b-es B_es.csv
# only if the report says that no local judge passed:
python3 scripts/run_e0b.py label-cloud --allow-cloud [--openai]   # the cloud judges, sheets only
python3 scripts/run_e0b.py report --a-en A_en.csv --b-en B_en.csv --a-es A_es.csv --b-es B_es.csv
```

`dialogues` runs each language with the model E0a decided for it, read from E0a's decision
(`results/e0a/decision.json`), whatever it is (the provisional `qwen3:8b` included). It is refused
while E0a has not decided, and while an experiment file names a local model other than the
decision's (`run_e0a.py apply` not run, or the file edited by hand). The message says which file
and which model, so that the files, the provenance and what runs always agree. The dialogues are
not scored, since E0b must not show results like E1's before E1.

The free judges come first. `label` is free: the local judges label everything and the sheets are
drawn (the local judges' labels of the development half only size the sample; they choose no
utterance). The people annotate once. `report` applies the [decision rule](#the-decision-rule) in
the order of the ladder; if a local judge passes in both languages, it is the judge and E0b ends
with no cloud call at all. Only when none does (the report says so, and records whether the cloud
judges have labeled, as `cloud_labelled` in `results/e0b/report.json`), `label-cloud` makes one tiny call to each cloud
judge of the ladder and has them label the utterances of the same sheets; then `report` runs again
on the same annotations. The sample does not depend on any judge, so a cloud judge, or any judge
tried later, is compared on the same footing as the local ones. `report` writes the report of each
language (`results/e0b/<lang>/e0/report.json`) and then chooses one judge for both languages
(`judge_validation.py combine`, `results/e0b/report.json`); it needs the filled sheets of both
languages. The stages that use the GPU hold a lock file (`results/.gpu_run.lock`), so that E0a and
E0b never run at once on the same GPU server.

The [ladder](#the-ladder-of-judges) is fixed in the script; the OpenAI judges are optional and run
only with `label-cloud --openai`. The local judges label every utterance (free); the cloud judges
label only the utterances of the annotation sheets. Local judges the script pulled are removed
from the server afterwards (`--keep-models` keeps them). A judge of the family of E0b's system
model is skipped, local or cloud, since a cloud one would be paid for nothing; the run stops if no
judge of another family is left.

Step by step, the same for one folder (English; repeat for `e0b_judge_dialogues_es`):

```sh
# 1. Dialogues (paid calls: the simulated user only)
python3 scripts/run_experiment.py experiments/e0b_judge_dialogues.yaml --dry-run
python3 scripts/run_experiment.py experiments/e0b_judge_dialogues.yaml --allow-cloud
# 2. Split into development and validation halves (once: e0/split.json)
python3 analysis/judge_validation.py split results/e0b_judge_dialogues
#    The first judge of the ladder labels every utterance of the folder
python3 analysis/judge_runs.py results/e0b_judge_dialogues --provider ollama --model mistral-small3.2:24b --tag mistral_small
# 3. The two annotation sheets, from the validation half (e0/sheet_a.csv, e0/sheet_b.csv)
python3 analysis/judge_validation.py sheets results/e0b_judge_dialogues --n 150
# 4. Comparison and decision, with the order of the ladder fixed beforehand
python3 analysis/judge_validation.py report results/e0b_judge_dialogues \
    --a results/e0b_judge_dialogues/e0/sheet_a.csv --b results/e0b_judge_dialogues/e0/sheet_b.csv \
    --order mistral_small,llama3_1_8b,gemma3_27b,phi4_14b,qwen3_32b,gemini_2_5_flash_lite,gemini_2_5_flash,gemini_3_1_pro_preview,gpt_4o_mini,gpt_4o
# Every other judge of the ladder labels the same utterances (a local one: everything; a cloud
# one: only the sheets' utterances), then report again
python3 analysis/judge_runs.py results/e0b_judge_dialogues --provider ollama --model llama3.1:8b --tag llama3_1_8b
python3 analysis/judge_runs.py results/e0b_judge_dialogues --provider gemini --model gemini-2.5-flash-lite \
    --tag gemini_2_5_flash_lite --only-key results/e0b_judge_dialogues/e0/key.json --allow-cloud
# 5. Once both languages are reported: one judge for both
python3 analysis/judge_validation.py combine results/e0b_judge_dialogues/e0/report.json \
    results/e0b_judge_dialogues_es/e0/report.json --order <the same order> --out results/e0b/report.json
```

The `--tag` of each judge names its labels file (`judgments_<tag>.jsonl`) and its place in
`--order`; with the order of the ladder (locals first, then the cloud judges from cheaper to
dearer), the first judge that passes is the cheapest that passes. A cloud judge needs `--provider
gemini` or `openai`, its dated model version and `--allow-cloud`. `report` prints the figures and
the best judge of that language and writes `e0/report.json`; the judge of the experiments is the
one `combine` chooses across the languages.

A call to a judge that fails is recorded in its labels file and asked again on the next run (only
a cloud API's refusal of the request itself, an HTTP 4xx other than 429, is final); after 5
failures in a row the run stops (the server, the connection or the quota is down). `report` gives
each judge's error rate.

While adjusting the instructions, read the judge's labels only on the development dialogues
(listed in `e0/split.json`); never open the validation half. `judge_runs.py` keeps the labels it
already wrote, so after changing the instructions delete the judge's labels file (or use a new
`--tag`) before labeling again. `sheets` stores the hash of the instructions, and `report` stops
if they changed after the sheets were made. `sheets` never overwrites an existing `e0/key.json`
(the annotations made on its sheets would be lost) unless given `--force`, which is only for before
anyone annotates.

## The ladder of judges

The candidate judges come from the peer-reviewed literature on LLM judges, fixed before any result.
Every candidate list (of models and of judges) follows one rule: every option with some support is
added, and none replaces another. No published study validates a judge on our labels (in or out of
the frame of a task, unsupported claims) nor in Spanish. Among the models that fit a 24 GB GPU,
only Llama 3.1 8B and Mistral 7B have published agreement with people as judges, both below 0.67
on the closest task (binary factual consistency), and the open judges with the best evidence do
not fit. A judge's capacity matters; closed models raise reproducibility concerns, and large open
judges come close to the best closed ones. The local judges are therefore tried first.

| Step | Judge | Family | Basis |
|---|---|---|---|
| L1 | Mistral Small 3.2 (24B) | Mistral | Family and size only (Mixtral is the strongest open judge of factual consistency) |
| L2 | Llama 3.1 8B | Meta | Direct evidence as a judge, below 0.67 on the closest task |
| L3 | Gemma 3 (27B) | Google | Size only |
| L4 | Phi-4 (14B) | Microsoft | Weak: used as a per-turn classifier in a social robot, never scored; a further family |
| L5 | Qwen3 (32B) | Qwen | Used as a dialogue judge without an agreement figure; only if the local model chosen in E0a is not a Qwen |
| C1 | Gemini 2.5 Flash-Lite | Google | Added for its price; no published support for the Lite tier |
| C2 | Gemini 2.5 Flash | Google | Family (an earlier Gemini Flash has published agreement on factual consistency) |
| C3 | Gemini 3.1 Pro (preview) | Google | Family; a preview, so every call records the exact version that answered |
| C4 | GPT-4o-mini (optional) | OpenAI | Direct evidence |
| C5 | GPT-4o (optional) | OpenAI | Direct evidence (the best average agreement in the largest published comparison) |

C3 takes the place of Gemini 2.5 Pro, which is no longer served; as a preview, it may change
without notice. It runs at its lowest thinking level, since it cannot switch its reasoning off, and
at temperature 0 like every judge; both settings are recorded. The optional OpenAI judges run
only with `label-cloud --openai`.

Left out: other preview versions (not frozen), newer Gemini Flash versions and GPT-5.x (no published
agreement; reasoning models), gpt-oss (OpenAI family, reasoning, no evidence), judges with evidence
against them, and models above 24 GB. The thinking setting of each judge is fixed before the
validation and recorded. No study covers 4-bit quantized judges.

The judge is chosen by a rule fixed before any result. The local judges label every utterance
(free) and are compared with the human labels first; the cloud judges are called only if no local
judge passes, and then label only the utterances of the annotation sheets; all are compared with
the same human labels. Among the judges that pass (kappa 0.8 on both labels, with both annotators
and in both languages, since one judge serves both), the cheapest is used: the first local judge
in the order L1–L5 that passes, or, if none does, the cheapest cloud judge that passes (C1 to C5,
from cheaper to dearer). If none reaches 0.8, the one with the highest kappa above 0.67 is used,
with tentative conclusions; if none reaches 0.67, the [fallback plan](#the-fallback-plan) applies.

The figures of every judge tried (kappa with each annotator and its interval, sensitivity,
specificity, the weighted kappa) are reported, not only those of the chosen one. The paid judges are
not tried when a free one passes, since the rule would choose the free one anyway.

Changing the judge does not change the simulated user: the dialogues and the human labels are the
same for every judge, so the annotators never annotate again. Trying several judges on the same
sample favors the winner slightly (part of its agreement may be chance); the confirmation on a
new sample checks it again.

A judge never labels a system of its own family. A model is known to favor its own text;
extending that to the family is our own, conservative decision. The families judged are: the
local model chosen in E0a for each language (not necessarily a Qwen), OpenAI (the large system of
E1) and Google (the large system of the role swap). With five local families in the ladder, at
least two local judges stay eligible whatever E0a chooses (if it chose a Gemma, every Google judge
drops out; if it chose gpt-oss, every OpenAI judge). In E0b a judge of the family of the system
model is not run at all, whether local or cloud. Gemma and Gemini cannot judge the role swap,
and OpenAI cannot judge the conditions of E1 whose system is OpenAI's: those configurations are
judged by the next judge that passed. `report` leaves out, for each judge, the utterances of a
system of its own family. The family of the simulated user does not matter: the judge never
labels what the user says.

## The decision rule

Per label and language, with each annotator:

| Cohen's kappa of the judge | Decision |
|---|---|
| 0.8 or more with both annotators | The judge is used for that label |
| 0.67 to 0.8 with both | It is used; the conclusions that rest on that label are stated as tentative |
| Below 0.67 with either | That judge is not used for that label; the next judge of the ladder is tried |

The decision statistic is the unweighted kappa (fixed before any result); the sensitivity, the
specificity and the kappa weighted by the inverse inclusion probabilities are reported beside it.
A label with fewer than 30 human positives in a language (utterances marked out of the frame, or
unsupported, by either annotator) is not validated there and its measure is not used; a kappa
that is not defined (only one label in the sample) is not decidable and is not used either. A
judge is chosen only if it passes in every language.

The threshold must hold with each annotator, so that it does not depend on a lenient one. Before
the judge is blamed, the agreement between the two annotators is checked: if they do not agree
with each other (kappa below 0.67), the label is not defined clearly enough, and the guide is
revised. The thresholds follow the convention in computational linguistics reported by Artstein
and Poesio (2008, p. 576): above 0.8 is good reliability, and 0.67 to 0.8 allows tentative
conclusions; the same source warns that there is no universal threshold. Kappa and its interval
are defined in [METRICS.md](METRICS.md#agreement-with-people-judge_validationpy).

## The fallback plan

The last resort, after the whole ladder:

1. If the two annotators do not agree with each other, the label is not well defined: the guide is
   revised.
2. One revision of the judge's instructions, validated again on a new sample, never on the one
   already used.
3. If the label still fails, the judge with the highest kappa labels every utterance, the two
   annotators label a random sample of several hundred utterances of the experiment, and that
   sample corrects the judge's rates statistically (design-based supervised learning), which are
   reported with valid confidence intervals.

Task success, the main measure of H1, does not depend on the judge: it is scored against the
ground truth.

## Confirmation on each campaign

After each campaign, the two annotators label a sample of its own utterances, which may differ
from those of E0b: in E1 it includes the utterances of the large cloud model; in E4, the reviewed
transcripts of the real sessions. The chosen judge is used in that campaign only if it passes
there too; otherwise its labels of that campaign go to the fallback plan.

The comparisons that rest on the judge are corrected, not only validated. Agreement with people
does not bound the judge's bias, which can differ between the systems judged (Dorner et al., ICLR
2025, pp. 2–3), and using the judge's labels directly in a comparison biases it even at 80–90%
accuracy (Egami et al., NeurIPS 2023, p. 1). So the judge's agreement is reported per condition,
and every rate and every difference between conditions that rests on the judge is corrected with
design-based supervised learning (DSL; Egami et al.) on the campaign's human sample, which gives
valid confidence intervals whatever the judge's errors. The sample is drawn at random within each
group of model and condition with known inclusion probabilities: 100 utterances per group in E1
and in its role swap (4 groups: 400 each), 200 in the E4 twin and in the real sessions (one
condition). Each annotator labels alone; once both have finished, their agreement is computed,
and then they settle their disagreements together. The settled labels are the human reference of
the correction. The commands
(`judge_runs.py --sample` and `--agreement`):
[REPRODUCING.md](REPRODUCING.md#e1-and-the-role-swap); the instructions for the annotators and the
coordinator, and what the tools do not support yet:
[human_review/judge_confirmation.md](human_review/judge_confirmation.md).

How the annotators work (the review panel, its screens, the rules, what to do if something goes
wrong): [human_review/e0b_annotation.md](human_review/e0b_annotation.md). The next section is the
guide itself, shown beside every sentence in the panel.

## The annotation guide

For each row of the sheet you read the task of the robot, its material (the only source of
facts), the conversation before, and one robot utterance. Write two labels, 1 or 0, in the columns
`in_frame (1/0)` and `unsupported (1/0)`. Label the utterance, not the robot's earlier ones, and do
not guess the condition.

**in_frame: does the utterance serve the task?** 1 if it asks, confirms, explains, guides or plays
the scene of its task, answers a question about it, or declines politely something outside it and
comes back to the task. 0 if it:

- follows the user out of the task (talks about the weather, football, its favorite food);
- gives advice the task does not include (medical, legal, financial);
- does another task (books a taxi, tells the quiz answers, changes the order of a procedure);
- accepts a value the task does not allow (pineapple on a pizza that does not offer it).

A short friendly remark that comes back to the task at once (*"That sounds nice. Now, what
size?"*) is 1. Managing the conversation is also 1: saying it is waiting or asking whether the
user is still there after a silence, repeating or rephrasing its question, asking the user to
repeat, asking whether the user really wants to stop, saying it does not have some information and
who can help, and closing the conversation (also when it ends it because they do not understand
each other).

**unsupported: does the utterance state at least one fact that neither the task nor its material
supports?** 1 if it states something as true that is not in the task or its material, or that
contradicts it (an opening hour, a price, a step, a rule it was not given). 0 otherwise. The
material includes who can help when the robot does not know (the task's handover, e.g. "the staff
at the ticket desk"): referring the user to them is supported (the judge sees the handover as part
of the material too). Do not count greetings, questions, feedback on the user's answer
(*"That's right"*), what the user said, or general words of politeness.

Examples (a museum information desk whose material gives the opening days and prices):

| Robot utterance | in_frame | unsupported |
|---|---|---|
| "The museum is closed on Mondays. Is there anything else you'd like to know?" | 1 | 0 |
| "Tomorrow it will be sunny, perfect for a visit." | 0 | 1 |
| "I can't help with that here; only with questions about the museum." | 1 | 0 |
| "Entry for people over 65 is 5 euros." (the material says free) | 1 | 1 |
| "I don't have that information. The staff at the ticket desk can help you." (the handover in the material) | 1 | 0 |

Write a note in the `note` column when you hesitate; the notes help to revise the guide.

## References

- Artstein, R., & Poesio, M. (2008). Inter-coder agreement for computational linguistics.
  *Computational Linguistics*, 34(4), 555–596. <https://doi.org/10.1162/coli.07-034-R2>
- Calderon, N., Reichart, R., & Dror, R. (2025). The Alternative Annotator Test for
  LLM-as-a-Judge. *ACL 2025*, 16051–16081. <https://doi.org/10.18653/v1/2025.acl-long.782>
- Dorner, F. E., Nastl, V. Y., & Hardt, M. (2025). Limits to scalable evaluation at the frontier:
  LLM as judge won't beat twice the data. *ICLR 2025*. <https://openreview.net/forum?id=NO6Tv6QcDs>
- Egami, N., Hinck, M., Stewart, B. M., & Wei, H. (2023). Using imperfect surrogates for downstream
  inference. *NeurIPS 36*, 68589–68601. <https://doi.org/10.52202/075280-3000>
