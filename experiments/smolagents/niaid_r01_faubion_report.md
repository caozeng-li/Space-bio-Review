# Scientific Project Review

## Proposal: "Inflammatory Cascades Disrupt Treg Function through Epigenetic Mechanisms"

**Principal Investigator:** William A. Faubion, Mayo Clinic
**Review date:** 2026-10-28
**Review type:** General scientific project evaluation

---

## Overall Assessment

This proposal investigates how IL6-mediated inflammatory signaling disrupts regulatory T cell (Treg) function through epigenetic mechanisms, centering on the histone methyltransferase EZH2. The central hypothesis—that IL6 activates JAK2, which phosphorylates EZH2 in its SET domain, weakening its histone methyltransferase activity and impairing Treg-mediated suppression—addresses a question of genuine importance for inflammatory bowel disease (IBD) biology and Treg-based cell therapy. The three aims are logically sequenced from molecular mechanism (Aim 1) through signaling biochemistry (Aim 2) to translational testing in experimental colitis (Aim 3).

The proposal builds on strong preliminary evidence that EZH2 is necessary for Treg maintenance, including a compelling conditional knockout phenotype (~80% mortality by 35 days in FOXP3ΔEZH2 mice, n=107), proximity ligation evidence of FOXP3-EZH2 association, RNA-seq data from Crohn's disease tissue, and initial IL6-JAK2-EZH2 signaling data. The PI's laboratory has relevant expertise in KLF-dependent FOXP3 regulation and experimental colitis.

However, the proposal faces several substantive challenges that limit confidence in its central mechanistic premise and the feasibility of its more translational aims. The most significant is that Y641 phosphorylation of EZH2 by JAK2 has already been reported in the lymphoma literature (Li et al. 2020 review, referencing prior work), and that phosphorylation was linked to EZH2 degradation via β-TrCP rather than the functional impairment model proposed here. The proposal's novelty claim therefore requires sharper differentiation from this prior work. Additionally, several aspects of the experimental design lack the precision needed to support the strong causal inferences promised. The treatment-colitis experiments in Aim 3 are intriguing but rest on molecular premises that Aims 1 and 2 must first establish. The proposal is strongest in its descriptive and correlational aims and weakest in its treatment claims, which would require substantially more preliminary evidence to be considered well-supported at this stage.

**Overall recommendation: Current proposal insufficient for support (revise).** The scientific direction is worthwhile and the preliminary data are substantial, but the current plan has material weaknesses in novelty differentiation, experimental design for causal inference, and feasibility of the translational aims that affect its main goals.

---

## Scientific Value: B (Good) — Confidence: B (High)

The proposal addresses a question with clear scientific and translational importance. The observation that FOXP3⁺ Tregs are present in inflamed IBD tissue yet fail to control inflammation is a well-recognized paradox in mucosal immunology (Arvey et al., *Nature Immunology*, 2014). Understanding how inflammatory environments subvert Treg function through epigenetic mechanisms could reveal new therapeutic strategies distinct from systemic cytokine blockade—particularly relevant given that IL6 supports epithelial homeostasis (proposal, Section 2). The proposal correctly identifies that "Tregs generally prevent colitis more efficiently than they treat established inflammation," motivating the treatment-focused experiments.

**What the work could contribute:**

- **Descriptive:** Mapping the FOXP3-EZH2 interaction domains and identifying IL6-dependent EZH2 phosphorylation sites in Tregs would provide valuable molecular anatomy. The RNA-seq comparison of Crohn's disease CD4⁺ cells with FOXP3/EZH2 chromatin targets already provides disease-relevant context.
- **Conceptual:** If successful, connecting IL6 → JAK2 → EZH2 phosphorylation → altered H3K27me3 → target gene derepression → impaired suppression would establish a new mechanistic link between inflammatory signaling and epigenetic maintenance of Treg identity. This would extend the DuPage et al. (2015, *Immunity*) finding that EZH2 is "critical for the maintenance of regulatory T cell identity after activation" by explaining *how* inflammatory signals disrupt that maintenance.
- **Methodological:** The proposal develops a phospho-Y641 antibody, EZH2-deficient HAP-1 cells for clean structure-function analysis, and genomically integrated luciferase reporters—all useful tools.
- **Practical:** Demonstrating that IL6Rα-deficient or phosphorylation-resistant EZH2 Tregs outperform wild-type Tregs in treating established colitis would have translational implications, though this remains distant.

