# Scientific Project Review

**Proposal:** Estimation and Association Analysis of Biomarkers for Tumor Immune Microenvironment
**Principal Investigator:** Wei Sun, Fred Hutchinson Cancer Research Center
**Mechanism:** R21 (exploratory/developmental)
**Review Date:** 2026-10-28

---

## Overall Assessment

This R21 proposal aims to develop statistical methodology for estimating immune-cell composition in bulk tumor samples by jointly modeling gene expression and DNA methylation, and for incorporating deconvolution uncertainty into prognostic survival models. The proposed framework addresses several well-recognized limitations of existing deconvolution approaches: the assumption that blood-derived reference profiles apply without modification to the tumor microenvironment (TIME), the conflation of RNA proportion with cell-number proportion, and the failure to propagate estimation uncertainty into downstream analyses. The twin innovations—a likelihood-based joint expression–methylation model that refines references using tumor data, and a latent-variable survival model that treats immune fractions as shared unknown quantities—represent a coherent and methodologically sound advance over the state of the art at the time the proposal was written.

The project is appropriately scoped for the two-year R21 mechanism: Aim 1 delivers the core estimation framework with validation on simulated, experimental, and TCGA data; Aim 2 extends it to survival prediction with a fused-lasso penalty for stabilization across similar cell types. The PI brings relevant statistical genomics expertise, the co-investigator contributes methylation specialization, and clinical collaborators provide domain grounding in colorectal and lung cancer. The data are publicly available and ethically unproblematic.

Several material limitations temper enthusiasm. The proposal does not specify how many immune cell types the full model will ultimately resolve—the gap between the preliminary five broad categories and the stated 28 reference cell types is substantial and the identifiability implications of that expansion are not addressed. The validation strategy relies heavily on in silico mixtures and TCGA benchmarks that lack ground-truth cellular composition; the proposal acknowledges that flow-cytometry-validated datasets are limited but does not fully reckon with how that constrains calibration. The survival component employs a parametric hazard for computational convenience, yet the rationale for preferring exponential survival over a more flexible semiparametric specification is not developed. These are significant but not fatal limitations for an exploratory methods-development project. The proposal describes a worthwhile contribution with a defensible route to its stated goals, subject to specific premises about identifiability and reference data quality that require attention during execution.

**Overall Recommendation:** Conditional support (conditional_support), subject to the premises that (i) the expanded cell-type panel remains identifiable in the joint model given the available sample sizes and reference data, and (ii) the validation framework provides sufficient calibration evidence to support the intended prognostic applications.

---

## Scientific Value: B (Good) — Confidence: B (High)

The proposal addresses a question of genuine importance: how to recover the immune composition of bulk tumor samples when only molecular measurements are available, and how to use those estimates without treating them as error-free observations. The clinical motivation is well stated—immunotherapy has transformed oncology, and understanding the TIME is central to predicting response—though the present project properly limits its scope to composition estimation and prognostic signatures rather than claiming to predict immunotherapy benefit directly.

The attainable knowledge contributions are threefold. **Descriptive:** The framework will produce immune-cell composition estimates and cell-type-specific expression and methylation profiles across TCGA cancer types, generating a resource for the community. **Methodological:** The joint likelihood model, the two-component mixture for distinguishing informative features from noise, and the multiplicative cell-size/transcriptional-activity factors all represent principled solutions to recognized problems in the deconvolution literature. **Conceptual:** The treatment of estimated fractions as latent variables shared between the omics and survival components formalizes a key inferential concern—that two-stage approaches can bias associations and distort precision—that had been acknowledged but not systematically addressed.

The value is tempered by the exploratory nature of the work. The proposal is explicit that this is an R21 methods-development project, not a definitive biomarker validation. The longer-term goal of creating TIME biomarkers for epidemiological and translational studies is appropriately deferred. Within the stated scope, the contribution is well framed: new likelihood-based methods, open-source R packages, and a systematic analysis of TCGA data across cancer types.

---

## Innovation: B (Good) — Confidence: B (High)

The proposal identifies three distinct innovations relative to the contemporary state of the art (circa 2015–2017). The external literature search supports that these are genuine contributions rather than incremental variations on established approaches.

