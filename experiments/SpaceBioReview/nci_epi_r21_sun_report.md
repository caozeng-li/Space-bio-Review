# Project review

## Overall assessment

This R21 project develops statistical methods for estimating the immune-cell composition of bulk tumor samples by jointly modeling gene expression and DNA methylation data. The intended products are an open-source likelihood-based deconvolution framework, R software, and a joint survival model that propagates compositional uncertainty into prognostic signatures rather than using point estimates. Analyses will be benchmarked using The Cancer Genome Atlas and external reference datasets, initially focusing on colon and lung cancer. The most valuable expected knowledge is a validated procedure that provides calibrated immune-cell abundance estimates capable of improving survival prediction and laying the groundwork for downstream tumor-immune microenvironment biomarker studies.

The proposal's main strengths are its principled joint-likelihood framework, which uses complementary omics signals to separate related cell types and address cell-size effects, and the novel integration of deconvolution uncertainty into a joint survival model. The PI's demonstrated track record includes published methods for both expression (ICeD-T) and methylation (EMeth) deconvolution, and the reference data assembly plan is credible given prior harmonization pipelines and available large-scale datasets. The most material limitations are that the identifiability of the cell-size scaling factors, which is central to the novel joint approach, is not supported by the specified constraints; the validation design does not specify how flow-cytometry ground truth will be reconciled with the RNA-versus-cell-number proportion distinction; and the reversion to the standard Fenton-Wilkinson approximation contradicts the PI's own prior improvements. These are addressable within an exploratory R21, but they are not merely minor omissions.

The supported strengths are weighed against identifiable risks to feasibility and rigor: the cell-size factor identifiability problem lacks the necessary constraints, the joint model's practical identifiability leaves the added value for fraction estimates uncertain, key validation components are insufficiently specified, and the two-year timeline is optimistic for delivering both aims with full rigor. On balance, the project addresses a worthwhile set of problems and proposes a credible, though unproven, methodological path that an R21 mechanism is designed to support. Conditional support is appropriate, with the condition that Aim 1 and its validation constitute the primary milestone and Aim 2 is treated as a preliminary exploration. The specific conditions are detailed under Management below.

## Ratings and review outcome

Rating A-E: excellent, good, moderate, weak, very weak. Confidence A-E describes confidence in the assessment, not project success.

| Dimension | Rating | Confidence |
| --- | --- | --- |
| Scientific value | B | B |
| Innovation | B | B |
| Rigor | C | B |
| Feasibility | B | B |
| Ethics and safety | A | A |
| Management | B | B |

**Overall conclusion: Conditional support**

## Scientific value

**The project addresses an important knowledge gap with well-motivated methods, but the attainable advance is moderated by the exploratory scope and unresolved uncertainties.** Accurate, uncertainty-quantified estimation of the tumor immune microenvironment (TIME) from routinely collected bulk omics would provide a broadly enabling analytical capability for epidemiological studies, biomarker development, and stratification for immunotherapy.

The proposal correctly identifies two recognized limitations of current practice. Single-modality deconvolution cannot leverage complementary signals from expression and methylation, and two-stage workflows that treat estimated immune-cell fractions as fixed covariates in downstream analyses bias associations and distort precision [1]– [1]. Joint likelihood modeling of both omics, with shared latent fractions and a linked survival component that propagates compositional uncertainty, is a principled response to both problems.

**The preliminary work provides credible empirical motivation.** Separate expression- and methylation-based deconvolution of TCGA colon-cancer samples produced associated but non-identical composition estimates, with hypermutated tumors showing lower B-cell and higher monocyte proportions (Wilcoxon *p* = 0.046 and 0.005, respectively) [1]. Simulated mixtures of five log-normal cell-type distributions demonstrated that a log-normal generalized linear model outperformed normal linear regression [1]. A known-mixture validation with six methylation samples and 362 CpGs gave a residual sum of squares of 0.029 for maximum-likelihood estimation versus 0.043 for non-negative least squares; the mixture-based method remained more robust after perturbation of 10% of probes (RSS 0.055 versus 0.155) [1]. These results motivate both the joint framework and the two-component mixture for separating informative from noise features [1].

