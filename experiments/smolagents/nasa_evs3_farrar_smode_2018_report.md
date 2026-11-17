# Scientific Project Review: Submesoscale Ocean Dynamics and Vertical Transport (S-MODE)

**Review date:** 28 October 2026  
**Proposal source:** NASA Earth Venture Suborbital-3 application (archive: src-fa47dd712481)  
**PI:** J. Thomas Farrar, Woods Hole Oceanographic Institution  
**Proposed period:** 1 July 2019–30 June 2024

---

## Overall Assessment

S-MODE is an ambitious, well-motivated multi-platform field investigation of submesoscale ocean dynamics and their role in vertical exchange. The proposal marshals an exceptionally broad array of airborne and in situ assets—DopplerScatt, PRISM, MASS, MOSES, Lagrangian floats, Wave Gliders, Seagliders, and a research vessel—to test the hypothesis that kilometer-scale fronts and eddies make important contributions to vertical transport of climate and biological variables. The scientific question is timely and significant: submesoscale processes represent a frontier where numerical models diverge and observational constraints are scarce. The proposal is distinguished by its thoughtful nested sampling design, strong instrumental performance margins, explicit threshold/baseline descope logic, and a team with demonstrated expertise across the needed disciplines.

The proposal's principal strengths are (a) a clear conceptual framework linking established theory (frontogenesis, mixed-layer instabilities) to specific testable predictions, (b) mature instruments at TRL ≥6 with prior flight heritage, (c) two complementary methods for estimating vertical velocity (Lagrangian float tracking and Wave Glider divergence arrays), and (d) a realistic staging through a pilot campaign before the main IOPs.

The key limitation is that the proposal's most scientifically distinctive contribution—Goal 4's remote-sensing proxy for vertical flux—is explicitly described as exploratory and depends on developing a poorly constrained vertical length scale *h* from the in situ data themselves. This circularity is acknowledged but not resolved. More broadly, the separation of submesoscale signals from internal waves and tides, the sensitivity of the flux proxy to measurement noise, and the statistical stability of fluxes from a modest number of feature realizations remain open questions that the design partially addresses but cannot fully resolve. The proposal's substantial observational capability makes it likely to advance several goals even where complete closure proves elusive.

---

## Scientific Value: B (Good) — Confidence: B (High)

The proposal addresses an important, well-framed question at a genuine frontier of ocean dynamics. The ocean surface boundary layer mediates climate-relevant air-sea exchange, yet the contribution of submesoscale (0.2–25 km) motions to vertical fluxes remains uncertain because models are sensitive to resolution and parameterization choices and observations are sparse. The proposal identifies this gap clearly, citing the disagreement between ROMS and MITgcm simulations of the California Current as concrete motivation.

The attainable knowledge contribution operates at multiple levels. At the descriptive level, Goals 1 and 3 will produce a benchmark observational dataset of submesoscale three-dimensional structure—temperature, salinity, velocity, vorticity, divergence, and strain—at resolutions that no existing dataset provides. This alone would support model evaluation and process interpretation for years beyond the campaign. At the conceptual level, Goal 2's tests of wind-stress modulation, vorticity-modified Ekman pumping, and wave–front interaction will provide direct evidence on air-sea coupling mechanisms that are presently known only from theory and simulation. At the methodological level, establishing whether DopplerScatt surface-current divergence can serve as a usable proxy for vertical velocity (even with limitations) would represent a genuine advance for remote sensing of ocean vertical exchange.

The practical contribution to satellite validation (SWOT, MODIS, and future missions) and to NASA's ocean-observing communities is credible and clearly articulated. The data delivery schedule to PO.DAAC is specified, with six-month post-campaign targets.

The scientific value is moderated by several considerations. First, the central hypothesis—that submesoscale dynamics make important contributions to vertical exchange—is now supported by a substantial body of modeling work (Capet et al. 2008; McWilliams 2016; Su et al. 2018) and by the process-study observations of D'Asaro et al. (2018) and Johnson et al. (2020; "Restratification at a California Current Upwelling Front, Part I," *J. Phys. Oceanogr.*). S-MODE extends rather than originates this line of inquiry. Second, Goal 4—the flux proxy—is genuinely exploratory. The proposal states that "complete flux quantification is difficult, but even partial progress would advance understanding." This is honest but limits what can be promised from the most distinctive element of the baseline mission. Third, the proposal does not quantify how many independent feature realizations are needed to distinguish model predictions statistically; three submesoscale surveys (threshold) or six (baseline) may be insufficient for robust model discrimination depending on natural variability.

---

## Innovation: B (Good) — Confidence: B (High)

S-MODE's innovation lies primarily in its observational approach rather than in proposing new physical mechanisms. The coordinated use of DopplerScatt surface-current mapping to guide in situ sampling of submesoscale features is genuinely novel. No prior campaign has combined wide-swath airborne Doppler radar with Lagrangian floats, Wave Glider divergence arrays, and hyperspectral ocean color in the manner proposed. The 2017 Gulf of Mexico DopplerScatt demonstration (Rodriguez et al. 2018) showed the instrument's capability; S-MODE would be the first to exploit it systematically for process-oriented submesoscale science.

