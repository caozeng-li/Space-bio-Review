# Scientific Project Evaluation

## Lymphoid Signals for Stromal Growth and Organization in the Thymus

**Principal Investigator:** Howard T. Petrie, The Scripps Research Institute  
**Mechanism:** R21 (exploratory/developmental grant)  
**Planned Duration:** Two years  
**Review Date:** 2026-10-28  

---

## Overall Assessment

This proposal addresses a genuine and important gap in thymus biology: the reciprocal signals from developing lymphocytes that sustain the growth, organization and survival of thymic stromal cells. The dominant research tradition has characterized stromal-to-lymphoid instructions; the reverse direction remains less systematically studied, despite longstanding evidence that lymphoid cells are required for normal thymic architecture. The project aims to produce a dynamic expression resource, a searchable and prioritized collection of candidate lymphoid-to-stromal signaling interactions, and initial biological validation in existing mouse models.

The proposal's strengths are its well-justified biological question, the elegant non-myeloablative transplantation model that avoids injury to the stromal compartment under investigation, and the logical architecture connecting expression discovery through computational prioritization to focused biological testing. The laboratory has published the core methods and the principal investigator has deep expertise in thymus biology.

The main limitations affecting the scientific judgement are: (1) the reliance on a now-dated microarray platform (Affymetrix MOE430_2) whose probe set coverage and dynamic range are substantially inferior to RNA-seq approaches that are standard as of 2026; (2) the indirect nature of stromal expression estimation, which conflates multiple stromal cell types and cannot resolve the cellular heterogeneity now well-documented in the thymic epithelium; (3) an ambitious schedule that compresses both extensive experimental work and computationally intensive analysis into a two-year R21 mechanism; and (4) the dependence of Aim 4 on candidate selection from Aims 1–3, leaving the concrete validation design unspecified at the proposal stage.

These concerns temper but do not negate the proposal's value. The expression resource and candidate database would be useful contributions even if the biological validation arm is only partially realized. The design is appropriate for an exploratory grant that explicitly frames Aim 4 as proof of concept.

---

## Scientific Value: B (Good) — Confidence: B (High)

The question—what lymphoid signals support stromal growth in the thymus—is important on both fundamental and translational grounds. The thymus is essential for T-cell production throughout life, and its age-related involution contributes to immunosenescence. Understanding the signals that sustain stromal integrity could inform regenerative strategies. The proposal correctly notes that work on thymic crosstalk has been published (references 6, 7) and that specific pathways (RANK-RANKL, CD40-CD40L, lymphotoxin-LTβR; see Irla, 2021, *Frontiers in Immunology*) are already implicated in medullary TEC differentiation. The contribution of this project is therefore not the discovery that crosstalk exists, but rather the systematic, unbiased enumeration of candidate interactions across both cortex and medulla during a dynamic growth response. This is a legitimate descriptive contribution.

The attainable knowledge includes: a time-resolved map of stromal gene expression changes during induced thymic growth; a searchable receptor-ligand candidate database with multiple evidence dimensions; and initial in vivo evidence for one or more novel interactions relevant to thymic size regulation. These products would lower the barrier to hypothesis generation in the field, even if detailed mechanistic dissection is deferred. The practical contribution—a prioritization framework that others can reuse—is moderate but real.

The proposal distinguishes its question from embryonic thymic specification and initial stromal lineage divergence, focusing instead on postnatal growth and maintenance. This framing is appropriate. The explicit limitation to bulk regional expression (cortex vs. medulla) rather than cell-type resolution is an honest and acceptable trade-off for an exploratory grant, though it does constrain what can be inferred about which stromal cell type responds to a given signal. Identification of the specific stromal cell type is acknowledged as a subsequent histological step.

---

## Innovation: B (Good) — Confidence: B (High)

The innovation resides in the combination of elements rather than in any single novel method. Each component—the IL7R-deficient transplant model, the microdissection-based stromal expression estimation, the Receptome-based receptor-ligand matching, and the multi-criterion prioritization—has been published or described previously. The novelty is in applying them together to the dynamic growth response, systematically across five kinetic stages and both cortical and medullary compartments.

Several elements distinguish this work from prior studies:

- **Dynamic dimension.** Most prior thymic stromal expression studies are cross-sectional. The proposed time-course captures initiation, log-phase growth, pre-peak, and peak/steady-state stages. This design can identify signals that are transiently induced at specific phases (e.g., growth initiation vs. termination), which single-time-point studies cannot. The inclusion of a "pre-peak" stage (~36 hours before the peak) to capture events preceding growth termination is a thoughtful design feature.