Three considerations moderate the attainable value. First, whether adding methylation meaningfully sharpens immune-cell estimates beyond what well-executed expression-only or methylation-only deconvolution can achieve depends on the information gain from complementary signals, which has not yet been demonstrated. Second, the joint survival model's practical contribution hinges on whether propagating compositional uncertainty produces better-calibrated or more discriminating survival signatures than a two-stage approach. The extreme information asymmetry—thousands of omics features per sample versus a single scalar hazard contribution, further attenuated by censoring—suggests that fraction estimates will be dominated by the omics likelihood, reducing the joint model in practice to uncertainty propagation rather than genuine co-estimation. Whether this refinement improves survival prediction relative to simpler alternatives is an open empirical question that the planned C-index evaluation will partially address, though the expected effect sizes (0.01–0.04 C-index units) approach the detectable threshold for TCGA sample sizes [1], [2]. Third, the work is developed and benchmarked on public TCGA and reference data rather than validated in a clinical prediction context, consistent with an R21's exploratory remit but limiting immediate translational value.

The analytical capability resulting from a successful project—software returning cell-type abundance estimates with associated uncertainty from one or both omics modalities—would be genuinely useful for investigators seeking to incorporate TIME composition into epidemiological and biomarker studies. The explicit commitment to open-source R packages with computationally intensive components in C/C++ via Rcpp [1] enhances practical accessibility. On balance, the question is well-chosen, the analytical need is real, and the proposed capability would represent a meaningful advance for the field.

## Innovation

**The proposal makes a genuine methodological contribution through the joint expression-methylation likelihood framework, and its survival-uncertainty propagation model is without direct precedent.**

The central innovation is a joint likelihood where the same latent cell fractions *ρ*\_qi appear in both the expression and methylation components [1]. This is qualitatively different from running separate deconvolutions and comparing results post hoc. The constraint that both modalities reflect the same underlying tissue provides statistical strength, and systematic discrepancies between RNA- and methylation-derived estimates are intended to inform multiplicative cell-size and transcriptional-activity scaling factors [1]. A conceptually similar unified cross-omics framework (Unico, Chen et al. 2025) has recently appeared [3], confirming the timeliness of the problem, but its modeling approach differs from the proposal's explicit likelihood-based formulation, so the proposed implementation remains a distinct contribution.

The second novel element—propagating deconvolution uncertainty into a joint survival model by treating immune-cell fractions as latent variables in both the omics and survival likelihoods [1]—has no clear precedent. Existing approaches either compute sample-level accuracy metrics such as the CETYGO score for quality control without downstream propagation, or provide posterior distributions but use point estimates for association analyses. The proposal's compositional-data approach with log fractions and a zero-sum constraint (Σ β\_q + γ = 0) is grounded in the compositional data analysis literature [1]– [1], [4]. The fused-lasso penalty across hierarchically clustered cell types to stabilize estimation of similar immune subsets [1] is a reasonable regularization strategy, though its sensitivity to clustering method and the assumption that omics adjacency implies survival similarity require further examination.

**Several individual components have clear precedent and are best understood as sound engineering choices.** The coordinate-ascent algorithm that alternates between estimating cell fractions and refining reference profiles draws on a well-established strategy. ISOpure (Quon et al. 2013) used block coordinate descent to alternately estimate tumor purities and a shared cancer reference profile before estimating individual profiles. The normal approximation for methylation beta values at each CpG within each cell type directly follows EMeth (Zhang et al. 2021), the PI's own prior work, which models observed methylation as a mixture of two normal distributions on the beta-value scale [1], [5]. The two-component mixture for distinguishing informative from noise features [1] parallels the aberrant/consistent CpG distinction in EMeth, though the proposal extends it to expression and makes it operate jointly, representing an incremental adaptation.