The dual vertical-velocity estimation strategy—direct Lagrangian float measurement plus Wave Glider divergence integration—is methodologically innovative. Each method has different error characteristics and assumptions, and their comparison will itself be informative. The proposal appropriately notes that divergence-based estimates will be tested against float measurements.

The exploratory flux proxy (Goal 4) is conceptually innovative in seeking to combine satellite-style remote sensing (divergence from radar, tracers from ocean color and SST) into a vertical flux estimate. However, the proxy depends on an empirical length scale *h* that must be calibrated from the same in situ observations it is meant to circumvent. This limits the independent novelty of the result but does not diminish the value of exploring the approach.

Relative to prior work, the proposal extends D'Asaro et al. (2018) by adding aircraft remote sensing, seasonal contrast, and systematic repeat surveys. It extends the Johnson et al. (2020) Lagrangian-front study by providing the broader spatial context that a single float-and-ship survey cannot supply. The innovation is a meaningful step forward, though it is an evolutionary rather than revolutionary advance in submesoscale observational capability.

---

## Rigor: B (Good) — Confidence: B (High)

The proposal's design is thoughtful and well-specified. The science traceability matrix (Table 2) connects each observable to required sampling, required accuracy, and demonstrated instrument performance. The margins are generally comfortable: DopplerScatt delivers 0.0086 m s⁻¹ over 2 km against a requirement of 0.08 m s⁻¹; MOSES has 0.01 K noise against a 0.05 K requirement; Lagrangian float vertical velocity precision of 0.001 m s⁻¹ against a 0.003 m s⁻¹ requirement. The one-hour coincidence requirement for SST and ocean color relative to current mapping is well-justified and appears achievable with the described flight patterns.

The nested sampling strategy (Fig. 4.1) is a well-conceived response to the scale problem: aircraft provide 75 × 300 km context, Seagliders map the 25 km mesoscale setting, and the ship, floats, Wave Gliders, and drifters resolve the 1–5 km submesoscale core. The observing-system simulation experiment (OSSE) planned before the main campaigns, using models from 2 km down to 30 m grid spacing, will test the sampling design against known simulated fields—a prudent and standard approach.

Several rigor concerns warrant attention. First, the separation of submesoscale signals from internal waves and tides is acknowledged as a core challenge but the proposal does not specify how this separation will be performed operationally. The isopycnal-following float outside the front provides an internal-wave reference, but the analysis strategy for combining this with frontal observations is sketched rather than detailed. Second, the divergence-to-vertical-velocity calculation for the Wave Glider array assumes that the 1 km square resolves the relevant horizontal divergence; if divergence is concentrated at scales smaller than 1 km, the estimate will be biased. Third, the statistical treatment of flux estimates (Goal 4) is discussed qualitatively but no power analysis or synthetic-data test is presented to establish how many feature realizations are needed for a stable flux estimate given measurement noise. Fourth, the proposal acknowledges that DopplerScatt wind-related biases vary more slowly than kilometer-scale gradients, supporting the use of derivative products, but does not quantify the residual bias in divergence at the 2 km smoothing scale.

These are not fatal defects. They represent the kind of uncertainty that is inherent in a first-of-its-kind observational program, and the pilot campaign is explicitly designed to address interoperability and measurement agreement. The proposal's candor about Goal 4's exploratory nature is a strength rather than a weakness.

---

## Feasibility: B (Good) — Confidence: B (High)

The feasibility case is strong. All airborne instruments are at TRL 6 or higher with existing flight heritage in the proposed configurations. DopplerScatt has demonstrated wind and current retrieval in the Gulf of Mexico (2017). PRISM has flown on ER-2 and other platforms. MASS has participated in 16 campaigns over seven years. MOSES has been used in two Gulf of Mexico campaigns. The oceanographic team has direct experience with Seagliders, Lagrangian floats, Wave Gliders, and underway CTD surveys.

The aircraft accommodation analysis (Table 5) shows comfortable margins for mass, volume, altitude, speed, duration, and power across all three platforms. The proposed 10–15 flights per IOP with 200% campaign-duration margin for weather provides substantial scheduling flexibility. The identification of backup aircraft and backup campaign seasons (September 2020 and April 2022) shows realistic contingency planning.

The principal feasibility risks are operational rather than instrumental. The California Current region experiences seasonal fog, and the proposal acknowledges this by selecting April and September to "avoid the worst seasonal fog" while noting that front prevalence is greatest in July–October. There is a tension here: the chosen months represent a compromise between atmospheric conditions and expected submesoscale activity, and the proposal does not quantify how much frontal activity is sacrificed. The risk matrix (Table 7) rates fog/cloud/rain as C3/L3 (moderate mission risk, 25–60% likelihood), mitigated by seasonal selection and duration margin, with residual C3/L1.

