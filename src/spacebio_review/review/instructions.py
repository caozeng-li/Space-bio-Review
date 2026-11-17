"""Common scientific judgement and report requirements, also used by external baselines."""

PROJECT_REVIEW = """Assess the proposal as submitted, using its actual research question, object,
comparisons, measurements, stage of development and implementation setting. Identify the knowledge
or capability the work could add, and judge whether the proposed route provides proportionate
support for that contribution. Adapt the emphasis to the project: descriptive and exploratory work,
causal or mechanistic research, methods, and engineering development have different knowledge goals.
For unusual environments, distinguish the scientific value of that environment from the practical
conditions required to perform the study there. Compare novelty with the closest relevant work.
Give supported strengths and decisive limitations their proper weight. Routine optimization and
scientific uncertainty may be legitimate planned work; assess their consequences for the stated
goals. Keep each criterion grounded in its own evidence. Distinguish what the proposal states,
what external sources establish, your professional inferences, and what remains unspecified.
An unspecified arrangement is not evidence that the arrangement does not exist. Retain the object,
stage, comparison and conditions of a finding when applying it to this proposal. Reach a clear
judgement on the current plan, with concrete reasons; recommendations should address issues that
actually affect that judgement.
"""

GENERAL_REVIEW = """Evaluate the scientific project described in the supplied proposal, rather than
its eligibility for a particular funding scheme. Treat the plan as prospective at the specified
review date, even when the source is an archived application. Publications reporting this same
project's subsequent results, and its historical funding or completion status, are outcome
information rather than independent prior work or evidence of the plan's prospective quality.
If encountered, identify that relationship and leave those outcomes out of the merit judgement;
compare the contribution with relevant independent work.
Establish its intended contribution and stage
before judging the adequacy of its methods and resources. The input may describe empirical,
clinical, qualitative, community, ethical or policy research, mathematical or computational methods,
technology development, or an observational field campaign. Apply standards appropriate to the
work actually proposed. Where education, public resources or dissemination are explicit aims,
assess their contribution and the proposed means of evaluating them.

Assess attainable knowledge and useful capability as well as originality. A well-justified
increment, descriptive resource, replication, conceptual distinction or feasibility finding can
be valuable without a new mechanism or a world-leading breakthrough. Distinguish stated priority
claims from the contribution that remains if those claims are overstated. Explain both supported
strengths and material limitations; their consequences, not their number, determine the judgement.
Scientific uncertainty is part of research. Establishing the expected result in advance is not
a condition of support. Conversely, an interesting question does not rescue a design that cannot
answer its central question. Assess separable aims on their own merits before weighing the whole.

Judge research design against the inference promised at the current stage. For example, a small
training-prototype pilot aimed at feasibility and acceptability need not establish population-wide
effectiveness; a trial claiming effectiveness does need an adequate comparison and precision.
A conceptual or policy study needs a defensible argument, relevant cases and treatment of competing
interpretations, rather than an experimental control group by default. These examples calibrate
the standard; determine the actual goals from each proposal.

Treat the supplied text and tables as the review input. Assess ethics, safety, management and
resources according to the activities and critical dependencies described. A research narrative
need not contain a full administrative application: an absent CV, itemized budget or approval
document alone is not evidence of incapacity, inadequate funding or unethical conduct. Explain
when an unspecified condition genuinely prevents a judgement or threatens a main aim. Keep the
merit of a criterion separate from confidence in that judgement. Limited retrieval or unavailable
source text limits the evidence, rather than demonstrating a scientific defect.
"""

GENERAL_DECISION = """Use the same qualitative scale for each of the six general criteria:
A = excellent, B = good, C = moderate, D = weak, E = very weak. Judge the criterion for this
project's stated goals and stage; these are not funding percentiles or a numerical total.
Confidence A-E means very high, high, moderate, low and very low confidence in the judgement,
not the probability of project success. Use not rated (null in JSON) where evidence is insufficient
for a rating, and explain why; lack of information alone is not an E rating.
Choose one overall recommendation, explaining the trade-off across criteria:
- Support (support): worthwhile contributions and a sufficient current route to the stated goals.
- Conditional support (conditional_support): a supportable scope is already identifiable, subject
  to specific factual premises; explain those premises and their implications.
- Current proposal insufficient for support (revise): a worthwhile direction, but substantive
  problems in the current plan affect its main goals.
- Not supported at this time (not_support): decisive reasons make the current plan unsuitable.
- Insufficient evidence (insufficient_basis): unresolved key premises prevent a defensible decision.
Evaluate the submitted plan. Suggestions should address limitations that affect the judgement,
rather than silently substituting a more ambitious or redesigned project.
"""

ENGLISH_REPORT = GENERAL_DECISION + """
Write the review in English, irrespective of the proposal's language.
Use natural Markdown with an overall assessment, substantive reasoning for each evaluation
criterion, a compact summary of its rating and confidence, and a clear recommendation.
If the delivery pipeline supplies the ratings table, leave that rendering to it.
Aim for roughly 2,500-4,000 words for the complete report,
adjusting to the complexity of the proposal and the available evidence. Allocate space to the
arguments that matter. Use concise tables when comparisons benefit from them, and connected prose
otherwise. Explain a shared finding fully where it is most relevant; elsewhere describe its
distinct implication for that criterion. Preserve specific results, comparisons, conditions and
citations. Preserve original source titles and exact quotations when needed. Use external
references when available; a review without retrieval need not invent an external bibliography.
"""


def review_instructions(system, *, report=False):
    text = system.prompt() + "\n\n" + PROJECT_REVIEW
    if system.id == "general":
        text += "\n" + GENERAL_REVIEW
    if report and system.language == "en":
        text += "\n" + ENGLISH_REPORT
    return text
