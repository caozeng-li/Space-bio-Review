# Project review

## Overall assessment

This project develops and tests a mechanistic account of masked detection and spectral coding based on neural fluctuations in the auditory system. The planned work combines a computational model of the auditory periphery and inferior colliculus with human psychophysics and awake-rabbit electrophysiology to explain performance in listeners with normal hearing and sensorineural hearing loss (SNHL). The three aims span: (1) characterizing individual listeners' cue use across frequency and building individualized predictive models; (2) testing whether neural-fluctuation cues from the midbrain account for simultaneous notched-noise masking and forward masking; and (3) extending the account to spectral slopes in pinna cues and fricative consonants. The most valuable expected knowledge is a physiologically grounded alternative to energy-based masking models that can account for individual differences in hearing impairment and unify simultaneous and forward masking within a single framework.

The project builds on a substantial body of published work from the laboratory demonstrating that neural-fluctuation cues in model IC neurons can account for notched-noise masking thresholds with and without level roving (Maxwell et al., 2020), that older listeners with hearing loss transition from envelope to energy cues (Mao et al., 2015; Leong et al., 2020), and that the awake-rabbit IC preparation yields the relevant neuron classes with full characterization. The integrated design combining individualized psychophysical characterization, cross-task model prediction, and physiological recordings is a genuine strength.

The main limitations affecting the judgement are: the shift from a two-interval to a one-interval roving-level procedure without calibration or pilot data; the absence of formally specified model-fitting procedures, risking overfitting; the lack of a reliability estimate for individual rove-effect measurements that are central to listener classification; and the underspecified nature of the disambiguating conditions for Aim 2a. The forward-masking and pinna-cue components are at an earlier stage of development. These limitations reduce the current plan's inferential force without negating its scientific promise.

The project's value lies in its potential to unify masking phenomena under a single mechanistic account and to predict individual performance from physiological models—a step beyond current group-level descriptions. The cross-task prediction design is its strongest feature. The principal limitations are not conceptual but design-specific: the uncalibrated shift to a one-interval procedure, the unspecified model-fitting protocol, and the absence of reliability estimates for individual cue classification all reduce the confidence with which the primary inferences can be drawn. These are remediable gaps that affect the current plan's ability to deliver definitive tests. The integration of computation, physiology and psychophysics remains a considerable strength, and the proposed extensions to forward masking and individualized prediction are well motivated and timely.

## Ratings and review outcome

Rating A-E: excellent, good, moderate, weak, very weak. Confidence A-E describes confidence in the assessment, not project success.

| Dimension | Rating | Confidence |
| --- | --- | --- |
| Scientific value | B | A |
| Innovation | B | B |
| Rigor | C | B |
| Feasibility | B | B |
| Ethics and safety | B | B |
| Management | B | B |

**Overall conclusion: Conditional support**

## Scientific value

**The question is important and the attainable descriptive and mechanistic knowledge is substantial, though constrained in part by the proposal's own scope.**

The project addresses a fundamental challenge in auditory neuroscience: what cues support detection of sounds in noise, and how does sensorineural hearing loss (SNHL) alter those cues? The dominant framework—the energy-based power-spectrum model—has shaped the interpretation of psychoacoustic masking and the design of hearing-aid amplification for decades. The proposal offers a coherent alternative grounded in peripheral physiology. It posits that instantaneous-rate fluctuations in auditory-nerve (AN) fibers, shaped by cochlear filtering, inner-hair-cell saturation, synaptic adaptation and synchrony capture, are transformed into average-rate changes in the population of modulation-tuned inferior colliculus (IC) neurons, and that those rate changes—not an increase in energy within a single peripheral filter—provide the detection cue [1].

**The practical implication is direct and clinically relevant.** Because SNHL degrades the peripheral nonlinearities that generate these fluctuations, restoring audibility through amplification may not recover the information the system needs [1]. If the fluctuation account is correct, hearing-aid strategies should target restoration of fluctuation cues rather than audibility alone. This is a testable claim with consequences for device design and fitting.

**The proposal rests on a substantial published foundation.** Maxwell, Richards and Carney (2020) showed that a single multi-best-modulation-frequency IC model accounted for human notched-noise masking thresholds in both fixed- and roving-level conditions without fitting parameters to the threshold data, while AN average-rate cues were insufficient even in the fixed-level task [2]. Leong et al. (2020) demonstrated that normal-hearing listeners showed small rove effects (\~1 dB) across 0.5–4 kHz whereas listeners with SNHL showed larger effects, particularly at frequencies where thresholds were most elevated [3]. The pilot data that four of five listeners showed frequency-dependent cue use while one flat-audiogram listener was better predicted by energy cues throughout [1] underscores the variability this project aims to capture, and which group-level analyses cannot resolve.

