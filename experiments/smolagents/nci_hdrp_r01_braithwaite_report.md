# Scientific Project Review

**Proposal:** Personalized Screening for Lung Cancer: Importance of Co-existing Chronic Conditions to Clinical Practice and Policy
**Principal Investigator:** Dejana K. Braithwaite, with Michael Gould and Gerard Silvestri
**Source ID:** src-b00258ddaa45
**Review Date:** 2026-10-28

---

## Overall Assessment

This proposal addresses a clinically important and well-motivated question: how multimorbidity—comorbidity, functional limitation, and impaired pulmonary function—modifies the balance of benefits and harms from low-dose computed tomography (LDCT) lung cancer screening in ordinary clinical practice. The central insight is that screening-eligible populations in community settings are older and sicker than participants in the National Lung Screening Trial (NLST), and that net benefit may not rise monotonically with lung cancer risk. The proposed work combines a multi-site observational cohort (approximately 34,000 individuals across three health systems) with an established CISNET microsimulation model (UM-LCS) to estimate screening outcomes across multimorbidity levels and to identify thresholds where harm may outweigh benefit.

The project engages a genuinely consequential problem for clinical and policy decision-making. Current guidelines rely on qualitative criteria ("reasonably good health") that lack transparent quantitative anchors, and evidence from trial populations is insufficiently generalizable to the multimorbid adults now being screened. The question is timely, and the conceptual framework—that competing mortality and treatment-related harms may dominate at high comorbidity levels—is persuasive and consistent with prior evidence on surgical outcomes in multimorbid lung cancer patients. The multidisciplinary team possesses relevant expertise in epidemiology, thoracic oncology, health services research, biostatistics, real-world data science, and simulation modeling. The proposed data infrastructure builds on established Watch the Spot (WTS) vocabulary and prior NLP algorithm development with demonstrated high sensitivity and specificity for nodule identification.

The principal strengths of this proposal are its real-world clinical relevance, the diversity of the multi-site cohort, the integration of observational data with simulation to extend inferences beyond observed follow-up, and the attention to functional limitations and pulmonary physiology beyond simple comorbidity counts. The major limitations include reliance on a cohort of people already screened—which cannot directly estimate the net benefit of screening versus no screening without strong modeling assumptions—the dependence of Aim 3 on Aims 1–2 outputs that are themselves subject to selection and measurement challenges, and the incomplete specification of how competing mortality data will be captured and integrated into the microsimulation in a way that convincingly identifies subgroups for whom screening is ineffective.

On balance, this is a well-conceived project addressing an important gap. It warrants support, though its success depends critically on execution details and on interpretive caution about the observational design's ability to support causal claims about screening effectiveness.

---

## Scientific Value: A (Good)

### Importance of the Question

The question—whether multimorbidity alters the net benefit of LDCT lung cancer screening—is of high clinical and policy importance. The proposal documents convincingly that NLST participants were younger (42.8% age 55–59, none ≥75), healthier (diabetes 9.7% vs. 21.7% in USPSTF-eligible HRS respondents; heart disease 12.7% vs. 26.4%), and had longer estimated life expectancy (21.2 years vs. 14 years for a Medicare screening-eligible subgroup). Since 2013, the USPSTF has recommended LDCT screening (Grade B), and uptake has expanded. Yet guidance on when to stop or not offer screening because of comorbidity remains qualitative. The proposal correctly identifies that COPD illustrates the tension: it increases lung cancer risk two- to threefold but also increases procedural complications, mortality after resection, and competing respiratory death.

### Attainable Knowledge

The proposal can contribute several forms of knowledge:

1. **Descriptive contribution (Aim 1):** Prevalence estimates of multimorbidity, functional limitation, and impaired pulmonary function among real-world screening recipients across three diverse health systems, with variation by race/ethnicity, SES, and age. This is a descriptive resource of genuine value to the field.

2. **Empirical associations (Aim 2):** Estimates of false-positive findings, procedure-related complications, and cancer detection rates stratified by comorbidity, functional limitation, and pulmonary function categories, using discrete-time survival models across repeated screening examinations.

