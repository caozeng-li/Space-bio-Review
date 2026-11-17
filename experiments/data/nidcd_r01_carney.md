# Developing and Testing Models of the Auditory System with and without Hearing Loss

Principal investigator: Laurel H. Carney. Mechanism: R01.

## 1. Project overview

This project will develop and test a mechanistic account of masked detection and spectral coding based on neural fluctuations. Computational models, human psychophysics and recordings from the inferior colliculus (IC) of awake rabbits will be combined to explain performance with normal hearing and sensorineural hearing loss (SNHL). The work concentrates on notched-noise masking, forward masking, and the steep spectral slopes in pinna cues and fricative consonants. A central objective is to produce models tailored to individual listeners rather than infer that all listeners with a similar audiogram use the same cues.

The proposed representation begins with instantaneous-rate fluctuations in auditory-nerve (AN) fibers. Peripheral filtering, saturation, synchrony capture and adaptation shape these fluctuations. Midbrain neurons tuned to amplitude modulation convert their contrasts across frequency channels into population responses. The project tests whether this representation can explain results that are difficult for energy-based power-spectrum and envelope-power-spectrum models, including detection when overall stimulus level is roved or equalized.

## 2. Background and rationale

Narrowband noise has strong low-frequency envelope fluctuations. Adding a tone can reduce those fluctuations, providing a detection cue even when energy is unreliable. Although a wideband stimulus has a relatively flat envelope, individual AN fibers receive a narrow effective band near their characteristic frequency. Consequently, peripheral responses to wideband sounds also contain fluctuations. These neural fluctuations are not simply the acoustic envelope: nonlinear peripheral processing can substantially change their magnitude and direction.

Many IC neurons are sensitive to modulations in approximately the 50–250 Hz range. Spectral edges and slopes can produce fluctuations in that range by restricting the effective bandwidth driving an AN fiber. A tone added within a notch can increase peripheral energy while reducing fluctuations and thus reducing the response of a modulation-sensitive IC neuron. This provides a different interpretation of masking thresholds from one based only on energy in peripheral filters.


**Figure 1.** Stimulus spectra, peripheral filters, effective response bandwidths, and filtered waveforms.

SNHL changes cochlear amplification, tuning, hair-cell transduction, saturation and synchrony capture. It therefore changes the fluctuation cues themselves, not merely sound audibility. The project will investigate whether listeners change from envelope-related to energy cues at frequencies with poorer hearing, and whether individual cue use predicts performance in more complex tasks. Spectral slopes are relevant to consonant identity and to pinna cues for sound elevation and externalization. Simply amplifying these frequency regions may not restore a neural representation altered by peripheral dysfunction.

## 3. Objectives and specific aims

1. **Identify cues used for detection in noise across frequency and build individualized models.** Test whether listeners transition from envelope to energy cues at frequencies where thresholds are elevated by inner- and/or outer-hair-cell dysfunction. Fixed- and roving-level tasks will characterize each listener, and related physiological recordings will test model predictions.
2. **Develop and test models for simultaneous notched-noise masking and forward masking.** Test whether neural fluctuations explain simultaneous masking thresholds and whether forward-masking results are better explained by this mechanism than by current temporal-processing accounts. Published datasets, new manipulations that distinguish model predictions, and IC recordings will be used together.
3. **Extend the account to spectral slopes in pinna cues and fricative consonants.** Test whether peripheral fluctuation profiles represent these slopes and whether interaural differences in fluctuations code elevation. This includes further development of a binaural model and comparisons with published psychophysics, new consonant-identification testing and neural recordings.

## 4. Preliminary work and basis

Prior modeling of reproducible-noise detection combined energy, fine-structure and envelope information and explained detailed diotic performance. Interaural envelope-slope differences outperformed conventional interaural time and level cues in detailed dichotic detection analyses. Older listeners with normal or mildly impaired hearing generally relied on envelope-related cues in the studied low-frequency tasks, whereas listeners with the greatest loss at 500 Hz shifted toward energy cues. These are observations motivating the proposed extension across frequency, not results already obtained for all of the present aims.

In a small pilot, four of five listeners showed frequency-dependent cue use, with envelope cues more useful in frequency regions with better hearing. One listener with a relatively flat audiogram was better predicted by energy cues throughout the tested range. This variability motivates direct characterization instead of assigning a cue from the audiogram alone.

Preliminary model and physiological responses show that adding a tone to notched noise can suppress an IC response even though acoustic energy increases. Model population differences are smaller after simulated outer-hair-cell impairment. A 200-Hz-wide noise target with the same energy as a tone produces a different fluctuation pattern, providing a candidate discriminating manipulation. Model responses to fricatives and pinna-related spectra also change with simulated impairment. Amplification does not necessarily recover the healthy population pattern.


**Figure 2.** Notched-noise stimuli and responses of peripheral filters, auditory nerve, modeled IC neurons and an IC neuron recorded in an awake rabbit.


**Figure 3.** IC population responses to notched noise with and without an added tone, in healthy and impaired models.


**Figure 4.** Model responses to two fricative consonants in healthy, impaired, and amplified-impaired conditions.


