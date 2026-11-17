# Predicting and Addressing Colonoscopy Non-adherence in Community Settings (PRECISE)

**Principal investigator:** Gloria D. Coronado.  
**Application mechanism:** R01.

## 1. Project overview

PRECISE will test whether telephone patient navigation improves completion of follow-up colonoscopy among safety-net patients who are unlikely to complete it without additional help. The study combines external validation of an existing prediction model with a pragmatic patient-randomized trial at Sea Mar Community Health Centers in Washington State. We will evaluate effectiveness, costs, cost-effectiveness, secondary clinical/process outcomes, and variation in effect according to predicted adherence.

Phase I will validate the model, classify patients' probability of follow-up, and adapt navigation to local resources. Phase II will compare navigation with usual care among patients with moderate or low predicted adherence, while observing the highest-adherence group separately. The detailed randomization and power plan specifies 924 randomized patients, 462 per arm, in 28 participating clinics. Enrollment is planned over 18 months.

## 2. Background and rationale

A positive fecal immunochemical test (FIT) requires diagnostic follow-up, yet many patients do not obtain colonoscopy. Previous studies report follow-up rates of roughly 42–59% in Veterans Administration settings and 81–82% in integrated systems, with substantially lower rates in community health centers than optimal. In STOP CRC, only 54% completed colonoscopy within one year after a positive fecal test, despite 87% being referred. Delays can undermine the benefit of screening.

Navigation can address practical and informational barriers, including bowel preparation, transport and an escort, time away from work, insurance, cost, fear, and difficulty contacting patients. Prior navigation programs increased colonoscopy use, but many control patients completed it without navigation. The central question is therefore not simply whether navigation works on average, but whether scarce navigation resources can be directed effectively to the patients most likely to forgo follow-up.

The Chronic Care Model guides the study. Its six domains connect decision support, clinical information systems, delivery design, health-system organization, self-management, and community resources. PRECISE emphasizes prediction-based decision support and clinical information systems alongside navigation embedded in ordinary clinic and community workflows.


The proposed innovations are targeting follow-up rather than initial screening, testing effects within predicted-adherence strata, measuring value for money across these strata, and using trained clinic staff rather than a parallel research-only service. Results should clarify whether and how health systems can target navigation sustainably.

## 3. Objectives and specific aims

1. **Externally validate and implement the risk score.** Use Sea Mar data, stratify patients by predicted colonoscopy adherence, and adapt navigation materials to the local setting.
2. **Assess effectiveness, costs, and cost-effectiveness.** Compare a centralized telephone navigation program with usual care for people at moderate or high risk of nonadherence—that is, moderate or low probability of adherence.
3. **Assess secondary outcomes and moderators.** Examine time to colonoscopy, missed/cancelled appointments, colonoscopy quality, intervention dose, and possible effect modification by predicted probability and patient characteristics.


## 4. Preliminary work and basis

STOP CRC involved 26 clinics in eight community health centers and approximately 50,000 patients. It used EHR tools to identify people overdue for screening and mail FIT kits. Chart abstraction of 613 FIT-positive patients found 87% referral and 54% one-year colonoscopy completion. Among referred non-completers, recorded reasons included declining (28%), inability to contact (11%), and a recent colonoscopy (12.8%); 44% had no recorded reason. Interviews with providers, referral coordinators, and patients identified preparation, financial, transport, scheduling, and fear-related barriers.

A Cox prediction model developed from 1,122 FIT-positive patients with one-year follow-up uses eight variables: age, Hispanic ethnicity, body mass index, prior-year clinic visits, missed appointments, previous CRC screening, prior-year influenza vaccination, and clinic site. The bootstrap-corrected c-statistic was 0.65. Predicted-adherence quintiles separated six-month colonoscopy probabilities from approximately 25% in the lowest quintile to 93% in the highest. Calibration and similar results at nine months support further external validation rather than assuming the development model transfers unchanged.

**Table 1. EHR-documented reasons for no referral or colonoscopy in STOP CRC**

| Reason | Not referred, n=84: n (%) | Referred without colonoscopy, n=195: n (%) |
| --- | --- | --- |
| No reason indicated | 26 (32.1) | 86 (44.1) |
| Patient declined | 24 (29.6) | 54 (27.7) |
| Unable to contact | 4 (5.0) | 21 (10.8) |
| Recent colonoscopy | 22 (25.9) | 25 (12.8) |
| Other | 5 (5.9) | 9 (7.7) |

Based on chart abstraction of 613 patients with positive FIT results.

**Table 2. Follow-up barriers reported by providers and patients**

| Barrier | Providers, n=15 | Patients, n=10 |
| --- | --- | --- |
| Bowel preparation | Yes |  |
| Arranging a ride | Yes | Yes |
| Time off work | Yes | Yes |
| Appointment delays |  | Yes |
| Billing/insurance | Yes |  |
| Colonoscopy cost | Yes | Yes |
| Patient fears | Yes |  |
| Confusion about colonoscopy | Yes | Yes |
| Multiple health issues |  | Yes |