**The attainable knowledge is organised across three aims of differing maturity.**

**Aim 1** will produce a detailed characterisation of cue use across frequency in individual listeners and computationally individualized models capturing that behaviour. The descriptive contribution—mapping how hearing loss alters cue reliance across the audiometric range—fills a gap left by earlier work focused on single frequencies. The cross-task structure, whereby models constrained by Aim 1 data predict Aim 2 and Aim 3 performance without further fitting [1], provides the strongest inferential leverage in the proposal.

**Aim 2a and 2b** together promise a mechanistic account that could unify simultaneous and forward masking within one fluctuation-based framework. This is a significant conceptual advance because estimates of filter bandwidth currently differ systematically between simultaneous- and forward-masking paradigms, a discrepancy typically attributed to cochlear suppression or methodological assumptions rather than to shared mechanisms [4] [5]. Brennan et al. (2023) have already shown that model-based masked-threshold estimates incorporating inherent envelope fluctuations, with medial olivocochlear efferent feedback, better captured forward-masking disruption than a model without those mechanisms [6]; the proposal extends this line of investigation systematically to notched-noise forward masking and to comparisons with established datasets.

**Aim 3** tests whether the same fluctuation profiles can represent the steep spectral slopes in pinna cues and fricative consonants, with the specific hypothesis that high-level performance improvements observed with synthetic rectangular notches may not generalise to natural pinna features [1]. This aim is more exploratory, with the binaural model extension described as laying a basis for subsequent studies [1], and the consonant-identification component offers a clinically relevant but less thoroughly specified test.

**The attainable knowledge has two structural limitations.** The proposal acknowledges that the model currently emphasises fluctuation magnitude within channels, with cross-channel envelope phase relationships deferred as a future direction [1]. Maxwell et al. reported that listener performance in wideband roving-level conditions likely involved cross-channel comparisons that the within-channel model did not capture [2]. This limits the model's completeness for tasks demanding across-frequency integration. Second, the awake-rabbit IC recordings provide mechanistic correlates rather than causal tests linking specific neural populations to perceptual decisions—the proposal appropriately treats physiology as a test bed for model predictions, but the link to human psychophysics remains correlational [1]. Neither limitation negates the project's contribution; they define its epistemic boundaries.

**If successful**, the project would deliver a physiologically grounded framework that accounts for individual differences across multiple masking and spectral-coding tasks from a single set of listener-specific peripheral parameters. This would be a meaningful step beyond both stimulus-based envelope models and atheoretical group-level descriptions of hearing impairment.

## Innovation

**The project offers genuine advances in its extension of an established framework to new domains, its commitment to individualised prediction, and its unifying ambition, though the core concept is not new. The originality is more incremental than foundational, concentrated in the forward-masking and cross-task prediction elements.**

The neural-fluctuation account itself has been the laboratory's central research programme for over a decade. Mao and Carney (2014) demonstrated that a physiological model including AN and IC stages predicted listener variance in tone-in-noise detection comparably to stimulus-based envelope models [7]. Maxwell, Richards and Carney (2020) then showed that a single multi-BMF IC model accounted for notched-noise masking thresholds with and without level roving, and for profile analysis—tasks that had required fundamentally different explanations under the power-spectrum model [2]. The proposal does not claim to invent the fluctuation account; it claims to extend it in three directions whose originality varies in kind and degree.

**Aim 1: individualised cross-task prediction.** The plan to characterise individual listeners' cue use across frequency and build models tailored to each listener's audiogram and DPOAE profile is an important but incremental step beyond Leong et al. (2020), who demonstrated group-level frequency-dependent rove effects in SNHL listeners but did not construct individual predictive models [3]. The methodologically most original feature is the cross-task structure: using Aim 1 characterisation to constrain models that then predict Aim 2 and 3 performance without further fitting [1]. If successful, this would provide evidence that a single physiologically grounded model can capture individual differences across multiple tasks. Competing frameworks such as FADE (Kollmeier et al., 2016) also individualise predictions using audiograms and level uncertainty, but within a modulation-filterbank rather than a physiological neural-fluctuation architecture [8]. The Zaar and Carney (2022) model already individualises speech-intelligibility predictions using audiogram-based IHC/OHC parameters [9]; extending this individualisation to basic detection and demonstrating cross-task prediction would be a meaningful advance.

