---
name: literature-understanding
description: Discover and directly read original literature around project questions, understand scientific relationships, and connect them to the application; delegate to a reading assistant when supplementary interpretation is needed.
---

# Answering Scientific Questions Through Literature

Extract subjects, comparisons, implementation phases, and readouts from `application_basis` and the application as search constraints. Combine with the current `objective` and information use `impact` to find precedents or methods worth reading.

## Finding Useful Materials

Turn questions into a few complementary information needs: what recent experiments have already found, what this plan might add, what the method can actually measure, and which implementation conditions have direct justification. Use `search_sources` to discover candidates in batches; `information_need` should state the relationship needing explanation. Prioritize primary experiments, original methods, and practical platform materials; reviews can help locate papers. `related_papers` extends along references and citations.

For life sciences, prioritize Europe PMC; for interdisciplinary, use OpenAlex, Semantic Scholar; for materials physics, arXiv; consult Crossref for bibliographic records, Tavily for platform and institutional materials. Keywords use short scientific terms with necessary object qualifiers; semantic searches use complete questions. The scale of retrieval depends on the current question; stop when sufficient understanding is achieved.

## Choosing the Appropriate Reading Method

You directly read to determine the experimental relationships, key methods, or platform conditions for the current judgment, understanding what knowledge a certain comparison established, what observations a specific setup can yield under what conditions, and link them to the application to form a judgment. When further understanding is needed, continue reading methods, results, and related materials within the same research process.

When calling `acquire_source`, `reading_purpose` indicates the scientific information to be retrieved this time; the tool directly returns the relevant original text and entry points for continued reading. Leaving it blank retrieves the material and its table of contents. Use `retrieve_knowledge` to locate, `read_materials` to read, or `browse_materials` to browse continuously, building understanding along the original text's methods, results, and figures.

For complex figures, independent questions within long materials, or when supplementary interpretation is needed, use `read_source_for_question` to explicitly delegate to a reading assistant, providing the specific question and, if needed, starting paragraphs or attachments. The assistant delivers the relevant original text and interpretation, upon which you continue research; follow up on the same source using `reading_id`, and use `read_source_notes` to review previous explanations.

The reading draft's `review_interpretation` is an explanation of the source; the accompanying original text is its basis. Understand the subjects and conditions actually reported by the source, then judge how it supports the evaluation of the application's existing arrangements. What is given to the chief reviewer is the review opinion and necessary basis; the reading process and material explanations are retained in the research phase. When adopting original text, use the `[EvidenceN]` short marker from the corresponding `citation` field; the code maintains permanent reference identities and formal numbers.

When reading, connect subjects, treatments, comparisons, and actual observations. Include baseline, timepoint, and conditions for fold changes and effect sizes; when multiple factors vary together, explain the understanding this comparison can actually support. Clearly acknowledge demonstrated effects. An abstract can support the background it indeed reports; keep unread tissue boundaries and experimental details as pending.

When comparing different studies, first align subjects, interventions, time scales, and readouts, then explain common results and differences. Understand method applicability by tracing the signal generation, sampling, and analysis processes: what static endpoints, continuous trajectories, and functional measurements can each reveal. Pursue conditions and experiments sufficient to change the current understanding; conclude the search when acquired materials answer the question.