Sea Mar operates 34 medical clinics, of which 28 nonspecialty clinics will participate. It serves approximately 250,000 patients, with about 29,000 screening-eligible patients; 37% are Latino. Its integrated EHR and existing FIT outreach support the proposed workflow. Planning data estimate 1,186 positive FIT results annually, with a 9% positivity rate; approximately 949 would fall in the moderate/low adherence strata before final eligibility exclusions.

**Table 3. Sea Mar colorectal screening, 1 July 2016–1 June 2017**

| Clinics | Eligible patients | FIT completed in prior year | Positive FIT results | High adherence probability | Moderate/low probability |
| --- | --- | --- | --- | --- | --- |
| 28 | 29,281 | 45.0% | 1,186 | 237 | 949 |

Eligibility: age 50–74. Planning assumptions: 9% FIT positivity; adherence strata 20% high, 40% moderate, and 40% low.

## 5. Research design and methods

### 5.1 Phase I: validation and risk classification

External validation will use a distinct historical Sea Mar cohort with positive FIT results more than one year earlier. The subsequent trial will use a prospective FIT-positive cohort. The specified eligibility is age 50–75 with positive FIT requiring colonoscopy, applying prior screening and clinical exclusions consistent with STOP CRC: recent colonoscopy, recent fecal testing in the screening eligibility process, colon disease, or shortened life expectancy. The randomization age categories later specified are 50–64 and 65–74.

The development Cox model's original linear predictor will generate probabilities. Missing predictors will be addressed using multiple imputation by chained equations. Discrimination will be assessed using the c-statistic, with a decline of at least 0.05 regarded as inferior separation. Explained variation will use an R-squared measure based on the D-statistic. Calibration plots will compare predicted probabilities with Kaplan–Meier observed probabilities in quintiles. If needed, predictions will be updated using Sea Mar's baseline colonoscopy rate or predictor coefficients recalibrated in its cohort.

A secure interactive database will populate de-identified EHR data, assign study IDs, and supply the eight predictors. The lowest two probability quintiles define low adherence, the middle two moderate adherence, and the highest quintile high adherence. The high-adherence group will undergo surveillance; the other groups will enter randomization. Clinic/patient representatives, gastroenterology providers, researchers, payers, and policymakers will participate in a quarterly advisory board.

### 5.2 Navigation adaptation, delivery, and training

The New Hampshire Colorectal Cancer Screening Program materials will be adapted using local GI referral arrangements, preparation practices, transport, insurance, and low-cost colonoscopy resources. Unlike a program that directly provides free colonoscopies, PRECISE will use existing clinical/community coverage. Documentation will be integrated into the EHR to reduce duplicate entry.

The six-topic contact sequence covers agreement and barriers; bowel preparation and transport; remaining preparation difficulties; final appointment/preparation checks; postprocedure support; and understanding results and the next screening interval. Contacts occur around scheduling and then approximately 5–7 days, 1–2 days, and one day before colonoscopy, on or after the procedure day, and 2–4 weeks afterward. Video calls may be used where available and preferred.

**Table 4. Patient-navigation contacts**

| Call | Purpose | Timing relative to colonoscopy |
| --- | --- | --- |
| 1 | Obtain agreement, confirm scheduling, establish rapport and assess barriers | At scheduling |
| 2 | Review preparation and obtaining supplies, address barriers, confirm transport | 5–7 days before |
| 3 | Review preparation and remaining challenges | 1–2 days before |
| 4 | Confirm appointment and preparation progress; answer questions | 1 day before |
| 5 | Evaluate experience and offer postprocedure support | Day of/after |
| 6 | Confirm receipt and understanding of results and recommended rescreening | 2–4 weeks after |

Navigators will be nurse practitioners or staff with a similar clinical role and medical-record access. Training includes two didactic days followed by eight weeks of coaching. Bilingual expertise will support delivery, and competency and fidelity checklists will be used. Logs will record community referrals, barriers, and cancelled/rescheduled appointments. Meetings are planned weekly for the first three months and then every two weeks, with quarterly specialist consultation. The planning estimate is 126 minutes per navigated patient, approximately 970 hours for 462 patients.

### 5.3 Randomization and comparator

The statistician will randomize within predicted-adherence level, age group, and sex. The detailed allocation assigns 231 low- and 231 moderate-adherence patients to each arm, totaling 462 per arm; the high-adherence surveillance group is separate. Staff other than the statistician will be blinded to predicted probability, but assignment to navigation is not blinded. Usual care consists of the existing notification/referral process and associated follow-up. If navigation is effective, it will subsequently be offered to usual-care and surveillance patients who have not completed colonoscopy.