**First**, the joint modeling of expression and methylation for immune deconvolution is novel. Existing expression-based methods (CIBERSORT, Newman et al. 2015, *Nature Methods*) and methylation-based methods (Houseman et al. 2012, *BMC Bioinformatics*; Accomando et al. 2014, *Genome Biology*; Koestler et al. 2016, *BMC Bioinformatics*) operated in a single modality. The proposal's insight that methylation can help address transcriptional differences between cell types while expression provides complementary separation of closely related immune populations is well motivated. The preliminary data—showing that expression-derived and methylation-derived estimates are associated but not identical—provides empirical support for the premise that the two modalities carry non-redundant information.

**Second**, the likelihood framework that treats reference-cell profiles as initialization rather than fixed truth, allowing the abundant tumor samples to refine blood-derived references through coordinate ascent, addresses a recognized problem: blood-derived profiles may differ from TIME profiles due to activation state, local cytokine milieu, and other contextual factors. The proposal correctly notes that this is analogous to an empirical Bayes formulation where the bulk tumor data inform the cell-type-specific parameters.

**Third**, the joint survival–omics model with latent immune fractions represents a conceptual advance over the conventional two-stage approach. The external literature search confirms that at the time of the proposal, methods that formally propagated deconvolution uncertainty into survival or association models were essentially absent. The compositional-data formulation (log-ratio predictor with sum-to-zero constraint) and the fused-lasso penalty for stabilizing estimates across hierarchically similar cell types are both technically sound choices.

The innovation is moderate rather than transformative because each component draws on established statistical frameworks (coordinate ascent, log-normal approximation of mixtures, compositional regression, fused lasso). The integration is novel; the individual building blocks are not. This is entirely appropriate for an R21 and does not diminish the project's value, but it calibrates the innovation rating.

---

## Rigor: C (Moderate) — Confidence: B (High)

The methodological framework is described with commendable mathematical clarity. The notation, the specification of the likelihood components, the moment-matching approximation for log-normal mixtures, and the coordinate-ascent estimation strategy are all presented at a level of detail that permits evaluation. The proposed validation strategy includes several complementary approaches: in silico mixtures from measured cell-specific profiles, comparison with existing deconvolution methods, benchmarks against flow-cytometry-established compositions, and assessment of estimated tumor-context profiles against published measurements of immune cells isolated from TIME.

Several concerns prevent a higher rating.

**Identifiability and dimensionality.** The proposal states that reference expression data will cover 28 cell types from over 800 samples, and methylation references will combine 450k data from six studies. However, the preliminary work examined only five broad cell-type categories from ten specific cell types. The proposal does not address how the model will scale from five to 28 cell types, nor whether the available tumor sample sizes (TCGA colon cancer has approximately 400–500 samples; lung cancer cohorts are comparable) are sufficient to identify 28 cell-type proportions per sample plus cell-type-specific parameters for thousands of genes and CpGs. The coordinate-ascent algorithm alternates between estimating fractions given profiles and profiles given fractions, which in high dimensions may exhibit slow convergence or become trapped in local optima. The proposal states that cell-size/transcriptional-activity factors are "identifiable when tumor sample number exceeds immune-cell-type number," which is necessary but not obviously sufficient given the additional parameters being estimated.

**Validation limitations.** The strongest validation evidence would come from samples with both molecular measurements and ground-truth cellular composition determined by flow cytometry or immunohistochemistry. The proposal acknowledges that experimental datasets with flow-cytometry-established composition "will provide additional benchmarks," but the availability and size of such datasets are not specified. The TCGA analyses will benchmark the method on real tumor data, but TCGA lacks independent ground truth for cellular composition, so these analyses primarily assess internal consistency and face validity rather than absolute accuracy. The preliminary validation on six methylation samples with known five-cell-type proportions (RSS 0.029 vs. 0.043) is encouraging but extremely limited in scale.

**Survival model specification.** The choice of a parametric (exponential) hazard model for Aim 2 is motivated by computational efficiency, with a semiparametric alternative noted as a possibility. Exponential survival assumes constant hazard over time, which is unlikely to hold for cancer survival data. The proposal would be strengthened by a plan to assess the sensitivity of conclusions to this assumption or by committing to the more flexible formulation.