Overall, the joint likelihood framework and the survival-uncertainty propagation model constitute genuine advances that distinguish this work from existing single-modality or two-stage approaches. The project is appropriately characterized as having good innovation for an exploratory R21: it combines established building blocks in a principled new configuration and addresses a recognized gap that the field has not yet tackled systematically.

## Rigor

**The multi-layered validation design uses appropriate tools but contains material gaps that weaken the inferential strength achievable for the central claims.** The proposal deploys five complementary validation strategies—simulated mixtures, comparison with existing methods, flow-cytometry benchmarks, TIME-isolated cell profiles, and TCGA analyses—that together address the major dimensions of deconvolution performance [1]– [1]. However, each layer carries limitations that collectively reduce confidence in the conclusions obtainable.

### Simulation strategy and reference context

The simulation strategy generates bulk tissues from weighted mixtures of measured cell-specific expression and methylation [1]. This is standard practice in the deconvolution literature and provides necessary internal consistency checks, but it cannot capture the context-dependent remodeling of cell-type-specific molecular profiles that occurs when immune cells enter the tumor microenvironment—a limitation the proposal itself identifies [1]. As Dai et al. (2025) note, non-cancer-specific benchmarks "may inform what the worst performing model types under the specific data scenario are, but limited inference can be made for future applications to cancer studies" [6]. The proposal mitigates this through planned use of TIME-isolated immune cell profiles and the iterative reference refinement via coordinate ascent [1], [1], but the simulation component alone cannot validate performance in real tumor contexts.

### Flow-cytometry validation gaps

The flow-cytometry validation plan is the most consequential gap. The proposal states only that "experimental datasets with flow-cytometry-established composition will provide additional benchmarks" without identifying specific datasets, sample sizes, cell types, or tumor types [1]. The literature context reveals that flow-cytometry-validated tumor deconvolution datasets are small and primarily blood-derived. Finotello et al. validated quanTIseq against immunohistochemistry in melanoma (n=70), lung (n=8), and colorectal (n=10) samples, with Pearson correlations of 0.32–0.75, 0.55–0.91, and 0.58–0.90 respectively, but their flow cytometry validation used only blood-derived immune cell mixtures from healthy donors [7], [7]. Salas et al. validated methylation deconvolution against FACS using only six healthy donor samples and artificial DNA mixtures [8]– [8]. These precedents establish that ground-truth data cover only major lineages (CD4+, CD8+ T cells, B cells, NK cells, monocytes, neutrophils) and rarely include the finer subsets that the proposal's 28-cell-type reference panel intends to distinguish [1].

More fundamentally, the proposal does not specify how flow-cytometry comparisons will account for the acknowledged mismatch between RNA-derived proportions and cell-number proportions. Flow cytometry counts cells; RNA-based deconvolution estimates RNA contributions that scale with cell size and transcriptional activity [1]. Without an explicit protocol for interpreting the expected pattern—raw RNA-based estimates should deviate from flow cytometry in predictable, cell-type-specific directions—the validation results will be difficult to interpret. The plan to estimate multiplicative scaling factors from systematic RNA-methylation differences is a legitimate approach [1], but its success depends on whether these factors are identifiable, a condition the proposal asserts without the required constraints.

### Uncertainty quantification and calibration

The uncertainty quantification, a distinctive strength of the likelihood framework, lacks a concrete validation strategy. Where truth is known (in silico mixtures, the preliminary known-mixture experiment [1]), calibration can be checked directly by comparing the fraction of true values falling within estimated confidence intervals to the nominal level. In real tumor data where ground truth is unavailable, the proposal could examine sensible patterns—whether uncertainty increases in samples with lower tumor purity or discordant expression-methylation signals—as qualitative diagnostics, though these do not substitute for calibration validation.

### Modeling choices and sensitivity