- **Unbiased receptor-ligand approach.** The Receptome resource (Ben-Shlomo et al., 2003, reference 4) provides a curated set of over 900 receptors and 600 ligands. Complementing this with GO-based and literature-based searches, while acknowledging the latter will be noisier, is a defensible strategy. The staged approach—expression filtering, then multi-criterion prioritization, then manual review—is methodologically sound for an exploratory project.

- **Avoidance of myeloablative conditioning.** The use of non-conditioned IL7R-deficient recipients avoids radiation or chemical damage to the stromal compartment. This is important because conditioning is known to damage TECs (Irla, 2021), and the proposal's subject is precisely the stromal response. This design choice enhances the physiological relevance of the findings.

The proposal acknowledges that RANKL, CD40L, lymphotoxin, FGF, and Wnt signals are already implicated in thymic crosstalk (Sun et al., 2013, *Protein & Cell*). These established pathways would likely appear among the candidates; the incremental contribution is the discovery of additional, previously unrecognized interactions and the temporal resolution of when each operates. This is a realistic and valuable increment rather than a transformative breakthrough, which is appropriate for an R21 mechanism.

A limitation is that the approach is restricted to microarray-detectable expression patterns and literature-documented receptor-ligand pairs. Truly novel interactions involving uncharacterized receptors or non-protein mediators would not be captured. This is inherent to the method and not a design flaw, but it defines the boundary of what the project can discover.

---

## Rigor: C (Moderate) — Confidence: B (High)

The research design is fundamentally logical: establish growth kinetics, collect region-matched tissue and lymphoid samples, computationally estimate stromal expression, identify receptor-ligand pairs, prioritize candidates, and test a small number in vivo. Each step has precedents in the laboratory's published work. However, several aspects of the design deserve scrutiny.

### Adequacy of the measurement platform

The proposal uses Affymetrix MOE430_2 microarrays. As of the 2026 review date, RNA-sequencing (bulk or single-cell) is the standard for transcriptomic discovery. Microarrays have limited dynamic range, cannot detect novel transcripts or splice variants, and rely on predefined probe sets. For the expression resource to be maximally useful to the field as a lasting reference, RNA-seq would be preferable. The proposal does not address this choice or justify why arrays remain appropriate. This is a substantive limitation for the resource component (Aim 1), though it does not invalidate the overall approach, since the laboratory has demonstrated that the method can recover known stromal signals on this platform.

### Indirect stromal expression estimation

The computational method estimates stromal expression by subtracting the lymphoid signal from the tissue signal. This approach has inherent limitations. First, it cannot distinguish among the multiple stromal cell types known to reside in the cortex and medulla (cTECs, mTECs, fibroblasts, endothelial cells, mesenchymal cells). The single-cell transcriptomic literature since the proposal's original framing has extensively documented TEC heterogeneity (see Irla, 2021; Muro & Nitta, 2025, *Immunological Reviews*). The proposal acknowledges this limitation and notes that histological methods can subsequently identify the specific cell type. For an exploratory resource, this is acceptable, but it means candidates will require cell-type resolution before mechanistic interpretation.

Second, the subtraction method assumes that the isolated lymphoid population accurately represents the lymphoid component of the tissue sample. Changes in lymphoid composition during the growth response (as endogenous IL7R-deficient lymphoid cells are replaced by wild-type donor-derived cells) could introduce systematic differences between the tissue-associated and purified lymphoid populations. The proposal recognizes this issue (section 5.2, p30) and states that each lymphoid profile is used to account for its matched tissue sample. This is the correct analytical approach, but the potential for residual bias remains.

### Replication and statistical power

The main expression experiment uses three independently prepared microarrays per sample type, each pooling RNA from three mice. This yields n = 3 per condition, which is minimal for microarray ANOVA across five time points. The proposal uses multiple-testing-adjusted q-values for the time-course analysis, which is appropriate, but with only three replicates per time point, the power to detect moderate expression changes is limited. Genes with genuine but modest temporal regulation may be missed. This limitation is partially mitigated by the multi-criterion prioritization approach, which does not rely solely on ANOVA significance.

### Candidate identification and prioritization

The receptor-ligand identification strategy (Aim 2) is reasonable but entails well-recognized risks. The literature-based co-citation approach will favor well-studied interactions over novel ones, potentially biasing the candidate set toward known pathways. The proposal addresses this by combining curated (Receptome) and literature-based approaches and by including a "thymus novelty" measure (pairwise co-citation divided by thymus co-citation) to identify established interactions with unrecognized thymic roles. This is a thoughtful mitigation.

