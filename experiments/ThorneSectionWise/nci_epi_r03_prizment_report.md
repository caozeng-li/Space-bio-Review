# Comprehensive Review

**Proposal Summary**
This proposal aims to investigate whether circulating levels of six MHC class I-like proteins, measured prospectively in initially cancer-free individuals from the ARIC cohort, predict subsequent colorectal cancer (CRC) risk. The primary exposure is a factor score derived from these correlated proteins, with analyses addressing baseline associations, within-person change using marginal structural models, and determinants of protein levels. A secondary exploratory aim employs Mendelian randomization (MR) to probe potential causality. The project leverages existing cohort data, already-collected or in-progress SOMAscan proteomic assays, and genetic data for approximately 12,000 participants, with a planned 24-month timeline.

---

## Overall Assessment

This is a well-conceived epidemiological investigation that addresses a clear and important knowledge gap: whether systemic immune-regulating proteins associated with tumor immune evasion are detectable as risk markers years before clinical cancer diagnosis, rather than merely as prognostic markers after disease onset. The central strength of the proposal lies in its prospective design within the ARIC cohort, which provides temporality that previous clinical studies could not. The research question is timely, the biological rationale connecting MHC class I-like proteins to immune surveillance is plausible and well-articulated, and the team possesses relevant expertise in both the biological system and the cohort infrastructure.

The proposal's scientific value is solid: establishing a prospective association between a coordinated panel of these proteins and CRC incidence would represent a genuine conceptual and descriptive advance, with potential implications for risk stratification and understanding the timeline of immune evasion in colorectal carcinogenesis. The inclusion of repeated measures over two decades and a biracial population adds descriptive richness.

However, the project's ability to deliver fully on its stated inferences is constrained by several unresolved methodological issues that affect its rigor and, consequently, the strength of its conclusions. The most consequential of these concerns the primary exposure itself: the factor score that constitutes the core of Aims 1 and 2. The proposal assumes a single dominant factor will emerge from the six proteins, but provides no plan for validating this structure or establishing what the study will conclude if it fails to materialize. Since the individual protein analyses are explicitly designated as exploratory, a failure of the factor structure would leave the primary inference unsupported, or at least substantially weakened. This is not a peripheral analytical detail but a central design contingency that remains unaddressed.

Additional rigor concerns include insufficient specification for the marginal structural models that underpin the change analyses (Aim 1b), particularly regarding the treatment model and weight diagnostics, and inadequate attention to the pleiotropy and linkage disequilibrium assumptions that are critical for the MR analysis. These limitations do not negate the value of the prospective-association contribution but do constrain the confidence with which the more detailed mechanistic and causal inferences can be drawn.

Feasibility is generally high given the secondary-analysis design and existing data resources, but it depends critically on the completion of Visit 2 SOMAscan assays currently described as “underway.” The schedule provides limited buffer for this dependency. The ethics and data-governance framework is appropriately protective at a structural level—de-identified data with identity keys retained by the coordinating center—but the documentation is thin regarding consent scope for integrated genomic-proteomic-cancer analyses, return of incidental findings, and management of group-level considerations for race-stratified genetic research.

On balance, the project pursues a worthwhile scientific question with a feasible core approach, but the current plan contains substantive gaps—particularly around the factor-score validation, the operationalization of the marginal structural model, and the MR pleiotropy strategy—that prevent confidence that the stated primary and secondary inferences will be adequately supported. These are addressable problems that do not require redesigning the study, but they do require explicit methodological commitments and contingency plans before the project can be considered ready for execution as described.

---

## Evaluation by Criterion

### Scientific Value: B (Confidence: B)

The importance of the question and the attainable knowledge are the proposal's strongest features. The conceptual contribution—shifting from prognostic to prediagnostic assessment of MHC class I-like proteins—is clearly articulated and scientifically well-motivated. Demonstrating that systemic immune-regulatory signals precede clinical CRC by years would represent a meaningful advance in understanding the timeline of immune surveillance failure, with potential downstream implications for risk stratification. The descriptive resource that the project would generate—repeated measures of six related proteins over two decades in a large, well-characterized biracial cohort—has independent value for the field. The methodological contribution of using a factor score and marginal structural models is incremental but principled.

The scientific value is tempered by the exploratory nature of the MR aim and the reliance on a composite exposure whose biological interpretability depends on empirical validation. If the factor score proves inadequate, the primary contribution would rest on individual protein analyses designated as exploratory, reducing the study's impact relative to its stated goals. The value is real but contingent on the factor structure behaving as anticipated.

---

### Innovation: B (Confidence: B)

