# Scientific Project Review

**Proposal:** Neural Markers of Persistence and Recovery from Childhood Stuttering: An fMRI Study of Continuous Speech Production  
**Principal Investigator:** Ho Ming Chow  
**Mechanism:** R21 (exploratory/developmental grant)  
**Review Date:** 2026-10-28  
**Source ID:** src-cd28e8763c0b

---

## Overall Assessment

This R21 proposal aims to investigate brain activity during continuous speech production in three groups of children aged 5–11: those with persistent stuttering (pCWS), those who have recovered from stuttering (rCWS), and children who do not stutter (CWNS). The project deploys a spatial independent component analysis (sICA) denoising method, already validated against PET by the investigators' group, to overcome the motion-artifact challenge that has historically limited fMRI studies of continuous overt speech. The central hypotheses—that persistence is associated with anomalous left-hemisphere speech-motor activity and that recovery involves normalization of left perisylvian activity with compensatory right-frontal engagement—are well grounded in a growing body of structural and adult functional findings.

The proposal is scientifically worthwhile. The question is important, the experimental approach is methodologically innovative for this population, and the team brings together the necessary expertise in fMRI methods, a longitudinal childhood stuttering cohort, and speech-language analysis. However, the project also faces several substantive challenges. The most significant is that the cross-sectional, group-comparison design cannot establish the developmental trajectories that the recovery hypotheses logically require. Relatedly, treatment history as a confound, the problem of distinguishing cause from consequence in neural differences, and limited statistical power for the more subtle recovery contrasts all constrain the inferences that can be drawn. These difficulties are inherent to the R21 mechanism and the state of the field rather than to carelessness in planning. The proposal nonetheless identifies a supportable scope of work: the within-subject comparison of stuttering instances versus fluent speech (Aim 1b) and the between-group comparisons on fluent speech (Aim 1a) are feasible and likely to yield interpretable data. The recovery aim (Aim 2) is the most important scientific contribution but the least well secured by the design.

On balance, a worthwhile direction is identifiable but substantive problems in the current plan affect its main goals. The proposal is not ready for support as written but contains elements that could be refined into a stronger plan.

**Overall recommendation: Current proposal insufficient for support (revise).**

---

## Scientific Value: B (Good) — Confidence: B (High)

The proposal addresses a question of genuine importance. Developmental stuttering affects roughly 5% of preschool-age children, with approximately 20% of cases persisting into adulthood (Bloodstein et al., 2021; Yairi & Ambrose, 2013). The psychosocial, academic, and vocational consequences of persistent stuttering are well documented. Understanding the neural correlates that distinguish persistence from recovery could, in the longer term, inform prognostic markers and intervention targets. The immediate knowledge contribution is descriptive and exploratory, consistent with the R21 mechanism: characterizing functional brain activity during ecologically valid continuous speech in children, distinguishing neural activity associated with fluent speech from that linked to individual stuttering events, and mapping group differences associated with recovery.

The proposal's use of continuous speech production—storytelling from the wordless picture book *Frog, Where Are You?*—is a distinct methodological strength. Prior childhood fMRI studies have largely relied on rest, single-word production, or simple speaking tasks that do not reliably elicit stuttering. As the proposal and external evidence confirm, "moments of stuttering are less likely to occur during simple speaking tasks" (Chow et al., 2023, NeuroImage: Clinical). The sICA denoising method, validated against PET (Xu et al., 2014, NeuroImage), addresses the central obstacle that has limited continuous-speech fMRI: speech-related motion artifacts. The preliminary work demonstrating that DVARS remained below approximately 1% for movements below 9 mm after sICA denoising in over 45,000 volumes from 281 pediatric resting-state scans provides credible evidence of technical feasibility in children.

A related published study by the same team (Chow et al., 2023, NeuroImage: Clinical) has already demonstrated the feasibility of this approach in 22 children with persistent stuttering and 18 controls, finding reduced left ventral premotor cortex activation during spontaneous speech in CWS. That published result—which the review treats as outcome information from a related but not identical project—confirms that the method works as proposed. The current proposal extends this paradigm to include the recovered group (rCWS), which is the novel element.

The attainable knowledge is twofold: first, a functional characterization of brain activity during fluent and stuttered continuous speech in children with persistent stuttering, including both between-group (pCWS vs. CWNS) and within-subject (stuttered vs. fluent) contrasts; second, a cross-sectional characterization of functional differences associated with recovery status. The first contribution is well supported by the methods; the second is more provisional. The proposal's explicit acknowledgment that "this design characterizes current group differences; future longitudinal work would track the development of those differences" is appropriately cautious. The value of the recovery characterization is therefore descriptive and hypothesis-generating rather than mechanistic or causal—appropriate for an R21 but limiting the contribution's weight.