The prioritization scheme (Aim 3) uses multiple evidence dimensions (co-citation, expression dynamics, signal intensity, STRING evidence, reagent availability). This is better than a single-threshold approach and allows users to weight criteria differently. The manual review step, which considers known roles in epithelial or mesenchymal cell biology, is essential because automated metrics alone cannot capture biological plausibility. The proposal is appropriately transparent that this involves qualitative judgement.

### Biological validation (Aim 4)

The validation strategy is appropriately scaled for an exploratory project: examine thymic size and composition in existing mouse mutants for one or more high-priority candidates. The approach using marrow from mutant donors transplanted into IL7R-deficient recipients is elegant because it tests the lymphoid-to-stromal direction specifically without confounding stromal-intrinsic effects. However, Aim 4 is the most dependent on outcomes from Aims 1–3. If the highest-priority candidates lack available mouse models (a risk the proposal acknowledges), the validation scope shrinks. The proposal's commitment to test "a small number" of candidates is realistic. A negative result—failure of a predicted interaction to affect thymic size—would not refute the overall approach but would reduce the project's immediate biological impact.

The proposal appropriately states that distinguishing effects on stromal proliferation, differentiation, and survival is beyond the exploratory scope. This is a legitimate boundary for an R21.

---

## Feasibility: C (Moderate) — Confidence: B (High)

### Dependencies and risk points

The project has several critical dependencies:

1. **Growth kinetics must be tractable.** The proposal assumes that the IL7R-deficient thymus growth response is reproducible with the planned cell dose and timing, but states that "precise kinetics have not yet been determined." The two preliminary kinetic series (one at four-day intervals, one at one-day intervals around critical transitions) are well-designed to establish the trajectory before the main experiment. However, if the response is highly variable between recipients, identifying distinct phases may be difficult. The proposal acknowledges that the growth trajectory may overshoot and that age-related involution continues, complicating the definition of a steady state.

2. **RNA recovery from small samples.** Figure 1 demonstrates recovery of ~10 ng RNA from a 20 µm section of an atrophic thymus, comparable in size to the IL7R-deficient thymus. This is encouraging but represents a static demonstration, not the repeated recovery across multiple samples and time points that the project requires. The Affymetrix platform typically requires 50–100 ng of total RNA for standard protocols; the proposal does not specify whether amplification will be used or what the minimum input requirement is for the planned arrays.

3. **Schedule compression.** The two-year R21 schedule allocates 6–8 months for kinetics and sample collection, 6–8 months for analysis and prioritization, and 8–12 months for refinement and biological testing. The overlapping ranges suggest a total of 20–28 months of activity in a 24-month project. Some compression is normal in grant schedules, but the analysis phase (months 7–12) is described as "intensive data-processing work." The personnel plan acknowledges that the informatics specialist (Fallahi, 10% effort) cannot absorb this workload alone and that the postdoctoral fellow must be trained. If the fellow requires substantial ramp-up time for computational work, the analysis phase could extend, compressing the biological validation phase.

4. **Mouse availability for Aim 4.** The proposal states that fewer than 200 additional animals will be needed for Aim 4 and that an amendment will specify numbers after candidate selection. This is reasonable, but if the selected candidates require conditional alleles that must be bred onto the IL7R-deficient background, the timeline could extend beyond two years. The proposal's reliance on existing models (rather than generating new ones) is appropriate for the R21 mechanism.

### Resource adequacy

The laboratory has established access to flow cytometry, laser microdissection, microarray processing, and computational infrastructure. The PI has published extensively with these methods. The personnel plan (PI supervision, full-time postdoctoral fellow, 10% informatics specialist) is lean but appropriate for an exploratory grant. The small informatics effort is a recognized vulnerability that the proposal addresses through training.

---

## Ethics and Safety: B (Good) — Confidence: B (High)

The proposal provides a clear animal-use justification: investigation of thymic function requires the intact organ, and the question concerns in vivo stromal–lymphoid interactions. The total of 420 mice for primary work, kinetics, and breeding is itemized by purpose, with explicit accounting of recipient and donor numbers. The additional animals for Aim 4 (fewer than 200) are acknowledged as dependent on candidate selection, with an amendment to follow.

The animal welfare provisions—veterinary oversight, anesthesia for procedures causing more than momentary distress, monitoring through recovery and at least daily for the subsequent week, and humane endpoints—meet standard expectations for rodent research. The proposal does not describe environmental enrichment or specific housing conditions, but this level of detail is typically in institutional protocols rather than the research narrative.