Two additional concerns affect the strength of conclusions. The proposal reverts to the standard Fenton-Wilkinson moment-matching approximation for sums of log-normal contributions [1], but the PI's prior ICeD-T work adopted a modified weighted variance structure because "the weighted model demonstrated improved fit and stability in simulated data" [9]; this reversion is unexplained. The sensitivity of the fused-lasso ordering to clustering method choice [1], which could be assessed by comparing results across common linkage methods, is unexamined. These do not individually undermine the project, but they contribute to a pattern where key design choices are asserted rather than justified against the PI's own prior evidence and available alternatives.

The TCGA analyses provide scale but not ground truth, and the proposal appropriately acknowledges that "TCGA sampling may underrepresent highly immune-infiltrated tumors" and that survival data "may be heavily censored" [1], [1]. On balance, the validation design is appropriately multi-layered for an exploratory R21—the TIME-isolated profiles, the weighted Kullback–Leibler tumor clustering [1], and the comparative evaluation against single-modality and two-stage alternatives [1] are thoughtful features—but the underspecification of flow-cytometry validation, the absence of uncertainty calibration checks, and the unresolved modeling choices limit the strength of conclusions that can be drawn from the planned experiments.

## Feasibility

**Feasibility is good: the PI's demonstrated track record, available public datasets, and computational infrastructure provide a credible basis for delivering core methods and software, though unresolved identifiability challenges and a compressed two-year timeline introduce real risks to completing both aims with full rigor.**

### Computational foundation and prior work

The PI has directly relevant published expertise in both expression-based deconvolution (ICeD-T) and methylation-based deconvolution (EMeth). EMeth implemented a two-component normal mixture with an EM algorithm, ridge penalty, aberrant CpG detection, simulation studies, in silico mixture validation, and TCGA analysis across four cancer types—a scope comparable to Aim 1 of the current proposal [5]– [5]. ICeD-T handled the log-normal expression model, the two-component mixture for aberrant genes, and data-driven variance structures [9], [9]. The current project extends these by adding a second modality into a coordinated joint likelihood with shared latent fractions and coordinate-ascent optimization. The step from EMeth's single-modality EM algorithm to the proposed joint framework is genuine additional complexity, but the PI has demonstrated the ability to build, debug, and benchmark methods of this class.

### Reference data plan

The reference data plan is credible. The methylation component inherits EMeth's pipeline, which already assembled and harmonized Illumina 450k data from six studies across seven immune cell types with cross-study batch-effect removal [5]. The expression reference plan—to assemble more than 800 samples covering 28 cell types from published datasets—does not identify a specific dataset list, but the proposal cites multiple concrete resources including BLUEPRINT (197 individuals with matched methylation and expression across three cell types), ENCODE, and several individual studies [1]– [1], [5]. The IHEC EpiATLAS now provides more than 2,000 uniformly processed reference epigenomes [10]. Cell-type-specific quantile normalization, supported by the existing smooth quantile normalization framework (Hicks et al. 2018), is a reasonable harmonization strategy [1], [11]. For expression data, cross-platform harmonization between RNA-seq and microarrays presents additional challenges that the proposal addresses by requiring read-depth-corrected gene counts and pre-log intensities [1], though residual batch effects will need cautious interpretation.

### Identifiability of cell-size scaling factors

The principal feasibility risk concerns the identifiability of the multiplicative cell-size scaling factors central to the joint model's value proposition. The PI's own prior work (ICeD-T) did not attempt to estimate such factors from data but instead borrowed externally derived cell size factors from Racle et al. [9]. The MOADE framework (Sun et al. 2025) addresses the structurally identical problem but enforces non-negativity constraints during optimization without guaranteeing unique recovery without additional scale restrictions [12]– [12]. The proposal's assertion that the factors are identifiable when sample number exceeds cell-type number is necessary but not sufficient; at minimum, a scale-fixing constraint such as fixing one cell type's factor or imposing Σ s\_q = Q must be imposed. This is precisely the kind of methodological challenge an R21 is designed to investigate, and the project could productively frame identifiability as a hypothesis to be tested, but the current proposal treats it as settled.