The project offers a well-justified increment in novelty relative to prior work. The central innovation is the shift from prognostic to prediagnostic assessment using a prospective cohort design, which the proposal correctly identifies as distinguishing its contribution from existing literature. The combination of a six-protein panel, repeated measures, long-term CRC follow-up, paired genetic data, and a biracial population is novel; no single prior study integrates all these elements for this specific question. The application of marginal structural models to assess protein-change effects while accounting for time-dependent confounding represents a methodological advance for this biomarker field.

The innovation is, however, primarily one of configuration and application rather than fundamental discovery of new methods or assays. The analytical techniques (Cox regression, linear regression, mixed models, MR) are well-established, and the SOMAscan platform is a commercial technology. The scientific gamble—that a factor score will detect signals that single proteins miss—is reasonable but not radical. This is a solid, incremental contribution rather than a disruptive one, which is entirely appropriate for the project's scope and mechanism.

---

### Rigor: C (Confidence: B)

The core prospective design provides a methodologically sound foundation for establishing temporality between protein levels and CRC incidence, and the sensitivity analyses excluding early-diagnosis cases appropriately address reverse causation. The documented SOMAscan quality-control metrics (median CV 5%, ICC 0.96) support the technical adequacy of the measurements. The power calculations are specific, plausible, and adequate for the primary baseline and short-term change analyses.

Three material limitations constrain the rigor judgement:

1. **Factor-score dependency without validation plan.** The primary exposure for Aims 1 and 2 is a factor score whose adequacy depends on the empirical correlation structure of the six proteins. The proposal assumes a single dominant factor (eigenvalue >1, correlations 0.4–0.55) but does not specify how it will validate this structure, what alternatives it will consider if the assumption fails (e.g., multiple factors, retention of individual proteins as primary analyses), or what inferences will be drawn in that scenario. Since individual protein analyses are designated as exploratory, the primary inference lacks a specified contingency plan for a plausible and consequential analytical outcome.

2. **Under-specification of marginal structural models.** The change analysis (Aim 1b) employs inverse-probability-of-treatment weighting to address time-dependent confounding, but the treatment model, the variables used to estimate weights, and the planned diagnostics for weight stability (truncation, extreme values) are not described. Without these operational details, the specific inference supported by the marginal structural model—and whether it successfully addresses the stated confounding—cannot be assessed.

3. **Inadequate treatment of MR assumptions.** The MR analysis specifies instrument strength (F>10) but provides no plan for evaluating pleiotropy or exchangeability, which are substantial concerns given the HLA-region location of many relevant genes and the potential for linkage disequilibrium and immune-system-wide effects. The absence of methods such as MR-Egger, weighted median, or colocalization leaves the causal estimate vulnerable to biases whose magnitude is not addressed.

Additional concerns—handling of non-detectable values in the factor-score context, accumulation of secondary analyses without a stated multiplicity strategy, and limited power for long-term change analyses—further affect the confidence with which the full pattern of results can be interpreted, though they do not directly threaten the primary association inference.

---

### Feasibility: B (Confidence: B)

The project's secondary-analysis design, leveraging pre-existing ARIC data, specimens, and cohort infrastructure, inherently reduces feasibility risk relative to primary data collection. Access to the necessary integrated dataset—SOMAscan proteomics, GWAS and sequencing data, cancer outcomes, and covariates—is convincingly established for approximately 12,000 participants. The investigator team possesses directly relevant expertise in molecular cancer epidemiology, computational genetics, and the ARIC cohort. The analytical methods are standard and well-supported by the stated software (SAS, Stata).

The primary feasibility concern is the stated dependency on completion of Visit 2 SOMAscan assays, described as “underway.” These measurements are essential for the baseline risk analysis (Aim 1a), the baseline component of the change analysis (Aim 1b), and the determinants analysis (Aim 2). The schedule allocates analysis start to month 4, providing minimal buffer for assay completion, quality control, or data integration delays. A significant delay in this step would cascade through the entire timeline. The proposal does not describe contingency planning for this dependency.

A secondary feasibility consideration concerns the MR analysis, which requires identifying strong genetic instruments from within the project's own data. This identification step is inherently uncertain scientific work and is allocated to the final project phase (months 19–24). The compact schedule creates a risk that this exploratory aim is conducted under time pressure, though this does not affect delivery of the primary Aims 1 and 2.

---

### Ethics and Safety: C (Confidence: C)

The study design—secondary analysis of de-identified cohort data and stored biospecimens with no new participant contact or specimen collection—carries inherently low direct risk to participants and the environment. The described data-access arrangement, in which investigators receive only coded research IDs while the identity-linkage key remains with the ARIC Coordinating Center, is a standard and appropriate privacy protection.

The ethics documentation is thin relative to the project's integration of genomic, proteomic, and cancer-outcome data. Several considerations relevant to ethical appropriateness are unspecified in the supplied text:

1. **Consent scope.** The proposal does not confirm that the original ARIC consent permits secondary genetic and proteomic analyses for cancer-risk research, or whether any limitations apply to the specific assays, the genotype-protein association work, or the use of whole-genome sequencing data. The absence of this confirmation is a documentation gap that limits confidence in the ethical framework assessment.

2. **Incidental findings.** The project explicitly examines functional MICA/MICB variants and their relationship to cancer risk, making the question of clinically actionable incidental findings non-hypothetical. The proposal does not address whether such findings would be returned to participants, to ARIC governance, or to clinical care.

3. **Group-level considerations.** The deliberate inclusion of Black and White participants and planned race-stratified analyses raise recognized ethical considerations in genetic epidemiology regarding potential misinterpretation, stigmatization, or reification of racial categories. The proposal does not describe community engagement, consultation on the presentation of race-stratified results, or measures to prevent misinterpretation.

These unspecified elements reduce confidence in the completeness of the ethical framework without, on the available evidence, demonstrating a violation or unmanageable risk. The absence of a return-of-results policy and group-harm mitigation planning are the most noteworthy omissions.

---

### Management: B (Confidence: B)

The 24-month timeline is structured logically with sequential phasing: IRB and data extraction (months 1–3), Aim 1 analyses (months 4–12), Aim 2 (months 13–18), and the secondary aim with manuscript preparation (months 19–24). For a small, self-contained R03-scale project, this is a proportionate and realistic schedule. The team's combined expertise in molecular cancer epidemiology, computational genetics, and biostatistics is well-aligned with the analytical demands. No external collaborators or expertise gaps are evident.

The principal management concern is the unspecified coordination mechanism for data integration. The proposal does not clarify whether the required merged dataset—combining SOMAscan, GWAS/sequencing, cancer outcomes, and longitudinal covariates—has already been assembled and quality-checked by the ARIC Coordinating Center, or whether this is a task for the investigator team during months 1–3. Harmonizing these complex, multi-source, repeated-measures data is a substantial effort and a common bottleneck in secondary analyses. If this work is the team's responsibility, the three-month allocation may be optimistic, potentially compressing the analysis schedule.

The MR instrument-identification step allocated to months 19–24 is unpredictable in duration, introducing a schedule risk for the secondary aim, though not for the primary deliverables.

---

## Summary of Ratings

| Criterion       | Rating | Confidence |
|------------------|--------|------------|
| Scientific Value | B      | B          |
| Innovation       | B      | B          |
| Rigor            | C      | B          |
| Feasibility      | B      | B          |
| Ethics and Safety| C      | C          |
| Management       | B      | B          |

---

## Recommendation: Current Proposal Insufficient for Support (Revise)

The project pursues a worthwhile scientific direction with a feasible core approach and a capable investigative team. The prospective assessment of MHC class I-like proteins as CRC risk markers would constitute a genuine conceptual and descriptive contribution, and the ARIC cohort provides a strong platform for this investigation.

The recommendation of "revise" rather than "conditional support" or "support" reflects the presence of substantive problems in the current plan that affect its main goals, rather than peripheral weaknesses. These are addressable issues that do not require abandoning the research direction or redesigning the study from scratch, but they do require explicit methodological commitments and contingency plans before the project can be considered ready for execution as described.

The most consequential issues requiring revision are:

1. **Factor-score validation and contingency planning.** The proposal must specify (a) how the factor structure will be validated in the ARIC data, (b) what criteria will determine whether a single factor is adequate, and (c) what the primary analysis and primary inference will be if the six proteins do not form a single dominant factor. Without this, the core of Aims 1 and 2 rests on an untested assumption whose failure would leave the primary findings unsupported.

2. **Operational specification of the marginal structural model.** The change analysis requires explicit description of the treatment model, the variables contributing to the inverse-probability weights, and the planned diagnostics for weight stability and truncation. These details determine whether the analysis addresses the time-dependent confounding that motivates its use.

3. **Mendelian randomization pleiotropy strategy.** The MR analysis must incorporate methods for evaluating and addressing pleiotropy (e.g., MR-Egger, weighted median, colocalization) and must discuss the exchangeability assumption in the context of the HLA region. The current reliance on instrument strength alone is insufficient for the causal inference claimed.

4. **Ethics documentation.** The proposal should confirm consent scope for the integrated genomic-proteomic-cancer analyses, state the approach to incidental findings (even if that approach is to defer to ARIC-wide policy), and describe plans for responsible presentation and interpretation of race-stratified results.

Addressing these points would substantially strengthen the proposal without altering its fundamental design or scientific goals. The underlying question is worth pursuing, and the ARIC resource is well-suited to answer it; the revisions needed are those that would allow the planned analyses to support the stated conclusions with appropriate rigor and ethical completeness.