**Aim 2b: forward masking as the most original component.** Published neural-fluctuation work has concentrated on simultaneous masking. Forward masking has been explained through temporal-window models or peripheral adaptation and recovery, with discrepancies between simultaneous- and forward-masking filter-bandwidth estimates typically attributed to cochlear suppression [4] or methodological assumptions [5] rather than to shared mechanisms. The proposal's idea—that inherent envelope fluctuations in the masker persist through the silent gap and contribute to forward masking, and that one fluctuation-based account may unify what are currently separate explanations—is novel and testable. Brennan et al. (2023) provided initial evidence that model-based forward-masking predictions incorporating envelope fluctuations and MOC efferent feedback better captured listener data than a model without those mechanisms [6]. The proposal extends this systematically to notched-noise forward masking and to comparisons with published datasets from Oxenham and Plack, Jesteadt and colleagues, Wojtczak and Oxenham, Oxenham and Shera, and Oxenham and Simonson [1].

**Aim 2a: consolidation and physiological grounding.** The simultaneous masking component builds directly on Maxwell et al. (2020). The proposed new psychophysical manipulations—replacing tones with equal-energy narrowband noise, varying flanking-band slopes, and filling notches with controlled-modulation sounds [1]—are logically sound tests of the fluctuation account, but the concept of using such comparisons is itself a standard disambiguation strategy. The addition of awake-rabbit IC recordings comparing fixed, roving and equalised levels within individual neurons adds a physiological dimension absent from the published computational work and represents a modest methodological extension.

**Aim 3: exploratory extension to spectral slopes.** The application to pinna cues and fricative consonants is conceptually well motivated but at an earlier stage of development. The examination of whether high-level improvements with synthetic rectangular notches reflect their unusually steep slopes rather than generalising to natural pinna features [1] is a specific, addressable question that distinguishes this work from conventional pinna-cue research. The binaural model extension for interaural fluctuation differences is described as laying a basis for subsequent studies [1], which positions this aim as exploratory rather than a decisive test. The originality here lies primarily in the application of the fluctuation framework to a new stimulus domain rather than in a novel theoretical claim.

**Overall**, the project's innovation resides not in a single breakthrough but in the systematic extension of a credible framework to new masking phenomena and to individualised prediction—a contribution pattern that, if realised, would shift the field toward physiologically grounded accounts of individual differences in hearing.

## Rigor

**The integrated modeling-psychophysics-physiology design provides a strong inferential framework, but several material design choices weaken the plan's capacity to deliver definitive tests. The central strengths are clear; the gaps are specific and remediable.**

### Inferential strengths of the design

The proposal's core logic is sound and well-motivated: individual listener models are built from a simple cue-diagnostic task in Aim 1 and then used to predict performance in more complex masking and spectral-coding tasks in Aims 2 and 3 without further fitting [1], with awake-rabbit IC recordings providing mechanistic grounding at the level of identified MTF-defined neuron types [1]. Convergent evidence from all three arms—computational, physiological, psychophysical—is genuinely sought within each aim, and the use of published datasets alongside new data is efficient.

The signal-detection-theory framework for translating model sensitivity into predicted thresholds is rigorous. Maxwell et al. (2020) operationalised this using Mahalanobis distance across the model population with explicit sensitivity analyses for Poisson versus proportional variance, independence versus covariance, and combinations of MTF classes, and the proposal states that "trends across stimulus parameters are central: a model whose trends cannot be reconciled with observed thresholds can be rejected even if a single threshold is matched" [1]. This emphasis on parameter trends rather than absolute threshold matching is a scientific strength that protects against over-interpretation of any single fitted value.

### Uncalibrated procedural change in the cornerstone measure

The roving-level paradigm is the cornerstone of Aim 1 and the individual-model building step. The group's published work (Leong et al., 2020) used a two-interval, two-alternative forced-choice procedure in which the rove was applied independently to both intervals, preserving within-trial level comparison [3]. The proposal adopts a one-interval, two-down/one-up adaptive track with interleaved fixed and roving segments [1]. This change is consequential. In a two-interval task, the listener compares two stimuli that may differ in level under roving, preserving the within-trial level comparison that energy-based strategies exploit. In a one-interval task, the listener must judge signal presence using an internal criterion against a single presentation, which should make level (energy) cues even less reliable, potentially inflating the rove effect.