3. **Modeling contribution (Aim 3):** Microsimulation projections of long-term screening outcomes (mortality, QALYs, number needed to screen, overdiagnosis) across multimorbidity-defined subgroups under varying screening strategies. The project can identify multimorbidity thresholds where modeled net benefit approaches that of an average-health comparator.

The combination of empirical and modeled evidence is a genuine methodological strength: the observational cohort addresses real-world patterns that trials cannot capture, while the simulation extends beyond the observed follow-up to lifetime horizons and policy-relevant scenarios.

### Practical Capability

The expected products—subgroup-specific estimates of screening harms and benefits, refined simulation models incorporating comorbidity-dependent parameters—could inform clinical shared decision-making and future guideline updates. This is practical evidence that addresses a recognized evidence gap, as articulated by the ATS research statement on incorporating coexisting chronic illness into screening decisions (Rivera et al., 2018).

### Limitations on Scientific Value

The central limitation is that the cohort consists of people already undergoing screening. It cannot directly estimate the counterfactual: outcomes without screening. Thus, empirical aims cannot themselves estimate the *net* benefit of screening—they estimate outcomes *conditional on being screened*. The net benefit inference in Aim 3 depends entirely on the microsimulation, which generates a no-screening counterfactual from natural-history parameters calibrated to trial and registry data. This is a standard and defensible modeling approach, but it means the most policy-relevant output—"screening is not effective for some groups"—rests on simulated rather than observed evidence. This does not diminish the scientific value of the empirical aims, but it does mean the proposal's largest claim about effectiveness requires cautious interpretation.

---

## Innovation: B (Good)

### What the Project Adds Beyond Prior Work

The proposal identifies several innovations:

1. **Combination of real-world outcomes with simulation:** Prior work has separately described multimorbidity in screening-eligible populations (Howard et al., 2015, *Cancer*), used microsimulation to evaluate screening strategies (Caverly et al., 2018, *Ann Intern Med*; CISNET consortium analyses), and monitored real-world screening use (Gould et al., 2017, *Ann Am Thorac Soc*). This proposal integrates them by using cohort-derived comorbidity, functional-limitation, and pulmonary-function estimates to refine simulation inputs—specifically, screening outcomes, biopsy and complication rates, Lung-RADS adherence, and competing mortality by multimorbidity level.

2. **Assessment of functional and pulmonary status beyond comorbidity counts:** Most prior analyses have used Charlson or Elixhauser indices. The proposal adds the Segal Claims-based Frailty Indicator (FRI) on a 0–13 scale and FEV1/FVC-based pulmonary function characterization. The FRI itself was developed by Segal et al. (2017, *Med Care*) to approximate the Fried frailty phenotype from claims data (AUC 0.75 for concurrent frailty prediction). This constitutes a meaningful extension beyond the comorbidity-count approach.

3. **Diverse multi-site population:** KPSC (integrated, ethnically diverse), MUSC (safety-net hospital with substantial Black population), and VA Portland (veteran population with dedicated screening coordination). This contrasts with single-payer or single-institution studies and offers variation in care delivery, geography, and population.

### What Is Incremental Rather Than Novel

The core methodological approach—multi-site EHR cohort linked to tumor registries, analyzed with discrete-time survival models and propensity score adjustment—is well-established in health services research. The UM-LCS model is an existing, validated CISNET platform. The proposal represents a well-motivated application and integration rather than a new method or model. The latent-class comorbidity profiles previously identified (Gould et al., 2017) are a basis for the work but are not advanced here as a new classification method. The contribution lies in the systematic application to a population-level screening question, not in the development of novel analytic tools.

### Distinguishing Originality from Value

The value of the question does not automatically make the approach highly original. The proposal's design is conceptually straightforward: measure multimorbidity in screening recipients, estimate outcome rates by multimorbidity level, and feed those into a model. The innovation is in the *integration* and the *comprehensiveness* of the exposure characterization (comorbidity + function + pulmonary physiology). This is a meaningful rather than transformative increment.

---