**Feature selection and the two-component mixture.** The two-component mixture for distinguishing informative from noise features is an appealing strategy supported by the preliminary perturbation experiment (RSS 0.055 vs. 0.155 after 10% probe perturbation). However, the proposal does not specify how the mixture will be estimated jointly with the other model parameters, and whether the classification of features as informative/noise will be probabilistic (soft) or hard. This matters for uncertainty propagation.

The preliminary work is directly relevant and competently executed. The TCGA colon-cancer analysis (hypermutated vs. non-hypermutated, Wilcoxon p = 0.046 for B cells, p = 0.005 for monocytes) provides biological plausibility. The distributional checks (normality of individual-CpG methylation within cell groups, log-normality of expression) and the simulation comparing log-normal GLM to normal linear regression are appropriate groundwork.

---

## Feasibility: C (Moderate) — Confidence: C (Moderate)

The feasibility assessment must weigh what the project needs to accomplish against what is specified about resources, data, and methods.

**Data availability (strength).** The project relies on publicly available data: TCGA for tumor samples, published purified-cell datasets for expression references (more than 800 samples, 28 cell types), and Illumina 450k methylation data from six studies for methylation references. These data exist and are accessible. The proposal describes a sensible harmonization strategy (cell-type-specific quantile normalization) with preliminary PCA evidence supporting its effectiveness. No new data collection is required, which removes a major source of timeline risk.

**Computational tractability (concern).** The likelihood involves thousands of genes, thousands of CpGs, potentially 28 cell types, and hundreds of tumor samples. The coordinate-ascent algorithm will iterate between fraction estimation and parameter estimation, each step itself a potentially high-dimensional optimization. The proposal mentions that "computationally intensive components" will be implemented in C/C++ with Rcpp, but provides no estimate of runtime, memory requirements, or convergence behavior. For an R21 with a two-year timeline, this is a material gap. A method that cannot run to convergence on TCGA-scale data within reasonable computational resources would fail to deliver its primary products.

**Team expertise (strength).** Wei Sun has published in statistical genomics and deconvolution methodology. The external search confirms relevant publications including EMeth (Zhang, Cai, Dai, Sun, *Scientific Reports*, 2021), which addresses methylation-based deconvolution with a similar philosophy of detecting features inconsistent with the deconvolution model; CARseq (Jin, Chen, Lin, Sun, *Nature Computational Science*, 2021), demonstrating cell-type-aware RNA-seq analysis; and participation in the TCGA colorectal cancer immune evasion study (Grasso, Giannakis, et al., *Cancer Discovery*, 2018). Michael Wu contributes methylation expertise. The clinical collaborators (Peters for colorectal cancer, Houghton for lung cancer) provide domain grounding.

**Timeline (adequately specified).** Aim 1 in year 1 and Aim 2 in year 2 is a reasonable allocation for an R21. The software deliverables (open-source R packages, C/C++ for performance-critical components) are appropriately planned.

**Critical dependencies.** The most important unaddressed dependency is whether the joint model remains identifiable and computationally tractable when scaled to 28 cell types with realistic sample sizes. The proposal would benefit from a power analysis or at least a reasoned argument about the effective sample size needed per cell type. The dependence on pre-estimated tumor purity (e.g., from copy-number data) is noted but the sensitivity of downstream estimates to errors in purity is not explored.

The feasibility rating reflects these uncertainties, moderated by the recognition that an R21 is explicitly exploratory and methods development need not deliver production-ready software or clinically validated biomarkers.

---

## Ethics and Safety: A (Excellent) — Confidence: A (Very High)

The project uses exclusively existing, public, de-identified omics data (TCGA, ENCODE, published immune-cell datasets) with no new participant recruitment or direct contact. The proposal states qualification for Exemption 4, consistent with the use of de-identified existing data. Investigators commit to not seeking identifying information, and data storage on secured machines with access limited to project personnel is appropriate.