**Figure 5.** Pinna-cue spectrum and healthy and impaired IC model population responses.

## 5. Research design and methods

### Shared computational and experimental methods

The peripheral component will use the Zilany et al. (2014) AN model. The initial illustrations use high-spontaneous-rate fibers, but medium- and low-spontaneous-rate fibers will also be explored. AN responses feed monaural or binaural single-frequency inhibition–excitation models. IC components include band-enhanced, band-suppressed and hybrid modulation-transfer functions (MTFs). Inner- and outer-hair-cell parameters will be adjusted for individual listeners using audiometry and distortion-product otoacoustic emissions (DPOAEs).

Signal-detection theory will translate model sensitivity into predicted detection or discrimination thresholds. Response derivatives with respect to the stimulus parameter will be normalized by response variability and combined across a neural population. Analyses will consider Poisson variance or variance proportional to mean rate, independence or covariance among neurons, and combinations of MTF classes. Trends across stimulus parameters are central: a model whose trends cannot be reconciled with observed thresholds can be rejected even if a single threshold is matched.


**Figure 6.** Inferior-colliculus model circuitry and modulation-transfer-function pathways.

Physiological recordings will use tetrodes in awake Dutch-belted rabbits. Binaural sounds will be delivered through headphones and custom earmolds, with probe-tube calibration at the start of each session. Daily sessions last approximately two hours. Neurons will be characterized by frequency tuning, response-map type, MTF, best modulation frequency, and interaural time/level sensitivity. Neural thresholds derived from rates and/or temporal responses, including receiver-operating-characteristic analyses, will be compared directly with model and human thresholds. Rabbit behavioral experiments are not proposed.

Human testing will include 15 age-matched control listeners and 30 listeners with SNHL. The human-subjects section also describes a total recruitment of 40 listeners. Participants are aged 18–80. Control thresholds will be below 20 dB through 4 kHz. SNHL eligibility requires exceeding at least two of: 26 dB at 2 kHz, 30 dB at 3 kHz, or 35 dB at 4 kHz, with an air–bone gap below 10 dB. Thresholds must not exceed 60 dB HL from 250 to 8,000 Hz, and between-ear differences must not exceed 15 dB at a test frequency. Screening includes audiometry, bone conduction, tympanograms, DPOAEs and a speech-in-noise measure such as QuickSIN. Analyses emphasize individual patterns rather than pooling subjects into hearing-loss categories.

### Aim 1: frequency-dependent cue use

One-interval fixed- and roving-level masked-detection tracks will be interleaved. Two-down/one-up tracking will test the effect of a 20-dB overall-level rove. Target frequencies are 0.5, 1, 2, 4 and, when feasible, 6 kHz. The main methods specify third-octave Gaussian-noise maskers; the pilot-based description also discusses 1-ERB maskers. Levels will be 30 and 60 dB sensation level where feasible, with no presentation above 100 dB SPL.

The first six reversals will be discarded, thresholds estimated from the final ten, and each track repeated four times. A track will be repeated if the standard deviation of the final ten reversals exceeds 5 dB. The anticipated testing time for this characterization is no more than three hours. Similar fixed/roving thresholds support envelope-cue use; elevation in the roving condition supports reliance on energy. Audiograms, DPOAEs and behavioral cue estimates will jointly constrain each listener's model. Those models then predict Aim 2 and consonant results rather than being independently fitted to each task.

**Published notched-noise datasets for model testing.**

| Dataset | Contribution |
|---|---|
| Glasberg and Moore (1990) | Classical filter descriptions based on notched-noise masking thresholds |
| Baker and Rosen (2002, 2006) | Hearing and hearing-loss results across levels and frequencies |
| Lentz et al. (1999) | Roving-level notched-noise paradigm |
| Irino et al. (2013) | Asymmetric-notch and level paradigms used to refine filter estimates |

### Aim 2a: simultaneous notched-noise masking

Published paradigms will first be reconstructed, including Glasberg and Moore's notched-noise filter descriptions, Baker and Rosen's normal/impaired and level-dependent results, Lentz et al.'s roving-level study, and asymmetric-notch/level paradigms reviewed by Irino et al. AN and IC responses will be simulated and thresholds calculated from average or instantaneous rates. These simulations will identify informative parameter combinations for recording sessions.

Physiology will compare fixed, roving and equalized levels within neurons, using systematic notch-width, target-level and frequency manipulations. Psychophysics will focus on new conditions that distinguish neural-fluctuation and energy accounts rather than repeat the entire existing filter-estimation literature. Candidate manipulations include replacing a tone with an equal-energy narrowband noise, varying flanking-band slopes, and filling a notch with low-level sounds with controlled modulation. Low-noise noise is another possibility, although peripheral filtering can alter its phase structure and increase fluctuations.

Participants characterized in Aim 1 will undergo standard notched-noise testing at 0.5, 1, 2 and 4 kHz, six notch widths from Δ = 0 to 0.5, and 70 dB SPL. Modified tasks will be centered at 2 kHz. Individualized predictions will be compared with these measurements. Theoretical psychoacoustic analyses will accompany empirical and computational work.


