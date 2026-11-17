# Lymphoid Signals for Stromal Growth and Organization in the Thymus

Principal investigator: Howard T. Petrie, The Scripps Research Institute. Mechanism: R21. Planned duration: two years.

## 1. Project overview

The project will investigate how developing lymphoid cells induce growth, differentiation and survival of thymic stromal cells. The thymus supplies the microenvironment in which incoming bone-marrow progenitors develop into T lymphocytes, but this relationship is reciprocal: stromal development and organization also depend on lymphoid cells. The proposed work addresses the less understood lymphoid-to-stromal direction of this interaction.

An inducible model of growth in the small thymus of interleukin-7-receptor-deficient (IL7R-deficient) mice will be combined with a previously developed method for mapping stromal gene expression in intact tissue regions. The study will characterize changes across the growth response, identify stromal receptors with corresponding lymphoid ligands, prioritize candidate interactions using expression and external evidence, and test the biological relevance of a small number of candidates in existing mouse models.

The intended products are a dynamic expression resource, a searchable and prioritized collection of candidate signaling interactions, and initial biological validation. Detailed dissection of how individual signals control proliferation, differentiation or survival is a subsequent research direction.

## 2. Background and rationale

T lymphocytes are continually lost and replaced. Because the thymus does not maintain a self-renewing stem-cell population, sustained T-cell production requires recruitment of circulating marrow-derived progenitors. Stromal signals then regulate lineage specification, proliferation, differentiation, selection and survival. An emphasis on these stromal-to-lymphoid instructions has left the reciprocal signals insufficiently characterized.

Loss of lymphoid cells changes thymic architecture and stromal function. Evidence includes experimental thymic crosstalk and the development of functional thymic tissue after hematopoietic transplantation in severe combined immunodeficiency. These observations motivate investigation of the signals that support stromal growth and organization.

The methodological difficulty is that stromal cells are hard to isolate, especially in a hypoplastic organ. Dissociation can also alter expression or destroy the three-dimensional context. The team's alternative compares gene expression in microdissected cortical or medullary tissue with expression in lymphocytes from the corresponding region. The tissue signal that cannot be attributed to the lymphoid component is used to estimate stromal expression. This method samples the stromal population of a region without requiring its complete physical purification. Identification of the particular stromal cell type expressing a candidate can subsequently use histological methods.

The biological model is the already formed but hypocellular thymus of young IL7R-deficient mice. Its stromal cells are intrinsically competent, with recognizable cortical and medullary regions, and its growth as inducible by normal marrow-derived progenitors without marrow conditioning. Avoiding chemical or radiation conditioning reduces injury to the very stromal tissue under investigation.

This question is distinct from embryonic specification of the thymic primordium and initial divergence of cortical and medullary stromal lineages. The study concerns growth and maintenance after a nascent organ has formed. 

## 3. Objectives and specific aims

1. **Define dynamic stromal gene-expression changes during induced thymic growth.** Measure the cortical and medullary stromal response at baseline, growth initiation, log-phase growth, growth termination and the peak or approximate steady-state stage.
2. **Identify stromal receptor and lymphoid ligand pairs.** Determine which expressed stromal genes encode receptors and whether their potential ligands or counter-receptors are expressed in the corresponding lymphoid populations.
3. **Prioritize candidate lymphoid-to-stromal signals.** Integrate expression dynamics, literature and interaction evidence, thymus-specific novelty, signal intensity and the availability of experimental resources. Preserve individual scores as well as possible combined scores in a searchable database.
4. **Establish biological relevance for one or more high-priority signals.** Use suitable existing receptor or ligand mouse mutants to examine thymic size and composition and, where appropriate, the ability of mutant marrow to induce thymic growth.

The exploratory premise is that combining a physiological growth response with region-matched expression measurements can reveal candidate communication axes that are difficult to nominate from a single established signaling pathway. The final aim supplies proof of concept rather than a complete mechanistic account of every candidate.

## 4. Preliminary work and methodological basis

The laboratory has published the stromal expression-mapping method and the non-myeloablative transplantation model. Previous mapping recovered known stromal signals, including Notch ligands, IL7, kit ligand, Cxcl12 and MHC-associated genes, as well as genes implicated in stromal development or function. Recovery of these genes indicates that the computational separation can identify biologically meaningful stromal expression.