The ship request (79 days including transit) and the multi-platform coordination are logistically demanding. The pilot campaign, which deliberately omits Seagliders and Lagrangian floats, provides a realistic test of the integrated platform operations before full deployment. The one-year gap between pilot and first IOP allows for calibration, analysis, and correction.

The most significant feasibility limitation concerns Goal 4. The vertical length scale *h* for the flux proxy must be inferred from in situ vertical velocity and radar divergence. If the relationship between surface divergence and vertical velocity proves to be strongly variable across features, seasons, or forcing conditions, the proxy may not generalize. The proposal acknowledges this dependence but does not identify a fallback analysis strategy if *h* proves unconstrained. This is an acceptable risk for an exploratory goal, but it means Goal 4 could produce null or inconclusive results despite successful data collection.

---

## Ethics and Safety: A (Excellent) — Confidence: B (High)

The proposal demonstrates appropriate attention to safety and environmental considerations. The use of biodegradable CARTHE-style drifters is a specific, commendable measure that reduces plastic pollution. The field program employs established ship and aircraft operations with standard NASA operating requirements. Aircraft operations are arranged to meet NASA airworthiness and flight safety standards, with NASA Ames ESPO providing deployment support.

No human subjects are involved. No protected communities, sensitive ecosystems, or culturally significant areas are identified in the operations domain. The offshore California Current region is a routine oceanographic working area without particular environmental sensitivity beyond general marine stewardship expectations.

The proposal does not explicitly discuss animal welfare considerations (marine mammal impacts from acoustic instruments), but the ADCP frequencies employed (300 kHz; Ka-band radar at 35.75 GHz for the airborne system) are standard for oceanographic research and do not present unusual acoustic risk. Autonomous platform operations carry some risk of loss and potential marine debris, though Wave Gliders and Seagliders are recoverable in principle and the proposal describes replacement contingencies.

No ethical concerns are raised by the scientific goals or methods as described. The rating reflects a well-managed operational safety posture and a specific environmental mitigation (biodegradable drifters) that exceeds routine practice.

---

## Management: B (Good) — Confidence: C (Moderate)

The management structure is clearly delineated: aircraft measurements (Rodriguez), in situ observations (Shcherbina), and analysis/modeling (D'Asaro) under the PI (Farrar) and Science Investigation Manager (Matthias), with NASA Ames ESPO providing field deployment support. The named scientific leaders have extensive and directly relevant track records in the respective areas. The organizational chart (Fig. 6.1) is conventional and appropriate for a multi-institution field program.

The timeline is well-structured: pilot (April 2020), first IOP (April 2021), second IOP (September 2021), with data deliveries phased through March 2022 and analysis through June 2024. The allocation of six postdoctoral researchers with staggered appointments—early modeling to optimize operations, rapid first-IOP analysis informing the second, later appointments for air-sea interaction and synthesis—shows thoughtful sequencing.

The risk management framework is explicit, with a standard C/L matrix, quarterly review, and specific mitigations. However, the risk ratings appear optimistic in places. Airborne instrument performance is rated C5/L2 (operations failure/budget overrun likelihood, 5–25% likelihood), which seems high for mature TRL ≥6 instruments. More importantly, the scientific interpretation risks—e.g., inability to separate submesoscale from internal-wave signals, failure of the flux proxy, insufficient statistical power—are not included in the formal risk table. These are not implementation risks but scientific risks that could affect whether the investigation achieves its stated goals.

Confidence in the management rating is moderate because the proposal lacks itemized budget information, personnel CVs, and detailed institutional commitments. Absent these, the assessment relies on the plausibility of the described arrangements and the reputation of the institutions involved (WHOI, JPL, UW, SIO, UCLA). The data management plan is appropriate: each group is responsible for its Level-2 products, delivery is overseen by the PI and Science Investigation Manager, and data are targeted for PO.DAAC archival within six months of each campaign.

---

## Summary of Ratings

| Criterion | Rating | Confidence |
|---|---|---|
| Scientific Value | B (Good) | B (High) |
| Innovation | B (Good) | B (High) |
| Rigor | B (Good) | B (High) |
| Feasibility | B (Good) | B (High) |
| Ethics and Safety | A (Excellent) | B (High) |
| Management | B (Good) | C (Moderate) |

---

## Recommendation: Support

S-MODE proposes a scientifically significant, well-designed, and realistically staged investigation of submesoscale ocean dynamics using a unique combination of mature airborne and in situ observational assets. The investigation is likely to produce a benchmark dataset that will advance understanding of submesoscale vertical exchange and support model evaluation for years beyond the campaign. Goals 1–3 are well-supported by the measurement strategy; Goal 4 is appropriately characterized as exploratory. The principal scientific risks—separation of submesoscale from wave signals, statistical power for flux estimates, and the empirical nature of the flux proxy—are inherent to the frontier being explored and are acknowledged with reasonable mitigation strategies. The threshold/baseline descope logic provides a credible fallback that preserves the core scientific contribution. The proposal represents a worthwhile investment in observational capability at a scale that addresses a recognized gap between process studies and climate models.