## Rigor: B (Good)

### Design Appropriateness for Inferences

**Aim 1 (descriptive prevalence):** The design is appropriate. Weighted prevalence with inverse selection probabilities, multivariable logistic regression for adjusted associations, and stratification by race/ethnicity, SES, and age are standard and defensible. Confidence: moderate—the success of inverse-weighting depends on availability of target-population risk-factor distributions, which are described in principle but not specified.

**Aim 2 (outcome associations):** The discrete-time survival modeling framework, with time indexed by examination number and time-varying covariates, is well-suited to repeated screening data. The use of generalized propensity scores for non-binary exposures is appropriate. The separation of models by outcome and the backward selection using AIC are reasonable, though the proposal does not address the inflation of Type I error across multiple outcomes.

**Aim 3 (microsimulation):** The UM-LCS model is a CISNET model that has undergone comparative validation (Meza et al., 2014, *Cancer*). The proposal's plan to recalibrate model inputs with cohort-derived estimates is a strength. However, several aspects affect rigor:

- The proposal states that competing mortality will be informed by "CISNET; NLST; PLCO; real-world LCS cohort" (Table 3/Table 1 in the protocol paper). How cohort-derived competing mortality will be integrated—whether via life tables stratified by comorbidity or through parametric models—is not detailed. This is important because the central hypothesis is that competing mortality dominates at high comorbidity levels.
- The "threshold approach" (finding multimorbidity levels where benefits/harms resemble average-health subgroups) is conceptually appealing but its statistical properties (precision of threshold estimates) are not addressed.
- Model validation against observed cohort outcomes is mentioned ("Results will be compared with the original trial-based model") but a structured calibration/validation plan is not described.

### Measurement Quality

- **Comorbidity:** The combined Charlson/Elixhauser score (Gagne et al., 2011, *J Clin Epidemiol*) is a validated predictor of mortality in elderly populations (c-statistic 0.860 for 30-day mortality vs. 0.839 for Charlson alone). Time-varying score calculation from the 12 months preceding each LDCT is appropriate.
- **Functional limitations:** The FRI (Segal et al., 2017) was developed with an AUC of 0.75 for concurrent frailty prediction. Its 0–13 scale and the proposed grouping (0, 1, ≥2) are reasonable, though its performance specifically in lung cancer screening populations is not established. The proposal acknowledges that FRI data are primarily available at KPSC and VA (~80% of cohort), with MUSC contributing less.
- **Pulmonary function:** FEV1/FVC <70% is the standard GOLD criterion for airflow limitation. Spirometry data availability is acknowledged as partial (KPSC and OneFlorida, per the protocol paper—note the proposal document describes only three sites while the published protocol describes four, reflecting an evolution of the study design).
- **NLP for nodule identification:** Sensitivity 96%, specificity 92% in prior work; expanded version with >95% for both, though noted as "unpublished." These are strong performance characteristics for EHR-based nodule ascertainment.

### Confounding and Selection

The proposal recognizes that people with multimorbidity who undergo screening may differ systematically from those who do not. The generalized propensity score approach addresses measured confounding. However, the cohort is restricted to people who were screened—this is a form of collider-stratification that may induce selection bias in the estimated association between multimorbidity and outcomes, because screening participation is affected by both comorbidity and unmeasured health-conscious behaviors. The proposal acknowledges this implicitly but does not fully address the inferential limitation it creates for Aim 2.

### Power and Precision

The power calculations (34,039 screened people, 902 incident cancers, detectable risk difference of 5% for procedure-related complications, 1.3–1.9% for false-positive risk differences at 80% power and α=0.05) appear adequate for the main comparisons. The detectable differences are clinically meaningful. The assumption of a 20–30% complication rate in the comparison population and 70% with consequential comorbidity is reasonable. However, power for subgroup analyses (e.g., within race/ethnicity strata) is not addressed.

### Missing Data

The plan for logistic models predicting missingness, sensitivity analyses with inverse-probability weighting, and multiple imputation (10 datasets) is appropriate. The assumption of missing-at-random is stated but cannot be verified.