The proposal neither discusses this procedural change nor presents pilot data using the one-interval procedure. Without calibration against the published two-interval data, the interpretation of an individual listener's rove effect as indicating envelope-reliant or energy-reliant cue use rests on an unvalidated assumption about what the one-interval rove effect magnitude means. A rove effect of, say, 3 dB in a one-interval procedure may reflect different cue weighting than the same numerical value in a two-interval procedure.

### Underspecified model-fitting and risk of overfitting

The AN model (Zilany et al., 2014) contains parameters controlling OHC gain, IHC transduction, and synaptic adaptation. The proposal states that IHC and OHC parameters will be adjusted using audiograms, DPOAEs, and Aim 1 behavioural cue estimates [1]. The crucial safeguard is cross-task prediction without further fitting [1], but the proposal does not specify a formal fitting procedure—no cost function, parameter bounds, or cross-validation protocol within Aim 1 is described.

This introduces appreciable risk that informal fitting involves implicit researcher degrees of freedom that overfit the Aim 1 data and weaken the predictive test. The Zaar and Carney (2022) model used "standard assumptions" for partitioning hearing loss between IHC and OHC impairment without specifying the decision rule [9], and Keshishzadeh et al. (2021) emphasised the difficulty of distinguishing damage subtypes from noninvasive markers alone [10]. Specifying the number of free parameters, the fitting algorithm, and the criteria for an acceptable fit is essential for the cross-task prediction to carry its intended inferential force.

### Uncertain measurement reliability for individual cue classification

The fixed/roving characterisation requires up to three hours per listener across five frequencies and two levels, with four repeated tracks per condition and a criterion of SD ≤ 5 dB for acceptable tracks [1]. No power analysis or reliability estimate is provided for individual rove-effect measurements. Leong et al. (2020) reported group-level differences with 12 listeners per group [3], but the proposal's individual-level classification demands higher precision. With the stated tracking procedure—six reversals discarded, thresholds estimated from the final ten reversals, tracks repeated four times [1]—measurement error in individual rove-effect estimates could be several dB, limiting the ability to detect graded cue-use patterns rather than categorical shifts. A formal analysis of expected individual-level reliability, based on the pilot data, would clarify what the design can resolve.

### Underspecified disambiguating conditions

The candidate manipulations for Aim 2a—tone versus equal-energy narrowband-noise targets, varying flanking-band slopes, filling notches with controlled-modulation sounds [1]—are logically derived from the fluctuation framework. The narrowband-noise manipulation is a particularly strong test because energy-based and fluctuation-based accounts make divergent predictions: an energy account predicts similar thresholds for tone and equal-energy noise targets (same energy added to the filter), whereas the fluctuation account predicts different thresholds (different effects on envelope fluctuations).

However, the proposal states that these conditions will be finalised based on modelling results [1], which introduces flexibility that could allow selection of conditions favouring the fluctuation account. Committing to a core set of disambiguating conditions a priori, even if additional conditions are explored, would strengthen the inferential force of this aim.

### Summary

These limitations do not invalidate the approach. The cross-task prediction structure, the rigorous SDT framework, the theoretically motivated candidate manipulations, and the established physiology preparation provide a solid foundation. Each gap is remediable, but together they reduce the confidence with which the current plan can deliver definitive tests of its central hypotheses. The core underlying issue is that several features of the design that would give strong support to the proposed claims—the one-interval procedure, the model-fitting protocol, the disambiguation conditions—are currently underspecified relative to their importance for the conclusions the project seeks to draw.

## Feasibility

**The computational pipeline, physiology preparation, and human testing scale are each substantially demonstrated, making the core programme feasible; the principal remaining risks are scientific rather than technical, and the proposal recognises them as questions to investigate.**

### Computational pipeline

The computational backbone—the Zilany et al. (2014) AN model feeding a population of modulation-tuned IC neurons—has already been implemented, validated, and shown to account for human notched-noise masking thresholds with and without level roving. Maxwell, Richards and Carney (2020) demonstrated that the multi-BMF IC model produced thresholds close to human data (within \~3 dB on average) and captured the key phenomenon of resistance to roving level, without parameters fitted to the threshold data [2]. The model's capacity to generate divergent predictions for energy-matched stimuli with different fluctuation properties is illustrated in the preliminary data: adding a tone to notched noise can suppress the modelled IC response even as acoustic energy increases, and a 200-Hz-wide noise target with the same energy as a tone produces a different fluctuation pattern [1].