The proposal frames its longer-term objective as informing interventions. While this is a legitimate aspirational goal, the pathway from cross-sectional group differences in BOLD activity to clinical intervention is indirect. The proposal does not overclaim this connection, however, and the descriptive contribution stands on its own.

---

## Innovation: B (Good) — Confidence: B (High)

The proposal makes several genuine, if incremental, contributions beyond prior work. The combination of elements—continuous speech fMRI with sICA denoising in young children, a three-group design including recovered children, and the explicit separation of fluent-speech activity from stuttering-event activity using a mixed block/event GLM—is novel as a package.

The most original element is the inclusion of the recovered group (rCWS) in a functional imaging study of continuous speech. As Neef and Chang (2024, PLoS Biology) note, "longitudinal studies that have collected neuroimaging data from children who stutter are rare, and even less common are those that have examined children who recover." The existing childhood recovery literature is predominantly structural (diffusion tensor imaging and morphometric studies from the Chang laboratory). The proposal explicitly builds on those structural findings—e.g., the observation that recovered children showed no corresponding reduction in left inferior-frontal white-matter coherence and greater right inferior-frontal coherence—to generate functional hypotheses. The functional characterization of recovery during continuous speech would add a genuinely new dimension.

The sICA denoising approach itself is not new (Xu et al., 2014), and the proposal's team has already applied it successfully to adults who stutter (Garnett et al., 2022, Frontiers in Human Neuroscience) and to children with persistent stuttering (Chow et al., 2023). The innovation here is the extension to the three-group childhood design with event-level analysis of stuttering instances, which has not been done before in children.

The theoretical framework—distinguishing a left frontotemporal pathway for speech mapping and monitoring, a right-frontal compensatory pathway, and motor execution involving SMA, basal ganglia, and motor cortex—is well established in the stuttering neuroimaging literature and is not itself novel. The proposal's contribution lies in testing specific predictions from this framework in children using a more ecologically valid task than has previously been employed.

The proposal appropriately notes that adult findings "may not transfer directly" to children, and Neef and Chang (2024) confirm that "in contrast to children, adults frequently exhibit heightened speech-related activity and connectivity within the right hemisphere cortical structures" and that "the absence of a similar rightward shift in children supports [the] compensatory hypothesis." The proposal's predictions thus reflect current understanding rather than simple extrapolation from adult data.

---

## Rigor: C (Moderate) — Confidence: B (High)

The proposal describes a generally well-specified technical pipeline: slice-timing correction, movement correction, sICA denoising, spatial normalization, smoothing, and a mixed block/event GLM in SPM12 with permutation-based multiple-comparison correction and blinded analysts. The speech coding protocol—transcription in CLAN/CHAT with independent coding by two SLPs, acoustic and video-based event localization, and quantitative linguistic measures—is appropriate and draws on established expertise (Ratner).

Several methodological choices strengthen the design. The mixed block/event design (Petersen & Dubis, 2012) appropriately separates sustained speech activity from transient stuttering events, which is essential because these two signals are confounded if not explicitly modeled. The inclusion of linguistic covariates (principal components of semantic diversity, syntactic complexity, speech rate, utterance length) is a thoughtful attempt to account for between-group differences in language production that could otherwise masquerade as neural differences. The blinding of analysts to diagnostic status and the use of permutation procedures for multiple-comparison correction are good practices.

However, several substantive concerns affect the strength of the intended inferences:

**1. Cross-sectional design for a developmental question.** Aim 2 asks what neural activity "characterizes recovery." A cross-sectional comparison of rCWS, pCWS, and CWNS can identify current group differences but cannot distinguish neural features that preceded and contributed to recovery from those that are consequences of recovered status or from pre-existing differences unrelated to stuttering trajectory. The proposal acknowledges this: "This design characterizes current group differences; future longitudinal work would track the development of those differences." This is honest, but it means Aim 2's contribution is more limited than the framing suggests. The proposal does not propose the longitudinal design that would be needed to answer the recovery question with developmental specificity.

**2. Treatment confound.** The proposal states that "treatment history will be recorded" but does not describe how it will be handled analytically. Children who recovered may have done so spontaneously or through treatment. If rCWS includes treated children, differences between rCWS and pCWS could reflect treatment effects rather than spontaneous recovery mechanisms. If treatment history is simply entered as a covariate, the analysis may be underpowered to disentangle its effects. The proposal does not address whether treated and untreated recovered children will be analyzed separately, nor how treatment-related neural changes—which are documented in adults (Neef & Chang, 2024)—will be distinguished from spontaneous recovery processes.