---

## Feasibility: B (Good)

### Data Access and Cohort Assembly

The cohort draws on existing data from three (per the proposal) or four (per the published protocol) health systems spanning 2016–2022. The retrospective component (2016–2019) is already available. The prospective component (2020–2022) would have completed data collection by the time of review (2026). The published protocol paper (Braithwaite et al., 2022, *BMJ Open*) confirms that regulatory approvals were obtained, a data coordinating center at KPSC was established, and standardized data elements from the WTS infrastructure were adopted. These are strong feasibility indicators.

### Technical Feasibility

- The NLP algorithms for nodule identification are already developed and validated.
- The UM-LCS model is established within CISNET.
- The analytic methods (discrete-time survival, generalized propensity scores, multiple imputation) are standard and implementable.
- The data harmonization across sites using a common data dictionary is a recognized challenge, but the WTS infrastructure and prior cross-institutional work by the team provide credibility.

### Schedule

The five-year timeline (Table 4, assuming January 2020 start) shows IRB and regulatory approval in the first half of Year 1, cohort collection across Years 1–3, Aims 1–2 analysis across Years 1–4, simulation refinement and Aim 3 from the second half of Year 1 through Year 5, and manuscript preparation from Year 3 onward. This schedule is aggressive but plausible given the team's experience and the retrospective availability of much of the data. The overlap between cohort collection and analysis (Years 1–3) is realistic for a multi-site observational study.

### Critical Dependencies

1. **Spirometry and functional limitation data availability at MUSC:** The proposal states FRI data are primarily from KPSC and VA. If MUSC (or OneFlorida as the fourth site in the protocol) cannot contribute these measures, the functional-limitation analyses will be restricted to subsets. This is acknowledged and does not threaten the main aims but limits generalizability.

2. **Medicare linkage for out-of-system ascertainment:** The proposal relies on Medicare linkage to capture diagnoses and treatments outside the participating systems. Successful linkage is not guaranteed but is a standard procedure with established pathways.

3. **Competing mortality data for simulation:** The quality of competing-mortality estimation from the cohort depends on complete mortality ascertainment. State mortality records and EHR vital status are cited; their completeness for the relevant time window is plausible but not demonstrated.

4. **Model recalibration convergence:** Whether the UM-LCS can be recalibrated to reproduce observed cohort outcomes across multiple multimorbidity strata simultaneously is an empirical question. If calibration proves difficult, Aim 3 outputs may be less precise than anticipated.

---

## Ethics and Safety: B (Good)

### Data Privacy and Confidentiality

The proposal describes a HIPAA-compliant data structure with unique study IDs separated from identifiers, encrypted REDCap-based transfers from MUSC and VA Portland to the KPSC coordinating center, and authenticated/authorized access. Site-specific secure databases and institutional IRB approvals are required. These are appropriate and standard practices.

The published protocol confirms that IRB approval was obtained via the smart IRB Master Common Reciprocal Institutional Review Board Authorization Agreement (Reference 12430) for all sites. This provides external evidence that ethical review was completed.

### Patient Risk

This is a retrospective/prospective observational study using EHR, claims, and registry data. There is no direct patient intervention and no modification of clinical care. The physical risk to participants is nil. The primary ethical consideration is data privacy, addressed as above.

### Unresolved Ethical Questions

- **Waiver of consent:** The protocol paper states "Patient consent for publication: Not applicable," suggesting a waiver of informed consent was obtained as part of IRB approval. The proposal application does not explicitly discuss the justification for waiver.
- **Data sharing:** The proposal does not describe plans for data sharing or public access to the harmonized cohort. The published protocol also does not include a data-sharing statement beyond dissemination via publications and presentations.
- **Patient and public involvement:** The published protocol explicitly states: "There is no patient and public involvement in the design and conduct of the study." The proposal, in contrast, mentions patient advocates will "inform interpretation and dissemination for patient-centered decision support." This discrepancy may reflect an evolution; the role of patient advocates is, in any case, limited to interpretation rather than design.

### Risk-Benefit Assessment