Figure 1 demonstrates microdissection and RNA recovery from a small thymic section. The tissue is an atrophic thymus from a 12-month-old mouse, only slightly larger than the thymus of a four-week-old IL7R-deficient recipient. This supports the feasibility of recovering RNA from small samples.


**Figure 1.** Laser microdissection of a 20 µm section from an atrophic thymus of a 12-month-old mouse. Cortex and medulla are separated, and the gel shows recovered RNA (left lane: medulla; right lane: cortex). About 10 ng RNA was obtained from the section.

Previous transplantation work demonstrated a strong growth response in the IL7R-deficient thymus, but its precise kinetics have not yet been determined for the proposed sampling design. Figure 2 is consequently a theoretical growth curve used to define sampling stages, rather than observed results. The Human Plasma Membrane Receptome group has also supplied its receptor-ligand database with mouse microarray identifiers to support batch matching. Existing computational scripts and the team's experience in gene-expression analysis provide the basis for subsequent prioritization.

## 5. Research design and methods

### 5.1 Establish the growth trajectory and collect matched samples

Aim 1 will use four-week-old male IL7R-deficient recipients and wild-type lineage-negative bone-marrow progenitors. The planned transplantation uses 2.5 million donor cells intravenously without myeloablative conditioning. A first kinetic series will measure thymus size at days 0, 4, 8, 12, 16, 20, 24, 28 and 32, using three recipients per time point. A second series is expected to examine one-day intervals around the onset of growth and on both sides of the peak so that the main expression experiment can target biologically meaningful phases.

The main study will examine five stages: untreated baseline, growth initiation, an intermediate log-growth point, approximately 36 hours before the peak, and the peak or approximate steady state. The pre-peak stage is intended to capture events preceding growth termination. The growth trajectory may overshoot, and age-related involution prevents the thymus from reaching a permanent steady state.


**Figure 2.** Theoretical kinetics of lymphoid-induced thymic growth and planned sampling phases. The timing will be determined experimentally; age-related involution continues during growth.

At each stage, one thymic lobe will provide microdissected cortex and medulla, while the other will provide the corresponding lymphoid populations. Cortical lymphocytes are defined by low or absent CD3, CD45 positivity and CD90 or CD117 positivity; medullary lymphocytes are defined by high CD3 and CD45 positivity. There will be three independently prepared microarrays for each of the four sample types: cortical tissue, cortical lymphocytes, medullary tissue and medullary lymphocytes. Each array represents RNA pooled from three mice. The tissue and lymphoid measurements therefore support region- and time-matched comparisons.

### 5.2 Estimate stromal expression and its dynamics

Affymetrix MOE430_2 arrays will measure expression. At each time point, the previously published statistical and computational procedure will compare the tissue with its corresponding purified lymphoid population to estimate the stromal contribution. Changes across time will then be evaluated between these estimated stromal profiles.

We will use the median calculated signal as the default threshold for classifying a stromal gene as expressed, with the 40th or 60th percentile available to alter stringency. A binary threshold can misclassify genes near the boundary. Array present/absent calls cannot directly separate the stromal and lymphoid components of a tissue sample. They will instead help flag probesets that are unlikely to be expressed anywhere in the tissue and those that may be stromal-specific. A probeset with fewer than two present calls among three tissue replicates will be flagged; two or more tissue present calls combined with fewer than two lymphoid present calls will flag a potentially stromal-specific signal.

Young IL7R-deficient mice retain some endogenous lymphoid cells. Their contribution will diminish as wild-type donor progenitors expand, changing the composition and expression signature of the lymphoid compartment. The analysis will not infer stromal changes by comparing lymphoid profiles across time. Each lymphoid profile is used to account for the lymphoid contribution to its own matched tissue sample. After stromal profiles are established, the lymphoid data can also be queried for the presence of candidate ligands at the corresponding stage.

### 5.3 Identify receptor-ligand candidates

Aim 2 combines two approaches. First, the Human Plasma Membrane Receptome resource supplies a curated set of more than 900 receptors and 600 paired ligands, with identifiers that can be mapped to the microarray data. The study will identify receptors expressed in stroma and check for their corresponding ligands in lymphoid cells.