### Timeline and scope

The two-year timeline with Aim 1 in year 1 and Aim 2 in year 2 is tight but not infeasible. EMeth demonstrates that a scope comparable to Aim 1 can be completed by a team with the PI's capabilities. However, the joint expression-methylation framework adds integration complexity beyond EMeth, and the reference data assembly and harmonization alone represents months of work. A realistic R21 outcome is completing Aim 1 with thorough validation and delivering a working joint deconvolution method; Aim 2 may reach proof-of-concept rather than a fully benchmarked survival method across multiple cancer types. This remains a respectable R21 outcome. The PI's track record, the availability of required data, and the institutional resources together provide sufficient confidence that the core methods and software can be delivered.

## Ethics and safety

**The ethical framework and data-security arrangements are appropriate and proportionate for secondary computational research using existing public, de-identified data.**

The study relies entirely on existing public and de-identified omics datasets—principally TCGA, ENCODE, and published immune-cell reference studies—together with available clinical covariates [1]. No new specimens are collected, no participants are recruited or contacted, and the investigators commit not to seek identifying information. Under the applicable regulatory framework, the work qualifies for Exemption 4 (secondary research using de-identified data), which the proposal cites explicitly [1]. The core ethical requirements of informed consent and minimization of participant burden do not arise. The proposal correctly notes that no direct benefit to the original participants is anticipated and that the expected benefit is methodological and scientific [1]. This distribution of benefit is characteristic of methods-development research using public repositories and raises no additional ethical concerns, provided the original datasets were collected under appropriate consent frameworks, which for TCGA and ENCODE is well-documented.

The data-security provisions state that data will be stored on designated secured machines accessible only to project personnel [1]. For a computational project working entirely with public, de-identified data, this level of access control meets the standard of proportionate risk management. The main residual risk—re-identification from genomic data—is low given that TCGA and similar repositories have already applied standard de-identification procedures, and the investigators are not seeking to cross-reference with identified datasets. No environmental, animal, or biosafety risks are associated with purely computational statistical methodology development.

In summary, the ethical and safety arrangements are complete and well-matched to the nature of the work. The exempt status is correctly identified, data access controls are specified, and the absence of participant contact, biological materials, or environmental hazards means that no material risks require further mitigation.

## Management

**The management arrangements are well-configured for the scope of an R21, with appropriate expertise, institutional support, and a clearly defined dissemination plan, though the compressed timeline for sequential aims introduces delivery risk that affects Aim 2 in particular.**

The PI, Wei Sun, contributes established expertise in statistical genomics, copy-number analysis, and expression-based deconvolution, as demonstrated by the ICeD-T and EMeth publications. The co-investigator, Michael Wu, brings complementary strength in methylation analysis and association methods [1]. This pairing is well-matched to the project's dual-modality design. The additional collaborations with Ulrike Peters and McGarry Houghton provide domain-specific clinical context for colorectal- and lung-cancer applications respectively [1], anchoring the methodological development in relevant cancer types.

The institutional environment at Fred Hutchinson Cancer Research Center supplies the necessary computational and statistical infrastructure for TCGA-scale analyses [1]. No specialized equipment, wet-lab facilities, or external data generation are required; the work is entirely computational and draws on public datasets whose access arrangements are already in place [1]. This reduces dependency risk substantially.

The schedule allocates year 1 to Aim 1 (joint deconvolution framework, reference assembly, and validation) and year 2 to Aim 2 (joint survival-omics model) [1]. This sequential structure is logical, as Aim 2 depends on the fraction estimates and software infrastructure developed in Aim 1. In practice, the year-1 allocation must accommodate reference data harmonization across 28 cell types and two modalities, implementation and debugging of the joint coordinate-ascent algorithm, simulation studies, method comparisons, and flow-cytometry validation—a substantial workload. A realistic outcome is that Aim 1 reaches completion with thorough validation, while Aim 2 achieves proof-of-concept rather than a fully benchmarked survival method. This remains a respectable R21 outcome.

