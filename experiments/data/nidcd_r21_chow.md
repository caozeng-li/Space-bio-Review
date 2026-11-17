# Neural Markers of Persistence and Recovery from Childhood Stuttering: An fMRI Study of Continuous Speech Production

Principal investigator: Ho Ming Chow. Mechanism: R21.

## 1. Project overview

The project will investigate brain activity during continuous speech in children with persistent stuttering (pCWS), children recovered from stuttering (rCWS), and children who do not stutter (CWNS). Its central hypothesis is that persistence is associated with anomalous activity in left-hemisphere speech-motor circuits, whereas recovery involves less anomalous left perisylvian activity and greater right-frontal involvement. The immediate purpose is to distinguish neural activity accompanying fluent speech from activity associated with individual stuttering instances, and to characterize recovery. The longer-term objective is to inform interventions..

## 2. Background and rationale

Developmental stuttering can affect social, emotional, academic and vocational development. Many children recover within a few years of onset, making childhood a useful period for studying both the disorder and compensatory or restorative processes. Previous childhood imaging has often used rest or simple speaking tasks that do not reliably elicit stuttering. Continuous speech is more relevant to the symptoms, but speech movements contaminate functional MRI.

Spatial independent component analysis (sICA) offers a way to separate movement- and speech-related artifacts from neuronal BOLD signals using their distinct spatial patterns. Six noise templates and three component-intensity measures—out-of-brain ratio, scattering degree and slice-wise variation—identify noise components. This approach offers whole-brain access, including subcortical speech-motor regions, without PET's radiation exposure. BOLD remains an indirect measure of neural activity.

The theoretical framework distinguishes a left frontotemporal pathway for mapping and monitoring speech, a right-frontal compensatory pathway, and motor execution involving supplementary motor area (SMA), basal ganglia and motor cortex. Recovery could involve normalization of left-hemisphere processing or an alternative right-hemisphere pathway.


**Figure 1.** Examples of noise and neuronal components in spatial independent component analysis.

## 3. Objectives and specific aims

**Aim 1: identify activity characterizing persistent stuttering.** Aim 1a compares fluent continuous speech between pCWS and CWNS, predicting differences in left posterior temporal and inferior frontal regions supporting speech monitoring. Aim 1b compares stuttering instances with fluent speech within pCWS, predicting differences in SMA, ventral premotor cortex and basal ganglia supporting motor planning and control.

**Aim 2: identify activity characterizing recovery.** Compare rCWS with pCWS to test for less anomalous left perisylvian activity, and compare rCWS with CWNS to test for greater right-frontal activity. This design characterizes current group differences; future longitudinal work would track the development of those differences.

## 4. Preliminary work and basis

Five preliminary studies support feasibility and the working hypotheses. Analysis of over 45,000 volumes from 281 resting-state scans in 50 CWS and 45 CWNS aged 3–13 showed that the strong relationship between movement and BOLD volume-to-volume variation diminished after sICA denoising. In these data, DVARS remained below approximately 1% for movements below 9 mm after denoising. A separate comparison in 18 adults found similar continuous-speech activity patterns with denoised fMRI and PET. An eight-year-old child completed the proposed speech paradigm and showed perisylvian activation for storytelling relative to overlearned speech.


**Figure 2.** Head movement and image artifacts before and after sICA denoising.


**Figure 3.** Continuous-speech activity measured with denoised fMRI and PET in adults.

In an adult continuous-speech study, 29 adults who stutter were compared with matched controls. The stuttering group showed reduced left perisylvian activity during fluent storytelling; stuttering instances were associated with increased putamen, SMA and ventral premotor activity. Adult findings motivate the childhood hypotheses but may not transfer directly. A childhood diffusion study found no corresponding reduction in left inferior-frontal white-matter coherence in the recovered group, with greater right inferior-frontal coherence. These structural findings support the functional recovery hypotheses.


**Figure 4.** Storytelling-related perisylvian activity in an eight-year-old child.


**Figure 5.** Speech-motor pathways and proposed mechanisms of persistent stuttering and recovery.


**Figure 6.** Reduced left perisylvian activity during fluent speech in adults who stutter.


**Figure 7.** Increased activity during stuttering instances in adults who stutter.


**Figure 8.** Inferior-frontal white-matter fractional anisotropy in persistent, control and recovered groups.

## 5. Research design and methods

### Participants and ascertainment

We will study 20 children per group, 60 total, aged 5–11, drawn from an existing longitudinal cohort with annual assessments. All will be monolingual English speakers without diagnosed developmental or psychiatric disorders other than stuttering.

Recovery and persistence will use serial Stuttering Severity Instrument, fourth edition (SSI-4), assessments and clinician/parent reports. Recovery is described as a decline from an intake SSI-4 score of 10 to below 10 at later assessments; persistence requires scores above 10 in years 3 and 4. CWNS must never have been diagnosed, have no family history or parental concern, and score below 6. Group assignment will be confirmed with new assessments. Treatment history will be recorded. The groups will be balanced on age and sex and, where possible, race, ethnicity and socioeconomic status.