The two-target-gene focus on RORA and IRF4 is a reasonable starting point that connects chromatin to transcription to function, though it narrows the scope of what can be inferred about genome-wide EZH2 effects. The human disease data (12 subjects, Crohn's vs. healthy ileum) strengthen the disease relevance, though they are correlational and from bulk CD4⁺ populations rather than purified Tregs.

The scientific value is good rather than excellent because the contribution, while important within the Treg field, would be primarily incremental in the broader context of EZH2 biology. The EZH2-Treg connection is established (DuPage et al. 2015; Hou et al., *J Exp Med*, 2019, for Tfr cells), and EZH2 phosphorylation as a regulatory mechanism is well-precedented (Cha et al., *Science*, 2005; Kim et al., *Cancer Cell*, 2013; Li et al. 2020 review documents phosphorylation at S21, T345, T487, Y244, Y641, T311, and others). The proposal's contribution would be connecting a specific inflammatory signal (IL6) to a specific phosphorylation event (Y641) in a specific cell type (Treg) with functional consequences for a specific disease context (colitis). This is valuable and appropriate for a mechanistic R01, but the framing sometimes implies greater novelty than the external literature supports.

---

## Innovation: C (Moderate) — Confidence: B (High)

**What the proposal adds beyond prior work:**

The proposal's most innovative elements are: (1) linking IL6-JAK2 signaling specifically to EZH2 phosphorylation in Tregs, connecting an inflammatory input to an epigenetic output; (2) testing whether interrupting this pathway preserves Treg function in *established* (treatment) rather than only *preventive* colitis models; and (3) the ex vivo TALEN editing of IL6Rα in primary Tregs as a potential therapeutic route.

**Relationship to key prior work:**

The DuPage et al. (2015) paper demonstrated that EZH2 is critical for Treg maintenance after activation, that CD28 co-stimulation induces EZH2, and that EZH2-deficient Tregs show a transcriptomic pattern similar to FOXP3-deficient Tregs. The Arvey et al. (2014) paper showed that FOXP3-bound sites in activated Tregs acquire H3K27me3 through EZH2 recruitment and that this is associated with gene repression. The current proposal extends this by asking how inflammatory signals disrupt this EZH2-dependent maintenance mechanism.

The Cha et al. (2005) paper established that Akt-mediated phosphorylation of EZH2 at S21 suppresses H3K27 methylation, providing the paradigm for kinase-mediated EZH2 regulation. The Kim et al. (2013) paper showed that EZH2 phosphorylation activates STAT3 signaling in glioblastoma, demonstrating cross-talk between phosphorylation and EZH2 function in a different context.

**Critical novelty concern regarding Y641:**

The proposal presents Y641 phosphorylation by JAK2 as a novel finding, with preliminary mass spectrometry and modeling data. However, the Li et al. (2020) review on EZH2 post-translational modifications states: "A study demonstrated that the phosphorylation of EZH2 at Y646 residue in human (Y641 in mouse) by JAK2 promotes the β-TrCP-mediated EZH2 degradation and consequent regulation of H3K27me3. They also found that JAK2-mediated pY641-EZH2 suppresses lymphoma pathogenesis." This indicates that JAK2-mediated phosphorylation of EZH2 at Y641 has already been reported, though in a lymphoma context and linked to degradation rather than direct enzymatic impairment.

The proposal's contribution would therefore be: (a) demonstrating this phosphorylation occurs in Tregs in response to IL6 (a different stimulus than the lymphoma context), (b) linking it to altered methyltransferase activity rather than degradation, and (c) showing functional consequences for Treg suppression. These are legitimate increments, but the proposal does not adequately discuss or differentiate itself from this prior report. The proposal's Y641E preliminary data (lower activity than WT or Y641F) is consistent with the proposal's model but does not exclude the degradation mechanism.

**The ex vivo editing approach** (Aim 3) using TALEN-mediated IL6Rα deletion in primary Tregs is conceptually interesting as a step toward cell therapy, but this is largely a feasibility demonstration rather than a novel concept—the use of gene-edited T cells for therapy is well-established in oncology.

Innovation is rated moderate because the core mechanistic pathway (IL6 → JAK2 → EZH2 phosphorylation → impaired Treg function) is a reasonable extension of known principles applied to a new cell-type and disease context, rather than a fundamentally new mechanism. The prior report of JAK2-mediated Y641 phosphorylation in lymphoma reduces the novelty of the central biochemical claim.

---

## Rigor: C (Moderate) — Confidence: B (High)

**Strengths of the experimental design:**

The proposal uses a well-structured multi-level approach: from domain mapping and biochemistry through chromatin analysis to cellular function and in vivo models. Several design features are commendable:

- The use of IL6Rα-deficient responder cells to isolate Treg-intrinsic IL6 effects (preliminary data, Section 4) is an elegant control.
- The CRISPR/Cas9-generated EZH2-deficient HAP-1 system enables clean structure-function experiments without endogenous EZH2 confounding results (Aim 2).
- The combination of phosphomimetic (S→D, Y→E) and phosphorylation-resistant (S→A, Y→F) substitutions provides complementary tests of the phosphorylation hypothesis.
- The inducible EZH2-deletion model separates developmental effects from mature Treg function.
- Blinded histological assessment is used in the DSS experiments.
- The proposal explicitly considers alternative mechanisms: indirect FOXP3-EZH2 association through EED/SUZ12, EZH2-associated lncRNAs for targeting, EZH1 compensation, effector-cell resistance, and changes in EZH2 substrates or partners rather than simple activity loss.

**Concerns about the experimental design:**

1. **Low nucleofection efficiency in primary cells (~20%):** This is acknowledged in the proposal. For experiments requiring clean comparisons (co-IP, ChIP, suppression assays), this efficiency may require substantial cell sorting and expansion, introducing potential selection and activation artifacts. The adenoviral approach (>75% transduction) partially addresses this, but adenoviral expression raises its own concerns about supraphysiological expression levels.

2. **Underpowered in vivo comparisons:** The treatment-colitis power calculation (4 RAG-null recipients per group, repeated 3 times, 90% power for 1.25 SD difference) is reasonable for detecting large effects in the transfer model. However, the treatment model (Aim 3) is inherently more variable than the prevention model—established colitis varies in severity between animals, and treatment effects are typically smaller than prevention effects. The proposal does not provide a separate power analysis for the treatment experiments. The 4-animal preliminary DSS experiment with EZH2 inhibitor is described as "preliminary" and was likely underpowered for reliable inference.

3. **Limited target gene scope:** Focusing on RORA and IRF4 provides a tractable experimental system, but these two genes cannot represent the full spectrum of EZH2-dependent Treg dysfunction. The proposal acknowledges 379 of 457 differentially expressed EZH2 target genes were upregulated in Crohn's samples, yet the functional experiments reduce this complexity to two reporters. This is appropriate for mechanism but limits the generality of conclusions.

4. **The Y641E/F comparison logic:** The proposal's hypothesis predicts that Y641E (phosphomimetic) reduces activity and Y641F (phosphorylation-resistant) maintains it during IL6 exposure. However, if Y641 phosphorylation primarily leads to degradation (as the lymphoma literature suggests), the Y641E construct's lower activity could reflect degradation rather than catalytic impairment. The proposal does not clearly address how it will distinguish these mechanisms.

5. **In vitro suppression assay limitations:** Standard anti-CD3/anti-CD28 suppression assays measure proliferation inhibition but may not capture the full repertoire of Treg suppressive mechanisms relevant to intestinal inflammation (IL-10 production, metabolic disruption, DC modulation). The proposal does measure IL-10 in some contexts, which partially addresses this.

6. **Correlational nature of some key evidence:** The human RNA-seq data show RORA and IRF4 upregulation in CD4⁺ cells from Crohn's lesions, and these overlap with FOXP3/EZH2 chromatin targets, but this is correlation, not evidence that EZH2 dysregulation causes these changes in human disease.

The rigor is moderate because the core molecular experiments in Aims 1 and 2 are well-designed for their descriptive goals, but several design features limit the strength of causal inference for the central mechanistic claim and the translational endpoints in Aim 3.

---

## Feasibility: C (Moderate) — Confidence: B (High)

**What is achievable:**

The domain-mapping experiments (Aim 1) use established methods (co-IP, proximity ligation, luciferase reporters, ChIP) that the laboratory has already implemented. The truncation constructs exist, and the PI reports experience with the expression systems. These experiments are likely to succeed in identifying interaction domains and testing their contribution to target gene repression, though whether a direct FOXP3-EZH2 interaction exists (versus PRC2-mediated association) is appropriately flagged as uncertain.

The IL6 signaling experiments (Aim 2) benefit from the availability of inhibitors (AZD1480), siRNA, dominant-negative constructs, and the phospho-Y641 antibody. The EZH2-deficient HAP-1 system is established. Identifying phosphorylation sites by mass spectrometry is technically demanding but achievable with the institutional proteomics core.

**Critical dependencies and risks:**

1. **The Y641 hypothesis may not hold:** The prediction scores in Table 1 are notably low for Y641 (NetPhos2.0: 0.014; GPS3.0: 1). In contrast, S610 and S639 have high prediction scores (NetPhos2.0: 0.992 and 0.942, respectively) and are predicted MAPK targets. If JAK2 does not directly phosphorylate Y641 in Tregs, Aim 2 would need to pivot to other sites or kinases. The proposal acknowledges this by evaluating six candidate sites, but the mechanistic pipeline is built around Y641 as the exemplar.

2. **Adenoviral reconstitution in primary Tregs:** The proposal plans to reconstitute FOXP3ΔEZH2 Tregs with wild-type or mutant EZH2 via adenoviral delivery, using CAR-transgenic mice for efficient transduction. This is a complex genetic cross that adds time and potential confounders. The efficiency and persistence of expression in primary Tregs are not well-documented.

3. **Established-colitis treatment model:** Tregs are known to prevent colitis better than they treat it. The treatment window (4 weeks after effector cell transfer) and the dose range (100,000–300,000 Tregs) may not be sufficient to show clear treatment effects. The proposal's own background section notes this challenge. If wild-type Tregs show minimal treatment effect, comparing modified Tregs becomes difficult.

4. **TALEN editing of primary Tregs:** Ex vivo editing, selection, and expansion of primary Tregs for adoptive transfer is a multi-step process with attrition at each step. The proposal acknowledges transient transfection and fluorescent selection but does not detail expansion yields, purity, or functional validation of edited cells before transfer. The collaboration with the institutional genetics core is noted but not elaborated.

5. **Schedule feasibility:** Five years is reasonable for the scope, but the interdependence of aims is a risk. Aim 3 treatment experiments depend on Aim 2 identifying the relevant phosphorylation sites and Aim 1 providing interaction-defective constructs. If the mechanistic work takes longer than expected, the in vivo treatment experiments could be compressed.

**Animal numbers:** 2,139 mice over five years appears reasonable for the described experiments, including breeding. The specification of both sexes is appropriate, though sex as a biological variable is not explicitly analyzed in the experimental design.

The feasibility is moderate because the molecular aims have established methodological foundations, but the translational aims depend on mechanistic premises that have not yet been validated, and several technical challenges (primary cell transduction, treatment-colitis efficacy, ex vivo editing yields) represent real risks.

---

## Ethics and Safety: B (Good) — Confidence: B (High)

The proposal describes appropriate animal welfare provisions: IACUC review at an accredited facility, daily weighing and examination, predefined humane endpoints (20% body weight loss or moribund appearance), analgesic support, and approved euthanasia procedures. These are standard and appropriate for colitis models.

The Y641F concern is explicitly noted: "Y641F has been associated with lymphoproliferative conditions in other settings." The proposal appropriately flags this and plans to investigate long-term function and toxicity. This is responsible.

The human tissue expression data (Crohn's disease resections and healthy ileal biopsies) were obtained from 12 age- and sex-matched subjects. The proposal does not detail the consent and ethics approval for these samples, but they form part of the preliminary data rather than proposed work. The proposed studies are exclusively in mice.

The resource-sharing plan (cell and mouse lines available to other investigators; PubMed Central deposition) is appropriate.

No human subjects research is proposed. The animal work is within standard immunological research practice. No environmental or dual-use concerns are apparent.

The rating is good: the provisions are appropriate and proportionate, with the Y641F concern responsibly noted, though some details of human tissue ethics approval are unspecified (this does not materially affect the rating).

---

## Management: C (Moderate) — Confidence: C (Moderate)

**Strengths:**

The PI has relevant expertise in intestinal immunology and Treg biology, with prior work on KLF-dependent FOXP3 regulation, chromatin modification, and experimental colitis. The laboratory has established the key methods (co-IP, ChIP, proximity ligation, transfer colitis, Treg culture) and generated the critical mouse models (FOXP3ΔEZH2, inducible deletion, CAR-transgenic crosses).

The institutional resources at Mayo Clinic are strong: flow cytometry, confocal imaging, sequencing and gene-expression cores, proteomics, mouse genetics, biochemical instrumentation, and tissue culture facilities are all available.

The identified collaborations (epigenetics, structural modeling, proteomics, genome-editing core) cover the required expertise beyond the PI's own laboratory.

**Weaknesses:**

The proposal provides limited information about the specific roles, time commitments, and expertise of key personnel beyond the PI. An R01 application would normally include detailed biosketches; their absence from the supplied text limits evaluation of whether the team has the specific expertise needed for structural modeling, mass spectrometry-based phosphoproteomics, and TALEN editing. This is not evidence of incapacity but leaves the management assessment less well-supported than it could be.

The schedule and coordination across the three aims are described at a high level. The interdependence of aims—Aim 3 depends on Aims 1 and 2 for reagents and mechanistic understanding—creates a coordination requirement that is acknowledged but not detailed with milestones or decision points.

The animal section provides aggregate numbers but limited detail on how breeding, genotyping, and experimental scheduling will be coordinated across the five-year period.

The management rating is moderate: the PI's expertise and institutional resources are appropriate, but the proposal lacks the personnel and coordination detail that would support stronger confidence.

---

## Summary Ratings

| Criterion | Rating | Confidence |
|-----------|--------|------------|
| Scientific Value | B (Good) | B (High) |
| Innovation | C (Moderate) | B (High) |
| Rigor | C (Moderate) | B (High) |
| Feasibility | C (Moderate) | B (High) |
| Ethics and Safety | B (Good) | B (High) |
| Management | C (Moderate) | C (Moderate) |

---

## Recommendation: Current Proposal Insufficient for Support (Revise)

The proposal addresses an important question—how inflammatory signaling disrupts Treg epigenetic maintenance—with substantial and relevant preliminary data. The conditional EZH2 knockout phenotype, the FOXP3-EZH2 proximity ligation, the human Crohn's disease expression data, and the initial IL6-JAK2-EZH2 findings together provide a credible foundation. The three-aim structure is logical, and the PI has appropriate expertise.

However, the current plan has substantive problems that affect its main goals. The central novelty claim—IL6-JAK2-mediated Y641 phosphorylation of EZH2 impairing Treg function—requires sharper differentiation from prior reports of JAK2-mediated Y641 phosphorylation in lymphoma. The treatment-colitis experiments in Aim 3, while conceptually attractive, rest on mechanistic premises that Aims 1 and 2 must first validate, and the experimental design may lack the sensitivity to detect treatment effects reliably. The low nucleofection efficiency and complex genetic crosses introduce practical risks.

**Key issues to address in revision:**

1. **Differentiate from prior JAK2-EZH2-Y641 work.** Explicitly discuss the prior report of JAK2-mediated Y641 phosphorylation and its link to EZH2 degradation. Clarify whether the proposal's model (catalytic impairment) is distinct from or complementary to the degradation model, and how the experiments will distinguish these mechanisms.

2. **Strengthen the treatment-colitis design.** Provide a separate power analysis for the treatment experiments, justify the treatment window and Treg dose, and consider whether a positive control (e.g., anti-IL6R antibody treatment) would help interpret the magnitude of any Treg treatment effect.

3. **Address primary cell transduction challenges.** Provide more detail on the efficiency, expression levels, and persistence of adenoviral EZH2 reconstitution in primary FOXP3ΔEZH2 Tregs, or consider alternative delivery approaches.

4. **Expand the alternative mechanism analysis.** Given the low prediction scores for Y641 phosphorylation, provide a clearer contingency plan if Y641 is not the functionally relevant IL6-JAK2 target in Tregs.

The scientific direction is worthwhile, and a revised proposal addressing these issues could make a strong case for support.