The knowledge to be gained—quantitative evidence to guide screening decisions in multimorbid populations—has substantial potential public health benefit. The data-privacy risks are proportionate and managed through standard safeguards. The risk-benefit balance is favorable.

---

## Management: C (Moderate)

### Team Expertise

The multidisciplinary team covers epidemiology (Braithwaite), thoracic oncology and surgery (Gould, Silvestri), health services research, disparities, biostatistics (Shen, Fan), real-world data science, and simulation modeling (Meza, Jeon). The PI's and key collaborators' track records include relevant prior publications on comorbidity profiles in lung cancer (Gould et al., 2017), lung cancer screening monitoring (Gould et al., 2017), and microsimulation analyses (Meza et al., 2014; Caverly et al., 2018). Silvestri is a co-author of the ATS research statement on comorbidity and screening. The expertise is well-matched to the proposed work.

### Coordination and Governance

The data coordinating center at KPSC, led by Gould and Shen with statistical input from Fan, provides centralized oversight for Aims 1 and 2. Monthly calls and annual meetings are specified. The division of labor between the three (or four) sites, the coordinating center, and the modeling group is described at a high level though roles for each team member are not individually specified.

### Resource Adequacy

The proposal does not include a detailed budget, which is expected in the application context (R01). The resources described—EHR infrastructure at three health systems, the WTS data vocabulary, existing NLP tools, and the UM-LCS model—are substantial and largely in place. The REDCap infrastructure for secure data transfer is established. The proposal does not identify specific resource gaps.

### Schedule and Deliverables

The five-year schedule (Table 4) maps activities to years. Anticipated products include: a harmonized clinical cohort, subgroup-specific estimates of screening harms and benefits, refined simulation models, and evidence to support screening discussions and guideline development. The timeline is specific about when cohort collection, analysis, and simulation refinement occur. Manuscript preparation from Year 3 onward is appropriate. No milestones for specific deliverables (e.g., "Aim 1 manuscript submitted by end of Year 2") are provided, which would strengthen accountability.

### Limitations Affecting Management Assessment

The proposal-level documentation does not include individual CVs, detailed role assignments for all investigators, or a line-item budget. Per review instructions, these omissions alone are not evidence of incapacity. However, the unspecified nature of some arrangements—particularly the distribution of analytic responsibilities across the multiple institutions and the specific expertise brought by each collaborator to Aim 3—limits confidence in the management assessment.

---

## Summary of Ratings

| Criterion | Rating | Confidence |
|-----------|--------|------------|
| Scientific Value | A (Good) | B (High) |
| Innovation | B (Good) | B (High) |
| Rigor | B (Good) | B (High) |
| Feasibility | B (Good) | B (High) |
| Ethics and Safety | B (Good) | B (High) |
| Management | C (Moderate) | C (Moderate) |

---

## Recommendation: Support

This proposal addresses an important and timely question with a well-reasoned conceptual framework, a credible multi-site data platform, an experienced multidisciplinary team, and an appropriate integration of observational and simulation methods. The central limitation—that the observational cohort cannot itself estimate the net benefit of screening versus no screening—is inherent to the design and is mitigated by the simulation component, though this shifts the most policy-consequential inference to modeled rather than observed evidence. The descriptive and association-based contributions (Aims 1–2) are independently valuable and achievable.

The moderate management rating reflects the limited specification of team roles, governance, and milestones rather than any identified deficiency. The overall judgment is that the project can deliver worthwhile contributions—descriptive characterization of multimorbidity in real-world screening populations, empirical estimates of screening outcomes across comorbidity/function/pulmonary strata, and microsimulation projections that identify subgroups where benefits and harms may differ substantially—and that the proposed route provides proportionate support for those contributions.

The recommendation to support is made with the recognition that the project's most ambitious claim—identifying groups for whom screening is not effective—will require careful calibration, validation, and interpretive restraint, and that the observational design constrains causal inference about screening effectiveness. These are features of the research that affect confidence in the anticipated results rather than reasons to withhold support for a well-designed investigation of a consequential scientific question.