The dissemination plan is well-specified: methods, analyses, and software will be published in the peer-reviewed literature, and all methods will be implemented as open-source R packages with computationally intensive components in C or C++ integrated via Rcpp [1]. This commitment to accessible, efficient software aligns with community standards and maximizes the practical utility of the work beyond the project period.

**Conditions for support.** The following conditions should be met before Aim 2 proceeds to substantive development, and they should be addressed in Aim 1's design and reporting:

- Specify the identifiability constraints for the multiplicative cell-size scaling factors, with at minimum one scale-fixing constraint and a sensitivity analysis of this choice.
- Provide a detailed flow-cytometry validation protocol that identifies specific datasets, expected cell-type coverage, and a plan for reconciling RNA-derived fractions with cell-number proportions from flow cytometry.
- Justify the reversion from ICeD-T's weighted variance structure to the standard Fenton-Wilkinson approximation, or revert to the prior improved formulation with supporting evidence.
- Treat Aim 2 as a preliminary feasibility investigation, with full benchmarking deferred to subsequent work if the joint deconvolution framework proves successful.

## References

[1] [nci\_epi\_r21\_sun](<D:/master/SpaceBioReview/demo_11/experiments/SpaceBioReview/runs/nci_epi_r21_sun/attempt-001/native/sources/src-1babb16cc064/original.md>) (Application material)

[2] [Deep learning with multimodal representation for pancancer prognosis prediction.](<https://europepmc.org/article/MED/31510656>). Cheerla A, Gevaert O; 2019-07-01 (External source)

[3] [Unico: a unified model for cell-type resolution genomics from heterogeneous omics data.](<https://europepmc.org/article/MED/41044605>). Chen ZJ, Rahmani E, Halperin E; 2025-10-03 (External source)

[4] [Variable selection in regression with compositional covariates](<https://doi.org/10.1093/biomet/asu031>). W. Lin, P. Shi, R. Feng, et al.; 2014-08-13 (External source)

[5] [EMeth: An EM algorithm for cell type decomposition based on DNA methylation data.](<https://europepmc.org/article/MED/33707472>). Zhang H, Cai R, Dai J, et al.; 2021-03-11 (External source)

[6] [A guide to transcriptomic deconvolution in cancer.](<https://europepmc.org/article/MED/41331516>). Dai Y, Guo S, Pan Y, et al.; 2025-12-02 (External source)

[7] [Molecular and pharmacological modulators of the tumor immune contexture revealed by deconvolution of RNA-seq data](<https://europepmc.org/article/PPR/PPR23480>). Finotello F, Mayer C, Plattner C, et al.; 2017-11-22 (External source)

[8] [An optimized library for reference-based deconvolution of whole-blood biospecimens assayed using the Illumina HumanMethylationEPIC BeadArray.](<https://europepmc.org/article/MED/29843789>). Salas LA, Koestler DC, Butler RA, et al.; 2018-05-29 (External source)

[9] [ICeD-T Provides Accurate Estimates of Immune Cell Abundance in Tumor Samples by Allowing for Aberrant Gene Expression Patterns.](<https://europepmc.org/article/MED/33012900>). Wilson DR, Jin C, Ibrahim JG, et al.; 2019-09-16 (External source)

[10] [EpiATLAS – a reference for human epigenomic research](<https://europepmc.org/article/PPR/PPR1260695>). , Manz Q, Bilenky M, et al.; 2026-06-26 (External source)

[11] [Smooth quantile normalization.](<https://europepmc.org/article/MED/29036413>). Hicks SC, Okrah K, Paulson JN, et al.; 2018-04-01 (External source)

[12] [MOADE: a multimodal autoencoder for dissociating bulk multi-omics data.](<https://europepmc.org/article/MED/41029754>). Sun J, Malik AA, Lin T, et al.; 2025-09-30 (External source)