**3. Power considerations for Aim 2.** The power calculations are based on the adult preliminary study (between-group difference of 0.7, SD 0.65; within-stuttering difference of 0.58, SD 0.74). These effect sizes are from adult comparisons and may not apply to the recovery contrasts in children, which the proposal itself acknowledges may involve "potentially smaller recovery effects." With n = 20 per group, subtle but meaningful recovery-related differences may be missed. The published Chow et al. (2023) study with 22 CWS and 18 controls had sufficient power to detect group differences in left vPMC but noted that "the sample size of the current study could be relatively small for detecting subtler effects."

**4. Stuttering frequency in the scanner.** The proposal estimates a 25% reduction in stuttering frequency due to scanner noise and requires >4.4% stuttering during quiet speech to yield >10 in-scanner stuttering instances at 300 syllables. This is a reasonable calculation, but the exclusion of children with fewer than ten in-scanner instances from Aim 1b's event analysis introduces potential selection bias: children whose stuttering is most suppressed by the scanner environment may differ systematically from those whose stuttering persists. The proposal's decision not to exclude these children from all comparisons is sensible, but the implications for generalizability of Aim 1b should be acknowledged.

**5. Linguistic confounds.** Despite the inclusion of linguistic covariates, the proposal does not fully address the possibility that pCWS, rCWS, and CWNS may differ systematically in language production characteristics that are not captured by the chosen measures, or that covarying out language features may remove variance that is genuinely part of the stuttering phenotype. This is a known challenge in stuttering research and is not unique to this proposal, but it limits the interpretability of group differences.

**6. The 9 mm motion limit.** The sICA denoising method is validated for movements below approximately 9 mm. The proposal states that volumes with abrupt movements above this threshold will be scrubbed. In children aged 5–11, particularly those who may be anxious or uncomfortable in the scanner, movement exceeding 9 mm is a realistic concern. The published study (Chow et al., 2023) excluded participants with FD > 2 mm in more than 15% of volumes, and from 48 children who completed MRI, 7 were excluded for excessive movement—a non-trivial attrition rate. The proposal's fallback of rescheduling with extra training is reasonable but may not be sufficient for all children.

---

## Feasibility: B (Good) — Confidence: B (High)

The core technical components are feasible and have been demonstrated in related work. The sICA denoising method is validated (Xu et al., 2014). The speech paradigm—storytelling from *Frog, Where Are You?*—has been used successfully in the team's childhood cohort. An eight-year-old child has completed the paradigm and shown expected perisylvian activation. The scanner, recording equipment, and stimulus presentation system are available. The three-investigator team combines complementary expertise: Chow (fMRI methods, sICA denoising), Chang (longitudinal childhood stuttering cohort), and Ratner (speech-language analysis).

The participant recruitment pathway is one of the proposal's strengths. Drawing from an existing longitudinal cohort with annual assessments means that the persistence and recovery status of potential participants is already characterized, reducing the uncertainty of prospective recruitment. The cohort also provides children with prior experience in research settings, which "should assist compliance."

Several feasibility concerns warrant attention:

**1. Recruitment of the recovered group.** The proposal aims for 20 rCWS. Recovery is defined as a decline from an intake SSI-4 score ≥10 to below 10 at later assessments. The existing cohort from which participants will be drawn must contain at least 20 children meeting this criterion who are also willing to undergo fMRI at ages 5–11. The proposal does not provide the number of recovered children currently available in the cohort. If the recovered group cannot be fully recruited, Aim 2—the most novel contribution—cannot be executed as planned.

**2. In-scanner stuttering for Aim 1b.** The event analysis requires at least ten stuttering instances per child. If scanner noise reduces stuttering more than the anticipated 25%, or if some pCWS children produce fewer syllables than expected, the number of children contributing to Aim 1b may fall below the power calculation threshold. The proposal acknowledges this risk and handles it by excluding low-event children only from Aim 1b, not from all analyses. This is a reasonable mitigation.

**3. Timeline and scope.** The 36-month timeline (recruitment and imaging months 1–18, analysis and presentations months 12–24, manuscript preparation months 24–36) is realistic for an R21. The overlap between recruitment and analysis preparation is sensible.

**4. Data sharing.** Data release is anticipated within one year after study closeout, limited to de-identified data under a formal data-use agreement. This is adequate but not exceptional; the restrictions may limit secondary analyses by independent groups.

---

## Ethics and Safety: B (Good) — Confidence: B (High)