The non-myeloablative transplantation model is a genuine refinement over conditioning-based approaches, reducing suffering by avoiding radiation or chemotherapy. This is both a scientific and an ethical strength.

Data sharing is addressed: findings will be communicated through publications and presentations, and data will be available upon acceptance, subject to institutional conditions including a possible 60-day delay for IP review. The proposal does not specify a public repository (e.g., GEO) for the microarray data, which would be the current standard. This is a minor gap rather than a decisive concern.

The use of mice is justified by the scientific question. No human subjects, environmental hazards, or dual-use concerns are identified.

---

## Management: C (Moderate) — Confidence: B (High)

### Personnel and expertise

The PI (Petrie) has a strong publication record in thymus biology, including the core methods (Griffith et al., 2009, *Immunity*; Porritt et al., 2003, *Journal of Experimental Medicine*; Prockop & Petrie, 2004, *Journal of Immunology*). The laboratory has demonstrated capability in both the experimental (transplantation, microdissection, flow cytometry) and computational (expression analysis, database construction) components.

The postdoctoral fellow will perform most of the experimental and computational work. The proposal states that Petrie will train the fellow and review raw data, which is appropriate. The informatics specialist (Fallahi) at 10% effort provides continuity with the laboratory's prior computational work (Fallahi is a co-author on Griffith et al., 2009). The 10% effort is small for the described "intensive data-processing work," but the proposal explicitly frames Fallahi's role as training and specialist assistance rather than primary execution.

No CVs, itemized budget, or institutional letters are included in the supplied text. Per the review instructions, this absence alone is not evidence of incapacity. The text describes shared-facility access and computational infrastructure, and the laboratory's publication record provides indirect evidence of access.

### Schedule coordination

The three-phase schedule (months 1–8: kinetics and samples; months 7–12: analysis; months 12–24: refinement and validation) is logical but ambitious. The overlap between phases is not explicitly discussed. A delay in establishing the growth kinetics could cascade through the entire project. The proposal does not describe contingency plans for such delays.

### Deliverables and dissemination

The stated deliverables—expression time course, annotated interaction database, initial mouse-model findings, and publications—are appropriate to the scope. The database, if made publicly searchable, would be a useful community resource. The proposal does not specify the database format, hosting, or long-term maintenance arrangements. For a two-year exploratory grant, this is understandable, but it affects the durability of the resource contribution.

---

## Summary of Ratings

| Criterion | Rating | Confidence |
|-----------|--------|------------|
| Scientific Value | B (Good) | B (High) |
| Innovation | B (Good) | B (High) |
| Rigor | C (Moderate) | B (High) |
| Feasibility | C (Moderate) | B (High) |
| Ethics and Safety | B (Good) | B (High) |
| Management | C (Moderate) | B (High) |

---

## Recommendation: Conditional Support

The proposal addresses a well-defined and important question in thymus biology using a logical sequence of established methods. The expected products—a dynamic stromal expression resource, a prioritized candidate interaction database, and initial biological validation—would constitute a worthwhile contribution to a field where the lymphoid-to-stromal direction of thymic crosstalk remains incompletely characterized. The strengths in biological rationale, model system selection, and multi-criterion prioritization design outweigh the limitations in measurement platform resolution and schedule ambition.

The recommendation is **conditional support** rather than unconditional support because two premises should be confirmed or addressed before the main expression experiment proceeds:

1. **Measurement platform adequacy.** The continued use of Affymetrix MOE430_2 arrays should be explicitly justified against the availability of RNA-seq approaches as of 2026, or the platform should be updated. If RNA-seq is adopted (even in a reduced-complexity form such as 3′-tag sequencing), the resource value would be substantially enhanced and the concern about probe-set coverage would be resolved. If arrays are retained for cost or continuity reasons, the limitations in transcript coverage and dynamic range should be acknowledged in the resource documentation.

2. **Schedule feasibility.** The two-year timeline should be reviewed against a more detailed work plan, particularly for the transition from the analysis phase to Aim 4 biological testing. A realistic assessment of whether meaningful biological validation can be completed within the R21 period—or should be explicitly framed as a future direction—would strengthen the plan's credibility without altering its scientific merit.

These conditions are addressable within the existing project framework and do not require redesign of the scientific aims. The proposal identifies a worthwhile direction, and the current plan provides a sufficient route to the stated goals once these premises are clarified.