Because scanner noise may reduce stuttering frequency, pCWS must show greater than 4.4% stuttering during quiet speech assessment. The proposed storytelling task is expected to produce about 300 syllables. Assuming a 25% reduction in scanner, this should provide more than ten stuttering instances. A child with fewer than ten in-scanner instances will be excluded from Aim 1b's event analysis, rather than automatically from every comparison.

### Imaging and speech tasks

A GE MR750 3T scanner with an eight-channel head coil will acquire EPI data with TR = 2 seconds, 3.45 × 3.45 × 4 mm voxels, 38 interleaved sagittal slices and acceleration factor 2. MPRAGE anatomical images will have 1 mm isotropic resolution. An optical noise-cancellation microphone and MR-compatible camera will record speech and visible movements. E-Prime will synchronize acquisition, recordings and stimuli. Headphones or earplugs will protect hearing.

Children will tell a story from the textless picture book *Frog, Where Are You?*, previously used successfully in the team's childhood cohort. Overlearned speech such as alphabet recitation will serve as a control condition. Each picture will appear for 15 seconds, followed by ten seconds of rest, with control pictures interspersed. The fMRI task will last less than 15 minutes. Children will be familiarized with the procedure before scanning.

### Speech coding

The speech team will transcribe recordings in CLAN/CHAT and mark precise onset and offset of part-word or single-syllable-word repetitions, prolongations and blocks. Acoustic events will locate repetitions/prolongations; visible articulatory movements will help locate blocks. Two independent speech-language pathologists will support reliable diagnosis and event identification. Transcript measures will include speech rate, utterance length, semantic diversity (for example TTR and VocD), and syntactic complexity (DSS and IPSYN).

### Imaging analyses and statistical comparisons

Preprocessing comprises slice-timing correction, movement correction, sICA denoising, spatial normalization and smoothing. In SPM12, a mixed block/event GLM will represent sustained speech using block regressors and stuttering instances using event regressors. Covariates will include the first two or three principal components of linguistic measures, severity, treatment type, IQ, socioeconomic status, age and sex. Analysts will be blinded to diagnoses. Permutation procedures will control multiple-comparison false positives.

Aim 1a uses between-group tests of fluent-speech estimates in pCWS and CWNS. Aim 1b uses paired contrasts of stuttered versus fluent speech within pCWS. Aim 2 uses between-group contrasts of rCWS versus pCWS and rCWS versus CWNS. The model explicitly separates transient stuttering effects from the fluent speech activity on which they occur.

Power calculations based on the adult preliminary study use a difference of 0.7 with SD 0.65 for the between-group comparison and 0.58 with SD 0.74 for the within-stuttering comparison. With alpha 0.05 and power 0.8, the strategy estimates 15 per group as sufficient, then increases planned recruitment to 20 to accommodate exclusion and potentially smaller recovery effects.

### Potential problems and alternatives

Childhood activity could differ substantially from the adult findings or from predictions based on structural imaging. If so, the functional data can revise the hypotheses rather than be treated as confirmation. Volumes with abrupt movements beyond the denoising method's working range, particularly above 9 mm, will additionally be removed using image scrubbing. If a child cannot complete the task correctly, the visit can be rescheduled with extra training. The existing participants' experience with scans and storytelling should assist compliance.

## 6. Implementation, resources and schedule

The study combines Chow's fMRI methods, Chang's longitudinal childhood cohort and Ratner's speech-analysis expertise. Required scanning, recording and stimulus equipment is available. Recruitment and imaging occupy months 1–18, with preparation of analyses in parallel. Speech and fMRI analysis and preliminary presentations occupy months 12–24. Manuscript preparation, submission and dissemination continue in months 24–36.

Future directions include functional connectivity during continuous speech, time-resolved analyses of transitions into stuttering, and longitudinal tracking from onset through persistence or recovery.

## 7. Ethics and data practices

Parents or guardians will provide informed consent and children verbal or written assent. MRI screening, hearing protection, breaks, familiarization and the option to stop without penalty will address discomfort and fatigue. Parents may remain nearby or with a distressed child. Audio/video and imaging data will use coded identifiers, locked or password-protected storage and controlled collaborator access. Data sharing after analysis and publication will be limited to de-identified relevant data under a formal data-use agreement, with release anticipated within one year after study closeout.

## 8. References

- Chang SE, Zhu DC. (2013). Neural network connectivity differences in children who stutter. *Brain*, 136, 3709–3726.
- Chang SE, et al. (2015). White matter neuroanatomical differences in young children who stutter. *Brain*, 138, 694–711.
- Xu Y, et al. (2014). Denoising the speaking brain: toward a robust technique for correcting artifact-contaminated fMRI data under severe motion. *NeuroImage*, 103, 33–47.
- AbdulSabur NY, et al. (2014). Neural correlates and network connectivity underlying narrative production and comprehension: a combined fMRI and PET study. *Cortex*, 57, 107–127.
- Tourville JA, Guenther FH. (2011). The DIVA model: A neural theory of speech acquisition and production. *Language and Cognitive Processes*, 26, 952–981.
- Petersen SE, Dubis JW. (2012). The mixed block/event-related design. *NeuroImage*, 62, 1177–1184.