No animal subjects, no intervention, no environmental hazard, and no dual-use concern are evident. The expected benefit is methodological and scientific; no direct benefit to original participants is claimed. These are near-ideal conditions for an ethics assessment: the research uses data already collected for other purposes under existing consent frameworks and generates no new risks.

The only minor note is that TCGA and similar datasets, while de-identified, can in principle present re-identification risks through genomic data; the proposal's commitment to secured storage and access control provides proportionate management of that residual risk.

---

## Management: C (Moderate) — Confidence: C (Moderate)

The management narrative is appropriately brief for an R21 application but leaves several practical questions unanswered.

**Personnel and expertise (adequately described).** Wei Sun (PI) and Michael Wu (co-investigator) bring complementary statistical expertise. The clinical collaborators (Peters, Houghton) provide cancer-specific domain knowledge. Fred Hutch provides institutional resources for TCGA-scale computation. The proposal does not itemize the personnel effort allocation, describe whether students or postdocs will contribute, or specify who will lead which components of implementation and validation. For a two-year R21, this level of detail is not unusual, and the absence of an itemized personnel plan does not by itself indicate incapacity. However, the substantial programming burden (R packages, C/C++ components, pipeline development) merits explicit attention to who will execute it.

**Schedule (reasonable at the milestone level).** The division into Aim 1 (year 1) and Aim 2 (year 2) provides a high-level schedule. The proposal does not specify intermediate milestones, deliverables within each aim, or decision points (e.g., criteria for expanding from five to 28 cell types, criteria for deeming the coordinate-ascent algorithm converged in practice). For an R21, this is a limitation but not a disqualifying one.

**Publication and dissemination (appropriately specified).** The commitment to open-source R packages, publication of methods and analyses, and TCGA data analyses is appropriate. The software deliverables (R packages with C/C++ integration) are well specified.

**Unspecified elements.** No CVs, budget, or detailed work plan are provided in the review materials. These are standard components of a full administrative application but their absence from the supplied text is a limitation of the evidence available for this review rather than evidence of incapacity. The management rating reflects what can be assessed from the narrative: the team is credible, the two-year timeline for two aims is plausible, and the institutional environment is supportive. The rating is moderated by the unspecified computational feasibility considerations noted under Feasibility.

---

## Summary Ratings

| Criterion | Rating | Confidence |
|-----------|--------|------------|
| Scientific Value | B (Good) | B (High) |
| Innovation | B (Good) | B (High) |
| Rigor | C (Moderate) | B (High) |
| Feasibility | C (Moderate) | C (Moderate) |
| Ethics and Safety | A (Excellent) | A (Very High) |
| Management | C (Moderate) | C (Moderate) |

---

## Recommendation: Conditional Support

The proposed work addresses a well-motivated gap in the deconvolution literature with a coherent statistical framework that integrates expression and methylation, refines reference profiles using tumor data, and propagates estimation uncertainty into survival modeling. The PI and team have relevant expertise and access to the necessary public data. The project is appropriately scoped for the exploratory R21 mechanism.

The following premises should be satisfied for the project to deliver its stated contributions:

1. **Identifiability at scale.** The joint model should remain identifiable and computationally tractable when expanded from the preliminary five broad cell-type categories to the intended finer resolution (up to 28 cell types). A reasoned argument or modest simulation addressing this scaling is needed before committing the full implementation effort.

2. **Validation calibration.** The reliance on TCGA (which lacks ground-truth cellular composition) and in silico mixtures for validation should be augmented with explicit attention to the available flow-cytometry-validated datasets, including their size, cell-type coverage, and expected precision. If such datasets are insufficient for calibration, the claims about absolute composition accuracy should be appropriately tempered.

3. **Survival model flexibility.** The parametric exponential hazard assumption should be examined for sensitivity, or the semiparametric alternative should be implemented as the primary formulation, given that constant hazard is unlikely to hold for cancer survival endpoints.

These are addressable concerns that affect the confidence in specific deliverables rather than the scientific merit of the approach. The proposal describes a worthwhile contribution to statistical methodology for tumor immunology, and the limitations identified here are appropriate targets for refinement during execution rather than reasons to redirect the project.