Second, a complementary search will use Gene Ontology categories and published literature. Boolean combinations of receptor activity, receptor-complex and membrane or cell-surface categories, with exclusions and exceptions for intracellular localization, will generate a receptor list. A corresponding hierarchy will identify candidate ligands. Genes can function as both receptor and ligand or counter-receptor, so both lists will be considered when searching for lymphoid binding partners. Multiple probesets will be collapsed to EntrezGene identifiers using the Gene Set Enrichment Analysis tool.

Automated searches of PubMed and PubMed Central will measure co-citation of receptor and ligand symbols and aliases. Searches will include human and mouse homologs. Publications mentioning more than 20 genes will be excluded from this co-citation calculation to reduce contributions from broad genomic studies unrelated to a particular functional interaction.

The literature-based approach is expected to return a larger and noisier candidate set than the curated database. Both outputs will be merged into one sortable resource so their separate evidence remains available. Requiring a stromal receptor and a lymphoid partner to be expressed at a relevant stage is expected to reduce false-positive candidates. Candidate pairing remains a basis for prioritization and subsequent biological testing.

### 5.4 Prioritize candidates for biological testing

Aim 3 will attach several evidence fields to each candidate rather than rely on a single filter:

- Receptor-ligand co-citation frequency, used as an indicator of established biological relevance.
- Co-citation of either member with thymus, thymic or thymocyte terms. Frequent pairwise citation but limited thymus citation is intended to identify known interactions with potentially unrecognized thymic roles. One possible combined measure is the receptor-ligand score divided by the thymus score.
- Time-course ANOVA with multiple-testing-adjusted q values for stromal receptor expression, to prioritize dynamic changes.
- Signal-intensity decile, particularly for genes near the expression threshold.
- STRING evidence and confidence measures, including literature co-citation and experimental support from its integrated interaction resources.
- Links to existing mouse mutants, embryonic stem-cell resources, targeting vectors, antibodies and other reagents that could make testing feasible within the project.

Expression patterns will additionally be grouped using hierarchical clusters with k-means. Visual inspection will flag groups associated with specific phases, such as growth initiation.

Manual review of the strongest quantitative candidates will consider known roles in proliferation, differentiation or survival, especially in epithelial or mesenchymal cells, and the practical availability of mouse models. Individual measures can be used separately or in combination. The staged process is intended to make focused qualitative evaluation possible after reducing the large candidate set.

### 5.5 Establish initial biological relevance

Aim 4 will select a small number of high-priority interactions with suitable existing genetic models. Possible tests include viable germline mutants, or conditional mutants, affecting the lymphoid ligand or stromal receptor. For conditional models, we will use the laboratory's existing hCD2-Cre line for lymphoid ligands and Foxn1-Cre line for stromal epithelial receptors.

The main readouts are thymic size and cellular composition. Candidates responsible for growth support are expected to show a hypocellular thymus when disrupted. A lymphoid ligand model is particularly attractive in the proposed design because marrow from mutant donors can be tested for failure to induce the normal growth response in IL7R-deficient recipients. These tests will establish whether selected predicted interactions have biological relevance in the organ-growth setting.

Candidate identities and therefore the detailed set of validation experiments depend on Aims 1-3. More extensive studies distinguishing effects on stromal proliferation, differentiation and survival are outside this exploratory project's scope.

## 6. Implementation, resources and schedule

The laboratory provides tissue preparation, microscopy and standard molecular equipment. Shared facilities provide flow cytometry and sorting, laser microdissection, microarray processing and computational infrastructure. The laboratory has established access to these resources and experience with the core methods.

Petrie will supervise the biological and informatic work, train the postdoctoral researcher, oversee protocols and resources, and review raw data before publication. A full-time postdoctoral fellow will perform most experimental and computational work. Mohammad Fallahi, an informatic scientist with prior experience with the laboratory, will provide training and specialist assistance at a stated 10% effort. The personnel justification anticipates intensive data-processing work around months 7-12 and emphasizes training the postdoctoral fellow because the specialist does not have capacity to absorb that workload alone.