The proposal describes appropriate ethical safeguards. Informed consent from parents or guardians with verbal or written assent from children aligns with standard practice for pediatric research. MRI screening, hearing protection, breaks, familiarization, and the option to stop without penalty address the known discomforts of scanning in young children. The provision that parents may remain nearby or with a distressed child is appropriate. Audio/video and imaging data will use coded identifiers with locked or password-protected storage and controlled collaborator access.

The use of a 3T MRI scanner in children aged 5–11 raises no additional safety concerns beyond those standard to pediatric neuroimaging, provided standard screening for contraindications is performed. The radiation-free method (fMRI rather than PET) is appropriate for this population.

Several considerations are unspecified but do not rise to the level of concerns:

- The proposal does not mention whether Institutional Review Board approval has been obtained or is pending. For an R21 application at the review stage, this is not unusual; the proposal describes the ethical framework without documenting regulatory status.
- No mention is made of a Data Safety and Monitoring Board or equivalent, which would not ordinarily be required for a minimal-risk observational neuroimaging study.
- The proposal does not discuss how incidental findings on structural MRI will be handled. This is a standard consideration in neuroimaging research and its absence is a minor gap.

The data-sharing plan is adequate: de-identified data under a formal data-use agreement, with release within one year of study closeout. The coded-identifier approach and controlled access are proportional to the sensitivity of the data.

---

## Management: C (Moderate) — Confidence: C (Moderate)

The management structure is described at a high level appropriate to an R21 application, but several details that would support confidence in execution are absent.

The team combines the necessary expertise: Chow contributes the fMRI acquisition and analysis methods, including the sICA denoising pipeline; Chang provides access to and knowledge of the longitudinal childhood cohort; Ratner leads the speech-language analysis, including transcription reliability and linguistic measures. These are complementary and non-redundant roles.

The schedule—recruitment and imaging in months 1–18, parallel analysis preparation, speech and fMRI analysis and preliminary presentations in months 12–24, and manuscript preparation in months 24–36—is realistic. The 18-month recruitment window for 60 children (approximately 3–4 children per month) appears achievable given the existing cohort.

The proposal does not include:
- A detailed budget or itemized resource allocation, which is not required for this review format.
- CVs or explicit documentation of investigator time commitment. The review notes that "an absent CV, itemized budget or approval document alone is not evidence of incapacity, inadequate funding or unethical conduct" per the review instructions, but the absence limits confidence in management.
- A clear description of decision-making authority, conflict resolution, or contingency planning for recruitment shortfalls—particularly for the recovered group.
- A data management plan beyond the ethics section's description of storage and access.

The available equipment and cohort access are documented. The investigator team is productive in this area, as evidenced by the published Chow et al. (2023) study and related work. The management plan is adequate for the R21 mechanism but provides limited detail for independent assessment.

---

## Summary of Ratings

| Criterion | Rating | Confidence |
|-----------|--------|------------|
| Scientific Value | B (Good) | B (High) |
| Innovation | B (Good) | B (High) |
| Rigor | C (Moderate) | B (High) |
| Feasibility | B (Good) | B (High) |
| Ethics and Safety | B (Good) | B (High) |
| Management | C (Moderate) | C (Moderate) |

---

## Recommendation: Current proposal insufficient for support (revise)

The proposal addresses a worthwhile scientific question with a methodologically innovative approach that has been credibly demonstrated in related work. The three-group design including recovered children would, if successfully executed, add genuinely new functional evidence to a field that has relied primarily on structural imaging for childhood recovery studies. The team, equipment, and cohort are appropriate.

However, the cross-sectional design cannot support the developmental recovery inferences that constitute the proposal's most novel contribution (Aim 2). The treatment-history confound is acknowledged but not adequately managed. Power for the recovery contrasts may be insufficient. These issues do not make the project unsupportable in principle, but they limit what the current plan can deliver relative to its stated goals.

The proposal would be strengthened by: (1) clarifying the analytical treatment of treatment history and, if possible, stratifying or restricting the recovered group by treatment status; (2) providing cohort data on the number of eligible recovered children to substantiate recruitment feasibility; (3) articulating more precisely the limited inferences that the cross-sectional design can support for Aim 2, and considering whether a longitudinal component (even with a subset) could be incorporated; and (4) addressing how between-group differences in language production that are intrinsic to stuttering will be distinguished from confounds.

The within-subject stuttering-event analysis (Aim 1b) and the between-group fluent-speech comparison (Aim 1a) are the strongest elements of the current design and could form the core of a revised plan with a more modest recovery component—or a plan that defers the recovery question to a future longitudinal study while focusing the current R21 on characterizing persistent stuttering during continuous speech.