**Table 5. Proposed assignment by probability of follow-up**

| Assignment | Low | Moderate | High |
| --- | --- | --- | --- |
| Navigation | 231 | 231 |  |
| Usual care | 231 | 231 |  |
| Surveillance |  |  | 231 |

### 5.4 Outcome ascertainment and fidelity

The primary outcome is chart-confirmed colonoscopy within one year after positive FIT. EHR codes will identify procedures, followed by chart audits for patients without coded completion. Prior work found that codes retrieved about 80% of colonoscopy reports. The auditor will be blinded to assignment and record reasons for non-completion or procedure date, pathology availability, and findings. Missing reports, missed appointments, preparation quality, completeness, and result communication will also be requested from GI facilities.

For intervention fidelity, 25% of telephone contacts will be recorded during the first three months and a new navigator's first three months, with patient verbal consent. Thereafter, 5% will be recorded. Recordings and EHR documentation will be reviewed for timing and content, with feedback and booster training. A 10% sample of usual-care encounters will assess changes and possible contamination. Dose is the proportion of the six calls or content areas completed. Qualitative interviews will also assess receipt and experience of contacts.

**Table 6. Measures and assessment schedule**

| Measure | Data source | Operational measure | CCM domain | Time |
| --- | --- | --- | --- | --- |
| Colonoscopy within one year | EHR; GI charts as needed | Number completing / number FIT-positive | Delivery-system redesign | Baseline; follow-up |
| Cost and cost-effectiveness | EHR; navigator logs | Time, training/salary costs, cost per follow-up and number needed to treat | Delivery-system redesign | Baseline; follow-up |
| Time to colonoscopy | EHR | Days | Delivery-system redesign | Baseline; follow-up |
| Time to cancer treatment | EHR | Days | Delivery-system redesign | Baseline; follow-up |
| Missed appointments/no-shows | GI records | Missed/no-show appointments / scheduled colonoscopies | Delivery-system redesign | Follow-up |
| Cancelled GI appointments | GI records | Cancellations within 24 hours / scheduled colonoscopies | Delivery-system redesign | Follow-up |
| Colonoscopy quality | EHR | Adequate preparation / colonoscopies; percentage reaching cecum | Delivery-system redesign | Baseline; follow-up |
| Adenomas, cancer and stage | EHR pathology | Adenomas or cancers / colonoscopies; cancer stage | Delivery-system redesign | Baseline; follow-up |
| Risk strata and patient characteristics | EHR | Frequencies: age, sex, insurance, language, screening history, comorbidities | — | Baseline; follow-up |
| Navigator training | NHCRSCP guide | Competencies completed | Self-management support | Ongoing |
| Contacts completed | EHR | Calls / anticipated calls | Self-management support | Ongoing |
| Content coverage | EHR; navigator logs | Content areas / six | Self-management support | Ongoing |
| Reaction, barriers and unintended consequences | Interview data | Debriefs with facilitators/navigators and one-to-one patient interviews | Delivery-system redesign | Follow-up |

### 5.5 Primary analysis and power

All primary analyses will follow intention to treat. Multilevel logistic regression will use study arm as a fixed effect and clinic as a random effect. An odds ratio above one supports greater completion with navigation. Quarterly secure EHR transfers will supply study data.

The power plan assumes 44% one-year completion under usual care in the moderate/low adherence population. Starting with 1,186 annual positive FIT results, it allows 3.5% not needing colonoscopy and selects the approximately 80% with lower predicted adherence, yielding 924 randomized patients. Average clinic cluster size is 33 across 28 clinics. At two-sided alpha 0.05 and 80% power, the detectable improvement is 12.9 percentage points with ICC 0.03 (56.9% completion; OR 1.68), or 9.2 points without a design effect (53.2%; OR 1.45).

### 5.6 Economic evaluation

The health-plan perspective will cover implementation, maintenance, and intervention delivery, together with medical care related to detection, colonoscopy, and rescreening. Cancer treatment costs are excluded. EHR events will be costed using Medicare fee schedules; staff logs, interviews, budgets, and national wage data will inform delivery costs. Research costs will be separated from replication costs, and sensitivity analyses will emphasize costs expected in routine implementation.

The one-year horizon requires no discounting. Incremental cost-effectiveness will be expressed per additional colonoscopy, adenoma detected, and cancer detected. Net-benefit regression and acceptability curves will assess cost-effectiveness over willingness-to-pay thresholds and by moderate/low adherence stratum. Cost distributions and censoring will guide two-part models, bootstrapping, and inverse-probability methods. Budget-impact scenarios will vary population size, fixed and variable costs, and possible offsets from fewer inadequate preparations, repeated procedures, or missed appointments. Numbers needed to navigate will be estimated by probability stratum.

### 5.7 Secondary outcomes, moderators, and qualitative work