The project's central computational task is therefore extension, not construction. The main outstanding computational challenges are expanding the model to forward masking—where adaptation and recovery dynamics must be tracked rather than steady-state fluctuation patterns—and incorporating cross-channel phase information. Both challenges are identified as directions to develop rather than presuppose [1]. The Zilany et al. model already includes synaptic adaptation whose 2014 update was noted to affect forward-masking properties [11], providing a starting point from which to build. Whether the fluctuation framework can accommodate forward-masking results without essentially becoming a temporal-processing model itself is a genuine open question, but the proposal appropriately treats this as a test rather than an assumption.

The proposal indicates that medium- and low-spontaneous-rate fibers "will also be explored" [1], a sensible next step given that the published modelling was limited to high-SR fibers. Maxwell et al. noted that LSR fibers with wide dynamic ranges are mainly observed above \~1500 Hz and would not improve roving-level coding using energy-based metrics [2]; expanding the fiber-type representation may therefore be more consequential for the hearing-loss modelling than for the normal-hearing case.

### Awake-rabbit IC physiology

The preparation is well-established in this laboratory. Fan, Henry and Carney (2022) reported 136 isolated single units from 4 awake Dutch-belted rabbits with full characterisation including MTF shape, binaural sensitivity, and noise delay functions. Among these, the relevant MTF classes were well represented: 29.4% band-enhanced, 45.6% band-suppressed, and 8.8% hybrid. Fan et al. (2021) obtained measurable tone-in-noise detection thresholds from 150 of 163 units at one or more noise levels [12]. The proposal's target of approximately 200 neurons per stimulus condition, drawn from 4 rabbits recorded over 12–24 months each [1], is consistent with this demonstrated yield.

The proposal acknowledges that IC cell types responsive to spectral notches—particularly those receiving dorsal cochlear nucleus input—are uncommon in the awake rabbit [1]. This limits the physiological grounding for Aim 3 to the more prevalent MTF types and makes that aim more dependent on the computational model. The proposal's fallback to modelling-only exploration of DCN contributions is a reasonable acknowledgment of this limitation but weakens the triangulation of model-physiology-psychophysics for the pinna-cue and fricative predictions.

### Human psychophysics

The testing programme specifies 15 age-matched control listeners and 30 listeners with SNHL [1], 12–15 hours of testing per listener over 6–8 weeks [1], with Aim 1 characterisation occupying no more than three hours [1]. These targets are realistic for a five-year single-site study. The SNHL inclusion criteria are clearly defined (exceeding at least two of: 26 dB at 2 kHz, 30 dB at 3 kHz, or 35 dB at 4 kHz, with air-bone gap below 10 dB, thresholds not exceeding 60 dB HL from 250 to 8,000 Hz, between-ear differences ≤15 dB) [1]. The main practical risk is participant attrition given the multi-session design, but the six-to-eight-week window per listener provides scheduling flexibility.

### Resources and timeline

The laboratory possesses the necessary sound-attenuated booths, auditory equipment, local computational facilities, and university high-performance computing access [1], supplemented by consultants in audiology, hearing-loss psychophysics, phonetics and theoretical psychoacoustics [1]. The five-year timeline linking modelling, physiology and psychophysics within each aim is appropriate given that each aim depends on the same individualised listener models derived in Aim 1 [1]. The principal scheduling risk is that the physiological arm's yield may extend beyond planned windows if certain stimulus-responsive cell types prove scarcer than anticipated, but the sustained recording plan of 12–24 months per animal provides buffer. Overall, the implementation is well-founded in the laboratory's published methods and track record; the scientific risks are recognised and proportionate.

## Ethics and safety

**The ethical and safety arrangements are appropriate and proportionate for the proposed human psychophysics and awake-animal electrophysiology.**

### Human participants

Recruitment and written consent will follow institutional review procedures [1]. Sound-delivery safeguards will limit loudness exposure, participants will receive frequent breaks, and data will be reported using arbitrary identifiers [1]. The testing programme involves moderate listening levels (up to 100 dB SPL ceiling, with 30 and 60 dB sensation level as standard conditions) and five frequencies with interleaved adaptive tracks [1]; the specified three-hour characterisation session with breaks is a reasonable accommodation. The participation burden of 12–15 hours over 6–8 weeks [1] is substantial but not excessive for a voluntary research study.