The research-strategy schedule allocates the first 6-8 months to establishing growth kinetics and obtaining matched tissue and cell samples, the next 6-8 months primarily to analysis and prioritization, and the remaining approximately 8-12 months to refining the results for publication and biological testing. 

The linked outputs are the expression time course, an annotated interaction database with links to supporting literature and resources, initial mouse-model findings and publications.

## 7. Ethics and data practices

The animal justification requests 420 mice for the primary work, kinetic studies and breeding. The main expression experiment uses 45 male recipients: five stages, three independently pooled samples per stage and three mice per pool. The budget includes 90 IL7R-deficient mice to obtain those males and 90 sex-matched wild-type donors. Each of two preliminary kinetic series is budgeted at 108 mice, and 24 animals are assigned to breeding. Where feasible, marrow from a donor can serve more than one recipient, reducing donor use.

Aim 4 requires additional animals whose number depends on the selected germline or conditional model and the affected compartment. Initial validation is expected to require fewer than 200 additional animals; an amendment will specify the number after candidate selection.

The justification for an animal model is the investigation of thymic function and organization as an intact organ. Veterinary oversight, anesthesia for procedures causing more than momentary distress, monitoring through recovery and at least daily for the subsequent week, and humane intervention or euthanasia for severe discomfort are described.

Data will be communicated through publications and presentations and made available by acceptance of the main findings, subject to institutional conditions. The institution reserves a possible additional 60-day delay for intellectual-property review. The specific presentation format and any necessary access restrictions will be determined when the data are generated. Unique mouse resources, if produced, will be shared under the applicable NIH resource-sharing requirements.

## 8. References

1. Griffith AV, Fallahi M, Nakase H, Gosink M, Young B, Petrie HT. Spatial mapping of thymic stromal microenvironments reveals unique features influencing T lymphoid differentiation. *Immunity*. 2009;31:999-1009.
2. Prockop SE, Petrie HT. Regulation of thymus size by competition for stromal niches among early T cell progenitors. *Journal of Immunology*. 2004;173:1604-1611.
3. Peschon JJ et al. Early lymphocyte expansion is severely impaired in interleukin 7 receptor-deficient mice. *Journal of Experimental Medicine*. 1994;180:1955-1960.
4. Ben-Shlomo I, Yu Hsu S, Rauch R, Kowalski HW, Hsueh AJ. Signaling receptome: a genomic and evolutionary perspective of plasma membrane receptors involved in signal transduction. *Science STKE*. 2003;2003:RE9.
5. Petrie HT, Zuniga-Pflucker JC. Zoned out: functional mapping of stromal signaling microenvironments in the thymus. *Annual Review of Immunology*. 2007;25:649-679.
6. van Ewijk W, Hollander G, Terhorst C, Wang B. Stepwise development of thymic microenvironments in vivo is regulated by thymocyte subsets. *Development*. 2000;127:1583-1591.
7. Mohtashami M, Zuniga-Pflucker JC. Three-dimensional architecture of the thymus is required to maintain delta-like expression necessary for inducing T cell development. *Journal of Immunology*. 2006;176:730-734.
8. Penit C, Lucas B, Vasseur F, Rieker T, Boyd RL. Thymic medulla epithelial cells acquire specific markers by post-mitotic maturation. *Developmental Immunology*. 1996;5:25-36.
9. Porritt HE, Gordon K, Petrie HT. Kinetics of steady-state differentiation and mapping of intrathymic-signaling environments by stem cell transplantation in nonirradiated mice. *Journal of Experimental Medicine*. 2003;198:957-962.
10. Crompton T, Outram SV, Buckland J, Owen MJ. Distinct roles of the interleukin-7 receptor alpha chain in fetal and adult thymocyte development revealed by analysis of interleukin-7 receptor alpha-deficient mice. *European Journal of Immunology*. 1998;28:1859-1866.
11. de Boer J et al. Transgenic mice with hematopoietic and lymphoid specific expression of Cre. *European Journal of Immunology*. 2003;33:314-325.
12. Gordon J et al. Specific expression of lacZ and cre recombinase in fetal thymic epithelial cells by multiplex gene targeting at the Foxn1 locus. *BMC Developmental Biology*. 2007;7:69.