**Figure 7.** Notched noise with an equal-energy narrowband-noise target and the corresponding IC population response.

### Aim 2b: forward masking

Model testing will cover published forward-masking results across level, frequency and delay; roving-level and additivity paradigms; high-level off-frequency maskers; and notched-noise forward masking. Key sources include Oxenham and Plack, Jesteadt and colleagues, Wojtczak and Oxenham, Oxenham and Shera, and Oxenham and Simonson. The aim is to examine whether different filter-bandwidth estimates from simultaneous and forward masking can be understood within one fluctuation-based account.

IC recordings will relate forward-masked responses to each neuron's modulation tuning. Selected conditions will include roving levels, high-SPL off-frequency maskers and notched-noise forward maskers. New psychophysical tests will be chosen from model and physiological results. For example, adding masker modulation is predicted to raise thresholds depending on its relationship to the fluctuations associated with the gap and probe. Listeners from Aim 1 will also perform a notched-noise forward-masking paradigm matched to Oxenham and Simonson (2006). The consequences for estimates of peripheral compression and tuning will be examined; those consequences are hypotheses to test.

### Aim 3: pinna cues and fricatives

The population model will be tested against published results for pinna-related spectral notches, including sensitivity to spectral detail and sound level. The project will examine whether high-level improvements observed with synthetic rectangular notches reflect their unusually steep slopes rather than generalize directly to natural pinna cues.

Recordings will use wideband noise convolved with human or rabbit head-related impulse responses. Simplified multipole IIR approximations will permit systematic manipulations relative to a neuron's best acoustic and modulation frequencies. Monaural recordings from both ears and dichotic recordings over a range of sound levels will test sensitivity to interaural fluctuation differences. The work lays a basis for subsequent studies of binaural pinna cues in SNHL.

Fricative stimuli will be presented to the model and physiological preparation. Predicted impairment effects will be compared with published consonant-confusion matrices and with consonant identification in listeners characterized in Aim 1, using stimuli and methods from Li et al. (2012) and Trevino et al. (2013), including the Fletcher AI corpus.


**Figure 8.** IC neuron responses to a synthetic pinna notch across sound levels.

### Alternatives and scope

If needed, the model can incorporate dorsal-cochlear-nucleus contributions to IC spectral-notch responses. Relevant IC cell types are uncommon in the awake-rabbit preparation, so that extension would primarily use modeling. Current emphasis is on the amount of fluctuation within channels; envelope phase relationships within and across channels are identified as a future direction. The project will update IC response nonlinearities as related modeling work develops.

## 6. Implementation, resources and schedule

The proposed program spans five years and links modeling, physiological experiments and psychophysics within each aim. The laboratory has sound-attenuated booths, auditory equipment, local computational facilities and access to university high-performance computing. Consultants contribute expertise in audiology, hearing-loss psychophysics, phonetics and theoretical psychoacoustics.

All human participants will complete Aim 1 characterization before Aim 2 and/or Aim 3. If new listeners are recruited for later tasks, they will also complete that initial characterization. Each study entails approximately 12–15 hours of testing per listener over 6–8 weeks. Four Dutch-belted rabbits are planned, with each used for approximately 12–24 months of physiological recording. The animal justification anticipates obtaining responses from approximately 200 neurons for each stimulus condition through sustained recordings, acknowledging the need to sample varied acoustic, modulation and binaural tuning.

## 7. Ethics and data practices

Human recruitment and written consent will use institutional review procedures. Sound-delivery safeguards will limit loudness exposure, participants will receive frequent breaks, and reports will use arbitrary identifiers.

Animal work will use veterinary supervision, anesthesia for surgery and procedures causing discomfort, postoperative analgesia, gradual habituation to the recording chair, and monitoring of health and behavior. Electrode locations may be verified histologically at the end of the study. The physiological task is passive listening.

New models and collected data will be shared through the laboratory website. Software will be supplied as MATLAB or C components with compiler instructions, readme files and executable examples. Associated publications will describe parameter determination and validation.

## 8. References

- Carney LH, Zilany MSA, Huang NJ, Abrams KS, Idrobo F. (2014). Sub-optimal use of neural information in a mammalian auditory system. *Journal of Neuroscience*, 34, 1306–1313.
- Baker RJ, Rosen S. (2002). Auditory filter nonlinearity in mild/moderate hearing impairment. *Journal of the Acoustical Society of America*, 111, 1330–1339.
- Baker RJ, Rosen S. (2006). Auditory filter nonlinearity across frequency using simultaneous notched-noise masking. *Journal of the Acoustical Society of America*, 119, 454–462.
- Davidson SA, Gilkey RH, Colburn HS, Carney LH. (2009). An evaluation of models for diotic and dichotic detection in reproducible noises. *Journal of the Acoustical Society of America*, 126, 1906–1925.
- Dau T, Püschel D, Kohlrausch A. (1996). A quantitative model of the effective signal processing in the auditory system. I. Model structure. *Journal of the Acoustical Society of America*, 99, 3615–3622.