The SNHL inclusion criteria are clearly bounded (thresholds not exceeding 60 dB HL, between-ear differences ≤15 dB, air-bone gap \<10 dB) [1]. This ensures that participants have sufficient residual hearing to perform the tasks and that middle-ear pathology is excluded—both criteria that protect participants from futile testing and discomfort.

### Animal subjects

Four Dutch-belted rabbits will be used, each for approximately 12–24 months of daily two-hour recording sessions [1]. Surgical procedures use anaesthesia and veterinary supervision, with postoperative analgesia provided [1]. Animals undergo gradual habituation to the recording chair, and health and behaviour are monitored throughout [1]. The recording task is passive listening [1]; animals are not required to perform behavioural responses to obtain reward or avoid punishment, which eliminates the motivational and stress confounds present in operant behavioural paradigms. Electrode locations may be verified histologically at the end of the study [1], a terminal procedure that provides anatomical confirmation of recording sites and is standard in chronic electrophysiology.

The sustained recording plan (200 neurons per condition from 4 animals over multiple months) is scientifically efficient because it maximises data yield per animal, reducing the total number of subjects relative to acute preparations. The welfare arrangements reflect an appropriate balance between the scientific requirement for awake-animal data—necessary because anaesthesia alters IC response properties, modulation tuning, and efferent feedback that are central to the neural-fluctuation hypothesis—and the mitigation of discomfort through habituation, limited session duration, and veterinary oversight.

### Data and environmental considerations

The project does not involve hazardous materials, protected populations beyond the hearing-impaired participant group, or environmental risks beyond routine laboratory operations. Data sharing through the laboratory website with documented software (MATLAB or C components, compiler instructions, readme files, executable examples) [1] supports reproducibility without raising identifiable privacy concerns given the use of arbitrary participant identifiers [1].

## Management

**The management structure is coherent and draws on demonstrated expertise and infrastructure; the main gaps are the absence of specified model-fitting protocols and quantitative success criteria, which create avoidable coordination risk given the project's dependence on cross-aim prediction.**

### Coordination and workflow

The five-year programme links computational modelling, awake-rabbit IC physiology and human psychophysics within each aim, with Aim 1 characterisation serving as the foundation for individualised model construction that then drives predictions in Aims 2 and 3 [1]. This sequential dependency is explicitly managed: all human participants complete Aim 1 characterisation before proceeding to later aims, and any newly recruited listeners for subsequent tasks also undergo the same initial assessment [1]. This structure ensures that the individualised models are built before they are tested, avoiding the circularity that would arise if fitting and prediction were intermixed.

Each listener's testing burden of approximately 12–15 hours over 6–8 weeks is allocated across the five-year timeline, with the three-hour Aim 1 characterisation providing the entry point [1]. The physiological arm runs in parallel, with four rabbits each recorded for 12–24 months, targeting approximately 200 neurons per stimulus condition [1].

### Expertise and infrastructure

The principal investigator has led the development of the neural-fluctuation framework over more than a decade, with published demonstrations of each methodological component: the Zilany et al. (2014) AN model, the multi-BMF IC model (Maxwell, Richards and Carney, 2020 [2]), the awake-rabbit preparation (Fan et al., 2021, 2022), human roving-level psychophysics (Leong et al., 2020 [3]; Mao et al., 2015 [13]), and individualised hearing-loss modelling (Zaar and Carney, 2022 [9]). External consultants contribute audiology, hearing-loss psychophysics, phonetics and theoretical psychoacoustics expertise [1], covering domains that complement the laboratory's core strengths. The available facilities—sound-attenuated booths, auditory equipment, local computational resources and university high-performance computing access [1]—match the technical requirements.

### Coordination risks from underspecified elements

Two under-specified elements of the research plan create coordination risk. First, the individualised model construction—the linchpin of the cross-aim prediction structure—lacks a specified fitting protocol, cost function or cross-validation procedure. Without agreed criteria for when a model is adequately fit to Aim 1 data, the transition from Aim 1 to Aims 2 and 3 involves an ungoverned decision point. The project would benefit from pre-specified quantitative criteria for acceptable model fit and from a protocol for handling listeners whose models cannot be adequately individualised—both are management decisions affecting workflow across aims.

