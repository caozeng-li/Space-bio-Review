# Evaluation protocol

Evaluation date: October 30, 2026. Research cutoff: October 28, 2026.
Evaluator: **ChatGPT Work (GPT-6 Astra Extra High)**.

## Dataset and reading procedure

Evaluate the delivered reports for 20 shared proposals from six methods: SpaceBioReview, Single Research Agent, Static Research Committee, Without Consultation, smolagents, and Thorne Section-wise. This yields 120 report assessments and 100 comparisons between SpaceBioReview and the other methods.

Read each proposal and record six reference points: three important contributions and three important limitations or dependencies, with proposal line references. Use the same reference points for all six reports. Read reports under per-project randomized aliases and examine cited sources when an external claim is decisive.

Assess each report's reasoning, interpretation of the proposal, treatment of external evidence, and overall judgment. Give credit for valid scientific insights beyond the reference points.

## Quality dimensions

Assign an integer score from 1 to 5 for each dimension:

| Dimension | Evaluation question |
| --- | --- |
| Overall review quality | Does the report identify the decisive factors and support a proportionate, useful overall judgment? |
| Scientific value recognition | Does it explain the original question, attainable knowledge, and contribution within the project's actual scope? |
| Judgment and grounding | Are the decisive proposal facts, evidence relationships, and inferences sound? |

Shared anchors: **1** = substantially misdirected or unreliable; **3** = useful but with an important gap; **5** = strong and well-supported. Scores of 2 and 4 represent intermediate quality.

Judge scientific arguments by their accuracy, relevance, and explanatory value. Assess each aim's contribution and dependencies within the proposal's scope. Distinguish prospective evidence from later outcomes of the same project.

## Pairwise comparisons

Rank paired reports by overall quality first, judgment and grounding second, and value recognition third. Equal score triples produce a tie. Record the outcome and its reason for every SpaceBioReview–comparator pair.

## Aggregation

Use all 20 proposals for quality means, score distributions, pairwise outcomes, and effect sizes. The primary outcome is overall review quality; report the other two dimensions separately.

Compute 95% percentile bootstrap intervals using 10,000 proposal-level resamples and random seed 20261030. Use the same resampled proposal indices for both methods in each comparison. Report paired differences as SpaceBioReview minus the comparator.

Summarize runtime on the 16 proposals with durations recorded for every method. Summarize report length on all 20 proposals, including references. Report resource medians and interquartile ranges.

## Outputs

Preserve report scores, comparison outcomes, rationales, source checks, and reference points as JSON. Generate the aggregate tables and detailed rationales as Markdown. Export five figures in PNG, PDF, and SVG:

1. Quality scores and confidence intervals.
2. Pairwise wins, ties, and losses.
3. Proposal-by-method quality heatmap.
4. Quality versus runtime and report length.
5. Paired overall-quality gains.