Shared-frailty Cox models will compare time to colonoscopy and cancer treatment initiation while accounting for clinic clustering. Multilevel logistic models will assess binary process and quality outcomes such as missed appointments and preparation adequacy. Approximately 59 new cancers are anticipated in the overall positive-test population; treatment-timing comparisons may consequently be underpowered.

**Table 7. Baseline secondary-outcome estimates**

| Outcome | Estimate |
| --- | --- |
| Median time to colonoscopy | 62 days |
| Missed/cancelled appointments | 15.6% |
| Adequate bowel preparation | 66–87.5% |
| Advanced adenomas | 31.0% |
| Cancer | 5.0% |

Time and cancer outcomes are based on STOP CRC; appointment and upper preparation estimates on NHCRSCP; the lower preparation estimate on Naylor et al. (2017).

The main moderation test adds probability stratum and its interaction with trial arm to the primary model; a parallel analysis uses continuous risk score. The stated detectable interaction odds ratios at 80% power are 2.98 (or 0.34) with the design effect and 2.17 (or 0.46) without it. Separate exploratory interactions will examine age, sex, language, comorbidity, insurance, income, prior screening, and intervention dose, emphasizing effect magnitude where precision is limited.

Approximately 60 patient interviews will purposively sample both probability groups and people who did or did not complete colonoscopy. English/Spanish interviews will explore understanding, preparation, support, barriers, program experience, and unintended effects. Another 25–30 interviews among about 20 navigators, clinic staff, and GI staff/providers will examine adaptation and sustainability. Transcripts will be coded in Atlas.ti using content analysis, the Chronic Care Model, and emergent themes. Disagreements will be resolved by consensus; the advisory board will help interpret de-identified findings.

## 6. Implementation, resources, and schedule

Coronado will lead in partnership with Sea Mar's medical director Ricardo Jimenez. The team includes navigation and implementation expertise, gastroenterologists, prediction and economic analysts, qualitative researchers, and health-information support. Sea Mar commits staff, training, tracking, and a clinical champion.

The five-year schedule progresses from regulatory approval, model validation, and materials adaptation to randomization and navigation, follow-up and chart abstraction, qualitative work, analyses, and dissemination. Long-term plans embed the prediction model in EHR workflows and produce a model specification guide and decision-support tools. The study acknowledges changing real-world clinic processes, limited precision for some moderators, and dependence on supportive local leadership.

**Study timeline**

| Activity | Planned period |
| --- | --- |
| IRB approval | Months 1–3 |
| Advisory board | Regular meetings throughout Years 1–5 |
| Risk-model validation | Months 4–7 |
| Navigation-material adaptation | Months 5–9 |
| Baseline data | Months 8–12 |
| Randomization | Months 12–14 |
| Staff training | Months 14–17 |
| Intervention | Months 15–34 |
| Qualitative interviews | Months 34–40 |
| Follow-up EMR data | Months 36–46 |
| Chart abstraction | Months 39–53 |
| Analysis | Months 47–60 |
| Manuscripts | Months 49–60 |
| Dissemination | Months 51–60 |

## 7. Ethics and data practices

The schedule includes IRB approval. Study IDs and de-identified predictor data support risk classification, with secure quarterly transfers and controlled clinical-record access. Patients will give verbal consent for recorded navigation calls; advisory-board review uses de-identified interview data. Navigation helps patients access existing care and does not replace clinically indicated colonoscopy referral or treatment.

## 8. References

- Rice K, Gressard L, DeGroff A, et al. Increasing colonoscopy screening in disparate populations: Results from an evaluation of patient navigation in the New Hampshire Colorectal Cancer Screening Program. *Cancer*. 2017.
- Raich PC, Whitley EM, Thorland W, Valverde P, Fairclough D. Patient navigation improves cancer diagnostic resolution: an individually randomized clinical trial in an underserved population. *Cancer Epidemiol Biomarkers Prev*. 2012;21:1629–1638.
- Meester RG, Zauber AG, Doubeni CA, et al. Consequences of Increasing Time to Colonoscopy Examination After Positive Result From Fecal Colorectal Cancer Screening Test. *Clin Gastroenterol Hepatol*. 2016;14:1445–1451.e8.
- Green BB, Anderson ML, Wang CY, et al. Results of Nurse Navigator Follow-up After Positive Colorectal Cancer Screening Test: A Randomized Trial. *J Am Board Fam Med*. 2014;27:789–795.
- Liss DT, Brown T, Lee JY, et al. Diagnostic colonoscopy following a positive fecal occult blood test in community health center patients. *Cancer Causes Control*. 2016;27:881–887.
- Hayward RA, Kent DM, Vijan S, Hofer TP. Multivariable risk prediction can greatly enhance the statistical power of clinical trial subgroup analysis. *BMC Med Res Methodol*. 2006;6:18.