Second, the disambiguating conditions for Aim 2a are described as candidates to be finalised based on modelling results [1], which introduces a scheduling dependency: the physiological and psychophysical arms for Aim 2a cannot commence their definitive tests until the modelling identifies the most informative conditions. Pre-committing to a core set of conditions would de-risk this dependency and allow parallel rather than strictly sequential progress.

### Data sharing and scheduling

The data-sharing plan—models and collected data distributed through the laboratory website with documented software, compiler instructions and executable examples [1]—is a responsible practice that supports reproducibility and secondary use. Published parameter-determination and validation descriptions will accompany the releases.

The scheduling risk that the physiological arm may extend beyond planned windows if neuron yield for less common stimulus-responsive cell types is lower than anticipated is partially mitigated by the sustained recording plan of 12–24 months per animal [1]. The laboratory's published yield—136 isolated single units from 4 rabbits—provides a realistic benchmark. Overall, the management arrangements are sufficient for delivery, with the identified gaps representing avoidable coordination risks rather than structural weaknesses.

## References

[1] [nidcd\_r01\_carney](<D:/master/SpaceBioReview/demo_11/experiments/StaticResearchCommittee/runs/nidcd_r01_carney/attempt-001/native/sources/src-0a1b88acd036/original.md>) (Application material)

[2] [Neural fluctuation cues for simultaneous notched-noise masking and profile-analysis tasks: Insights from model midbrain responses.](<https://europepmc.org/article/MED/32486827>). Maxwell BN, Richards VM, Carney LH; 2020-05-01 (External source)

[3] [Sensorineural Hearing Loss Diminishes Use of Temporal Envelope Cues: Evidence From Roving-Level Tone-in-Noise Detection.](<https://europepmc.org/article/MED/31985535>). Leong UC, Schwarz DM, Henry KS, et al.; 2020-07-01 (External source)

[4] [Auditory filter shapes derived from forward and simultaneous masking at low frequencies: Implications for human cochlear tuning](<https://www.semanticscholar.org/paper/657b207ab4a56d86cc5b28ee0d5ba940cc9d158d>). John Leschke, Gerardo Orellana, C. Shera, et al.; 2022-03-01 (External source)

[5] [On the controversy about the sharpness of human cochlear tuning.](<https://europepmc.org/article/MED/23690279>). Lopez-Poveda EA, Eustaquio-Martin A; 2013-05-21 (External source)

[6] [Inherent envelope fluctuations in forward masking: Effects of age and hearing loss.](<https://europepmc.org/article/MED/37092921>). Brennan MA, Svec A, Farhadi A, et al.; 2023-04-01 (External source)

[7] [Tone-in-noise detection using envelope cues: comparison of signal-processing-based and physiological models.](<https://europepmc.org/article/MED/25266265>). Mao J, Carney LH; 2014-09-30 (External source)

[8] [Sentence Recognition Prediction for Hearing-impaired Listeners in Stationary and Fluctuation Noise With FADE: Empowering the Attenuation and Distortion Concept by Plomp With a Quantitative Processing Model.](<https://europepmc.org/article/MED/27604782>). Kollmeier B, Schädler MR, Warzybok A, et al.; 2016-09-07 (External source)

[9] [Predicting speech intelligibility in hearing-impaired listeners using a physiologically inspired auditory model.](<https://europepmc.org/article/MED/35750575>). Zaar J, Carney LH; 2022-06-09 (External source)

[10] [Towards Personalized Auditory Models: Predicting Individual Sensorineural Hearing-Loss Profiles From Recorded Human Auditory Physiology.](<https://europepmc.org/article/MED/33526004>). Keshishzadeh S, Garrett M, Verhulst S; 2021-01-01 (External source)

[11] [Updated parameters and expanded simulation options for a model of the auditory periphery.](<https://europepmc.org/article/MED/24437768>). Zilany MS, Bruce IC, Carney LH; 2014-01-01 (External source)

[12] [Responses to diotic tone-in-noise stimuli in the inferior colliculus: stimulus envelope and neural fluctuation cues.](<https://europepmc.org/article/MED/34391193>). Fan L, Henry KS, Carney LH; 2021-12-31 (External source)

[13] [Cues for Diotic and Dichotic Detection of a 500-Hz Tone in Noise Vary with Hearing Loss.](<https://europepmc.org/article/MED/25976088>). Mao J, Koch KJ, Doherty KA, et al.; 2015-05-15 (External source)
