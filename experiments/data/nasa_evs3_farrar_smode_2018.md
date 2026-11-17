# Submesoscale Ocean Dynamics and Vertical Transport (S-MODE)

## Project overview

S-MODE proposes a coordinated airborne and oceanographic investigation of the hypothesis that submesoscale ocean dynamics make important contributions to the vertical exchange of climate and biological variables in the upper ocean. The investigation will combine direct measurements, remote sensing, theory, and numerical simulation to determine how kilometer-scale fronts and eddies move water and its properties between the surface boundary layer and the ocean interior. The intended product is a benchmark observational data set that can discriminate between high-resolution model predictions and improve the interpretation of surface measurements from aircraft and satellites.

**Principal investigator:** J. Thomas Farrar, Woods Hole Oceanographic Institution  
**Program:** NASA Earth Venture Suborbital-3  
**Proposed period:** 1 July 2019–30 June 2024  
**Scientific leadership:** Ernesto Rodriguez, aircraft measurements; Andrey Shcherbina, in situ observations; Eric D'Asaro, science analysis and modeling; Paul Matthias, Science Investigation Manager.

The field program is centered offshore of San Francisco, in a domain extending approximately 300 km offshore. A pilot campaign will precede two 25-day intensive operations periods (IOPs), one in spring and one in fall, allowing measurements under contrasting mixed-layer depth, forcing, and stratification. DopplerScatt surface-current and wind maps will guide a nested in situ survey involving a research vessel, Wave Gliders, Seagliders, surface drifters, and water-following Lagrangian floats. PRISM, MASS, and MOSES will add ocean color, sea surface temperature (SST), waves, and sea surface topography.

The investigation has four science goals. Goals 1 and 3 define the threshold mission; the baseline mission supports all four goals through longer observations and a second season. The program combines measurements intended to test established process predictions with a more exploratory effort to construct and validate remote-sensing proxies for vertical flux.


**Figure 1.1.** Aircraft and ocean platforms will observe the region offshore of California during a two-week pilot and two 25-day intensive operations periods in spring and fall 2021, with 10–15 flights per period. The nominal site is 300 km from San Francisco.

**Table 1. Instrument and platform inventory**

| Instrument | Variables | Platform |
|---|---|---|
| NASA-JPL DopplerScatt | Surface currents and winds | NASA King Air B200 |
| UCLA MOSES | SST | NASA King Air B200 |
| NASA-JPL PRISM | Hyperspectral ocean color | NASA ER-2 |
| SIO MASS: lidar, infrared, visible and hyperspectral imaging | Directional wave spectra, surface topography, SST and ocean color | Twin Otter DHC-6 |
| GPS surface drifters | Upper-ocean currents | Ship-deployed, 100 drifters |
| Meteorology, current profiler, fixed-depth CTDs and bio-optics | Surface fluxes and wind; upper-ocean temperature, salinity, velocity, chlorophyll and beam attenuation | Four Wave Gliders |
| CTD, current profiler, bio-optics and turbulence sensors | Temperature, salinity, velocity, chlorophyll, backscatter and float tracking | Five Seagliders |
| CTD, bio-optics and ADCPs | Lagrangian trajectories, vertical velocity, temperature, salinity and turbulence | University of Washington Lagrangian floats |
| Current profilers and Underway CTD | Upper-ocean temperature, salinity, velocity and chlorophyll | Research vessel |

## Scientific background and rationale

The ocean surface boundary layer represents only about 2% of ocean volume but mediates exchange of heat, nutrients, oxygen, and carbon with the atmosphere. Mesoscale eddies and waves, conventionally spanning about 25-1,000 km, have relatively weak vertical velocities, of order 1-10 m per day or less. At smaller horizontal wavelengths of approximately 0.2-25 km and time scales of hours to days, fronts and eddies can develop strong ageostrophic secondary circulations. These submesoscale motions connect nearly two-dimensional, geostrophic mesoscale flow to three-dimensional turbulence and can produce substantial buoyancy and biogeochemical fluxes.

The physical expectation is that frontogenesis and several instabilities generate strong vertical motion near horizontal density gradients. Resulting buoyancy fluxes can restratify the upper ocean, while vertical circulation transports biological material and nutrients between the illuminated surface layer and deeper gradient layers. Convergence and dispersion also organize patchiness and horizontal transport. The relevant scales overlap with inertial currents and internal gravity waves, but those motions have different dynamics. A snapshot of small-scale variability is therefore insufficient to attribute an observed vertical velocity to submesoscale circulation.

Numerical models have supplied much of the evidence for these effects. Increasing model resolution produces abundant fronts and filaments, but the quantitative predictions remain sensitive to numerical damping, grid spacing, boundary-layer turbulence, and air-sea exchange formulations. At grid spacings approaching 100 m, explicitly simulated submesoscale structures overlap scales represented by turbulence parameterizations. The interaction is two-way: turbulence modifies fronts, and frontal gradients modify turbulence. Existing models only partially represent this coupling.

California Current simulations from ROMS and MITgcm illustrate this uncertainty. Both generate submesoscale structure, but they differ in the relative prominence of eddies and fronts and in the seasonal contrast between March and September. The regional ROMS illustration has 500 m grid spacing; the global MITgcm illustration has approximately 2 km spacing. The comparison motivates observations that can test the structure, evolution, and exchange processes.


**Figure 3.1.** California Current surface temperature at 12 km and 0.75 km grid spacing illustrates the emergence of fronts and eddies as the simulated submesoscale range expands (Capet et al., 2008).


**Figure 3.2.** Surface vorticity in March and September from regional ROMS at 500 m spacing and global MITgcm at about 2 km spacing. The rectangle marks the experiment region. Vorticity is normalized by the local Coriolis parameter. The upper-right inset shows Gulf of Mexico DopplerScatt observations on the same distance and color scales.

Air-sea interaction adds uncertainty. Ocean vorticity can exceed the vertical component of Earth's rotation and alter the usual relationship between wind stress and Ekman pumping. SST gradients and currents can modify wind stress, while fronts can refract or otherwise modify surface waves and wave breaking. Bulk exchange coefficients used in models have not been established at these scales. Modeling suggests that resolving submesoscales strongly changes mean air-sea heat flux; observations will test the magnitude and mechanisms of this effect.

Sustained observations are required for three reasons. First, repeated measurements are needed to separate submesoscale circulation from internal tides, inertial currents, and other wave motions. Second, a feature evolves over several days and the in situ system can characterize only one feature at a time; several weeks are needed to obtain multiple realizations. Third, mixed-layer depth, winds, and heat flux change seasonally, and competing simulations disagree about how submesoscale activity responds. Spring and fall campaigns provide a direct contrast in those conditions. Model resolution denotes grid spacing; observational resolution denotes effective independent sample spacing. The resolved wavelength is approximately twice the sampling interval.


**Figure 3.3.** Vertical velocity at 20 m in a 500 m ROMS simulation (left) and the vorticity-gradient contribution to wind-driven vertical motion estimated using Stern’s theory (right).

## Specific science goals and success requirements

### Goal 1: Quantify three-dimensional structures responsible for vertical exchange

The first goal is to measure the evolving temperature, salinity, density, velocity, and optical structure of submesoscale features and their mesoscale setting. DopplerScatt is the central targeting instrument: daily maps over roughly 100 km will locate fronts and provide spatial context for concentrated ship and autonomous-platform observations. The target is 1 km effective sampling and current-gradient accuracy of about 2 × 10^-5 s^-1, or approximately 0.2 times the Coriolis parameter. This is intended to resolve frontal divergence, vorticity, and strain. Wind-speed accuracy of approximately 1.5 m s^-1 is required for typical winds of 3-10 m s^-1.

SST and ocean color must be observed sufficiently close in time to current mapping that moving fronts can be aligned. The target is within one hour, corresponding to substantially less than 1 km displacement for a typical current of 0.1 m s^-1. SST differences smaller than 0.05°C at 100 m sampling should resolve fronts with approximately 0.5°C contrast across 5 km. A chlorophyll difference of 0.05 mg m^-3 is the desired discrimination for features with about 0.5 mg m^-3 contrast.

Vertical velocity will be estimated by two complementary methods. Lagrangian floats and their ADCPs will measure water-following motion and velocity profiles directly; repeated float positions accurate to approximately 300 m are needed to place these records in context. Four Wave Gliders arranged in a nominal 1 km square will measure horizontal divergence as a function of depth, extending from a few meters to about 70 m. Integrating divergence supplies a second estimate of vertical velocity. The required divergence precision is better than 2 × 10^-5 s^-1 against typical 1 km-scale values near 10^-4 s^-1.

The ship will repeat approximately 5 km surveys around a tracked float every 1-2 hours, obtaining nominal 1 km horizontal sampling and profiles to about 100 m. Standard CTD accuracy of 0.005°C and 0.01 g kg^-1, and approximately 0.01 m s^-1 ADCP accuracy after 60 s averaging, are sufficient. A surrounding Seaglider survey will sample the mesoscale over 25 km, to 1,000 m depth, with approximately 5 km sampling and a two-day repeat requirement.

### Goal 2: Quantify air-sea interaction and surface forcing

The second goal tests how wind, heat exchange, currents, SST gradients, waves, and wave breaking influence submesoscale structure and vertical velocity. DopplerScatt will provide coincident wind and current fields, while the ship and at least two Wave Gliders will measure the bulk-flux variables needed to interpret them: wind, SST, air temperature, humidity, shortwave radiation, and downwelling infrared radiation. Repeated frontal crossings will reveal their spatial variation.

The physical tests include current- and SST-induced changes in stress, vorticity-modified Ekman pumping, and the effect of wind orientation relative to a front. Down-front forcing can sharpen fronts and promote exchange; opposite forcing can slump fronts and restratify the upper ocean. MASS lidar will characterize directional wave spectra over wavelengths from hundreds of meters down to decimeter scales. Infrared and visible imagery will measure foam and wave breaking, while Wave Glider wave sensors supply complementary local spectra. The analysis will compare observed vertical velocities with theoretical predictions and investigate whether wave modulation helps explain departures from conventional bulk-flux descriptions.

### Goal 3: Relate remotely sensed surface currents to boundary-layer currents

DopplerScatt samples the top few centimeters, whereas the circulation relevant to vertical exchange extends through tens of meters. Near-surface shear and Stokes drift can make those currents differ substantially. Dense arrays of approximately 50 surface drifters over a roughly 10 km region will bridge the radar measurement to the upper 60 cm; ADCPs will extend the comparison to deeper layers.

The investigation will assemble simultaneous, co-located velocity observations and examine their dependence on depth, horizontal scale, wind stress, wave properties, and density stratification. Away from fronts, these comparisons will test relatively established expectations for near-surface shear and wave effects. At fronts, strong density and velocity gradients introduce less understood behavior; high-resolution simulations may be needed to interpret the discrepancies. The result will characterize the relationship between radar current measurements and currents within and beneath the surface boundary layer.

### Goal 4: Examine vertical transport across submesoscale and mesoscale ranges

Transport requires covariance of vertical velocity and the transported property. The target quantity is a flux of the form <w′C′>, where C may be temperature, chlorophyll, or particulate organic carbon (POC). Large vertical velocities alone do not establish a net flux. We will combine surface current divergence and remotely sensed tracers into a flux proxy and then evaluate its validity.

For horizontal divergence D = -∂W/∂z, we will use a characteristic vertical-velocity proxy W_D = D h, where h is an estimated vertical length scale. The length scale will be inferred from in situ vertical velocity and radar divergence, and separately assessed in numerical models. Given a usable relationship for h, the aircraft data will support estimates of <W_D′C′>. Statistical stability, convergence, and sensitivity to instrumental noise will be tested explicitly. Comparisons will be made with mesoscale Omega-equation estimates, Seaglider turbulent-flux estimates, and the contributions of different horizontal scales. This is the most exploratory goal. Complete flux quantification is difficult, but even partial progress would advance understanding of vertical transport.

### Threshold and baseline missions

The threshold program consists of a 10-day pilot and one 14-day IOP in spring or fall. The IOP requires at least seven DopplerScatt flights over a 50 × 100 km region; at least five matching SST and PRISM ocean-color scenes; seven mesoscale glider surveys spanning 25 km with two-day/5 km resolution; and three submesoscale feature surveys, each spanning 5 km and lasting three days at two-hour/1 km resolution. Temperature, salinity, velocity, and optical observations accompany these surveys, together with trajectories, vertical velocity, and velocity-gradient profiles at submesoscales.

The baseline extends the IOP to 25 days to increase the expected number of feature realizations to six, and adds another IOP in the opposite season. These additions support Goals 2 and 4 as well as the threshold goals. The proposed threshold provides an explicit descope option, reducing flight and ship time by about half and lowering processing and analysis demands. The science traceability matrix connects the required observations with instrument capabilities and the operations concept.

**Table 2. Science traceability matrix**

H denotes horizontal sampling and extent, V vertical sampling and range, and T temporal sampling and duration. Goals 1 and 3 form the threshold mission; Goals 2 and 4 extend the baseline mission.

| Goal and observable | Required space/time sampling | Required accuracy | Instrument performance |
|---|---|---|---|
| 1: Surface horizontal velocity | H: 2 km over >50 × 100 km; T: daily for >10 days | Gradients of 4 × 10⁻⁵ s⁻¹; 0.08 m/s over 2 km | DopplerScatt: 0.0086 m/s over 2 km; drifters: 0.05 m/s in 10 m/s wind |
| 1: Surface chlorophyll | H: 2 km over >50 × 100 km; T: daily for >10 days | Resolve differences of 0.05 mg/m³ | PRISM: ±30% over 0.01–10 mg/m³ in optically deep water |
| 1: SST | H: 2 km over >50 × 100 km; T: daily for >10 days | 0.05°C | MOSES/MASS: 0.01°C error; <1–10 m spatial resolution |
| 1: Mesoscale temperature | H: 5 km over 25 km; V: 5 m to 1,000 m; T: 2 days for >10 days | 0.01°C | Glider/ship CTD: 0.01°C |
| 1: Submesoscale temperature | H: 1 km over 5 km; V: 5 m to 150 m; T: 2 hours for >10 days | 0.01°C | Glider/ship CTD: 0.01°C |
| 1: Mesoscale/submesoscale salinity | As for temperature | 0.01 g/kg | Seagliders: 0.01 g/kg |
| 1: Mesoscale horizontal velocity | Mesoscale sampling above | 0.01 m/s | Seaglider ADCP: 0.01 m/s |
| 1: Submesoscale horizontal velocity | Submesoscale sampling above | 0.01 m/s | Ship ADCP: 0.01 m/s |
| 1: Submesoscale velocity gradients | One array with 1 km spacing; V: 4 m to 100 m; T: 30 s for >10 days | 2 × 10⁻⁵ s⁻¹ | Gliders in 1 km square: 0.02 m/s over 1 km; 2 m bins to 100 m |
| 1: Point vertical velocity | T: 30 s for >10 days | 0.003 m/s | Lagrangian float: 0.001 m/s |
| 1: Vertical velocity profiles | V: 2 m over 20 m; T: 60 s | 0.003 m/s | Float ADCP: 0.002 m/s |
| 1: Turbulent mixing | H: 5 km over 25 km; V: 5 m to 1,000 m; T: 2 days for >10 days | TKE dissipation noise <10⁻¹⁰ W/kg | Seaglider temperature/velocity microstructure noise <10⁻¹² W/kg |
| 3: Surface velocity and profiles | As for Goal 1 | As for Goal 1 | As for Goal 1 |
| 3: Surface wind vectors | H: 2 km over >50 × 100 km; T: daily for >10 days | <1.5 m/s | DopplerScatt and in situ: 1 m/s |
| 3: Wave breaking and directional spectra | H: 5 km over 50 × 100 km; T: daily for >10 days | Breaking kinematics at wave speeds >1 m/s; wavelengths from hundreds of meters to <1 m | MASS imagery: 0.05–0.5 m; lidar: 2–4 cm per return; Wave Glider directional-wave sensor: 10 cm RMS |
| 2: Solar radiation | Point measurements; T: 2 hours | Bias <15 W/m² | Ship/glider radiometers: bias <10 W/m² |
| 2: Downward infrared radiation | Point measurements and H: 5 km; T: 2 hours | Bias <15 W/m² | Ship/glider radiometers: bias <10 W/m² |
| 2: Air temperature and humidity | — | 0.25°C and 4% | Wave Gliders: 0.2°C and 2% |
| 2: Surface wind vectors | H: 2 km over >50 × 100 km; T: daily for >10 days | 1.5 m/s | DopplerScatt and in situ: 1 m/s |
| 2: Wave breaking and directional spectra | H: 5 km over 50 × 100 km; T: daily for >10 days | Wave speeds >1 m/s; wavelengths from hundreds of meters to <1 m | MASS imagery: 0.05–0.5 m; lidar: 2–4 cm per return; Wave Glider sensor: 10 cm RMS |
| 4: Co-located SST, chlorophyll, POC and velocity; supporting subsurface data | H: 5 km; V: 5 m; T: 2–24 hours | Chlorophyll range 0–20 mg/m³; POC range 10–1,000 mg/m³, based on MODIS-Aqua April/September climatology | PRISM chlorophyll ±30%; MASS chlorophyll ±50% over 0.01–10 mg/m³ in optically deep water; POC RMSD 30% over 10–1,000 mg/m³ |

The region must have active submesoscale processes, prior observational and modeling coverage, and ready ship and aircraft access. Airborne and in situ observations will occur on the same day, with radar, SST and ocean-color observations within one hour. Daily broad-area radar maps provide spatial context. Sustained sampling over days will separate dynamical processes; the baseline adds several weeks of sampling and contrasting seasons to resolve feature evolution and seasonal variability.

## Preliminary work and enabling observations

Earlier multi-platform studies showed that SST maps can locate a frontal eddy, drifters can identify convergence, ship surveys can resolve its density field, and a Lagrangian float can follow subduction. Figure 3.5 gives one such example, with independent float-motion and ADCP evidence for sinking. These observations establish the feasibility of the coordinated approach, while the new program extends spatial coverage, repetition, seasonal contrast, and simultaneous measurements.


**Figure 3.5.** Previous coordinated observations resolve a front and eddy, its density structure, and subduction. The red float trajectory follows water downward; float motion and ADCP measurements independently indicate sinking. Adapted from D’Asaro et al. (2018).

DopplerScatt has already demonstrated wide-swath wind and current retrievals and maps of surface vorticity and divergence, including the 2017 Gulf of Mexico example. Mature Lagrangian floats and high-rate ADCPs equipped with vertical beams and inertial motion measurements make direct vertical-velocity measurements more practical. PRISM and MASS add hyperspectral observations that can link physical patterns to biological and biogeochemical variables. High-resolution ocean simulations can now span a broad range of horizontal scales and provide detailed, testable predictions.

The instruments are at TRL 6 or higher and have been deployed in the proposed aircraft configurations. PRISM has operated on ER-2 and other aircraft; MASS has participated in 16 campaigns over seven years; MOSES has been used in two Gulf of Mexico campaigns. The oceanographic team has experience with Seagliders, Lagrangian floats, instrumented Wave Gliders, and underway CTD surveys. The pilot campaign will intercompare the measurements and test the integrated platform operations.


**Figure 3.6.** Gulf of Mexico measurements in April 2017 show normalized vorticity (left) and divergence (right). The positive-vorticity plume is associated with Mississippi River outflow. The approximately 25 km swaths have a blanked 2 km nadir zone (Rodriguez et al., 2018).

## Research design and measurement methods

### Site, seasonal contrast, and nested sampling

The California Current region is selected because earlier observational and modeling studies provide a strong context, the site is accessible from California ports and aircraft bases, and the operations area overlaps a planned SWOT fast-repeat crossover. A delay in the planned SWOT launch would not compromise the investigation.

April and September provide contrasting mixed-layer depths and atmospheric conditions while avoiding the worst seasonal fog. Front prevalence is described as greatest in July-October and lowest in March-April. The pilot is separated from the first main campaign by approximately a year to permit calibration, analysis, and correction of interoperability problems. Satellite sea surface height, winds, SST, and ocean color will supplement aircraft and in situ observations.

Aircraft operations have three modes. A broad DopplerScatt survey will cover the nominal 75 × 300 km domain at least once in a campaign to locate target features. A fast-sampling mode repeatedly crosses a 25 km intensive operations region on approximately 100 km tracks, requiring about 10 minutes per PRISM leg and 20 minutes per DopplerScatt/MOSES leg including turns. The faster systems can cover the area in under an hour; MASS covers the region in about two hours. We anticipate about four visits by all instruments in a sortie. A fast-synoptic mode covers a 50 km square around the intensive region, using complementary track orientations to reduce radar nadir gaps, improve wave sampling, and minimize sunglint. This larger pattern is repeated approximately twice per sortie. Weather, sea state, and near-real-time data determine the daily choice.

The intensive operations region follows a strong front or eddy. Four Seagliders will traverse racetrack patterns, diving to 1,000 m and surfacing roughly every 3 km. Each glider will travel about 25 km per day, allowing the array to map the region at approximately 3 km resolution every two days.

A Lagrangian float outside the main frontal feature will follow an isopycnal beneath the mixed layer, providing context for internal-wave, tidal, and mesoscale vertical motions. One or more floats in the front will follow subducting water. The ship will acoustically track them, with glider assistance, and survey velocity and density in a roughly 5 km neighborhood. Four Wave Gliders will maintain the approximately 1 km gradient-measurement array. Drifter deployments of more than 30 instruments in a 10 km pattern will characterize convergence and supply independent surface-current estimates. Individual frontal occupations last a few days before redeployment.


**Figure 4.1.** A nested grid spans 25 km to 1 km around a front using autonomous platforms and a research vessel. Sampling tracks are separated for clarity in the drawing and will overlap during operations.

Seawater samples from the vessel will be analyzed for chlorophyll-a, particulate and total organic carbon, oxygen, and nutrients. They will support calibration and drift correction, optical proxy construction, and estimates of nutrient and total-carbon flux. Their role is essential because remotely sensed optical products must be checked against material measurements.


**Figure 3.4.** VIIRS ocean-color imagery from February 2016 shows complex structures offshore of San Francisco.

### Airborne instruments and performance

DopplerScatt uses a coherent Ka-band radar at 35.75 GHz with a mechanically scanning 22 cm antenna. At 28,000 ft it covers a 24 km swath and retrieves vector currents and winds on a 200 m grid. GPS/IMU measurements remove aircraft motion. Except near nadir, native current-retrieval random errors are reported as below 15 cm s^-1 and often lower; averaging independent samples to 2 km produces much smaller errors. Wind-related biases vary more slowly than kilometer-scale gradients, which is why derivative products are a central scientific use. Opposite-heading calibration lines will be collected during deployment.

PRISM on the ER-2 measures hyperspectral radiance from 350-1,050 nm with 2.83 nm sampling. At approximately 65,000 ft it provides an approximately 11 km swath with better than 20 m ground sampling, enabling coverage within the one-hour coincidence requirement. The ER-2's speed and endurance permit repeated overlap with MASS for cross-calibration. PRISM products include corrected radiance, water-leaving reflectance, chlorophyll-a, and POC.

MASS combines lidar, visible, infrared, and hyperspectral imagers with precise inertial and GPS data. It supplies directional wave spectra, surface elevation, SST, ocean color, and wave-breaking statistics. Individual lidar returns have a stated vertical accuracy of 2-4 cm, with horizontal sampling down to approximately 0.2 m. Its slower Twin Otter platform requires careful scheduling for spatial and temporal overlap. MOSES shares the King Air with DopplerScatt and uses a longwave infrared camera to obtain coincident SST maps; it provides 0.01 K noise-equivalent temperature difference, approximately 13 m sampling, and an 8.4 km swath.


**Figure 5.1.** The airborne DopplerScatt radar package.


**Figure 5.2.** The King Air cabin arrangement and radar installation support joint surface-current and SST observations.


**Figure 5.3.** PRISM installed in the ER-2 nose.


**Figure 5.4.** The Twin Otter carries lidar, infrared, visible, hyperspectral, and navigation instruments. The pictured ALAMO float/AXCTD launch capability is not part of this investigation.

**Table 3. Instrument requirements and performance**

| Instrument and parameter | Requirement | Performance | Scientific use |
|---|---|---|---|
| DopplerScatt current precision | 0.09 m/s over 5 km | 0.0215 m/s over 5 km | Synoptic currents, divergence and gradients |
| DopplerScatt wind direction | <30° | <20° | Wind correction and air-sea interaction |
| DopplerScatt wind speed | 1.5 m/s | 1 m/s | Wind correction and air-sea interaction |
| DopplerScatt ground resolution | <5 km | <1 km, 200 m posting | Submesoscale surface processes |
| DopplerScatt swath | 50 × 100 km per sortie | 24 km; >50 × 600 km per sortie | Wide-area mapping and derivative fields |
| PRISM spectral range | 400–1,000 nm | 350–1,050 nm | Chlorophyll and biogeochemistry |
| PRISM spectral sampling | 10–15 nm | 2.83 nm | MODIS-comparable bandwidth and finer spectra |
| PRISM ground resolution | ≤200 m | <20 m | Match current maps; highest ER-2 altitude near 65,000 ft |
| PRISM swath | 5 km | <11 km | Cover features from 100 m to 10 km |
| MASS Riegl Q680i lidar wave-height accuracy | 10 cm per pulse | 2–4 cm per pulse | Wavenumber spectra from hundreds of meters to tens of centimeters; Stokes drift |
| MASS lidar sea-surface height | 0.5 cm over 10 km | 1 cm at 1 km and 0.3 cm at 10 km wavelength | Mesoscale/submesoscale height anomalies and internal waves |
| MASS lidar ground resolution | <5 m | 0.2–1 m | Submeter wave spectra and saturation/equilibrium ranges |
| MASS lidar swath | >100 m | 100–2,000 m | Directional wave-spectrum resolution |
| MASS infrared SST noise-equivalent difference | 0.1 K | <0.035 K | SST fronts and surface currents |
| MASS infrared spectral range | 8–9 μm | 7.5–10.5 μm and 8.0–9.2 μm | FLIR SC67000HS and SC6000 cameras |
| MASS infrared ground resolution | <10 m | 0.3–5 m | Altitude adjusted to target properties |
| MASS SPECIM Kestrel spectral range | 400–900 nm | 400–990 nm | Chlorophyll, biogeochemistry and ocean-color fronts |
| MASS hyperspectral sampling | 10–15 nm | 1.75–7 nm | MODIS-comparable bandwidth and finer spectra |
| MASS hyperspectral ground resolution | ≤200 | 0.1–1 m | Altitude adjusted to target properties |
| MASS hyperspectral swath | — | 200–2,000 m | Coverage adjusted through altitude |
| MASS visible spectral range | Visible | Visible | Whitecaps and wave-breaking rates |
| MASS visible sampling frequency | >2 Hz | 10 Hz | Whitecap kinematics at 100 knots; up to 100 Hz if needed |
| MASS visible ground resolution | <10 m | 0.05–1 m | Altitude adjusted to target properties |
| MASS visible swath | >100 m | 400–3,000 m | Altitude-dependent coverage |
| MOSES FLIR A6750SC SLS SST noise-equivalent difference | 0.05 K | 0.01 K | SST fronts |
| MOSES ground resolution | 100 m | 13 m | Altitude adjusted to target properties |
| MOSES swath | — | 8.4 km | Altitude-dependent coverage |

### Ocean platforms and sensors

The four Wave Gliders are wave-propelled SV3 vehicles with solar-recharged electronics. Their instrument suites characterize the lower atmosphere and upper approximately 100 m of ocean. Sensors include weather stations, sonic anemometers, CTDs, chlorophyll fluorescence, oxygen, beam transmission, inertial and GPS wave measurements, a 40 m thermistor chain, and current profilers. The suite includes a 300 kHz ADCP and a Nortek Signature 1000 with integrated motion sensing for velocity and Reynolds-stress observations.

CARTHE-style biodegradable drifters follow the upper 60 cm with wind slip below 0.5% of neutral 10 m wind speed. Large arrays both identify convergence and permit estimates of vorticity and divergence. The Underway CTD allows repeated casts at approximately 10-minute intervals; this yields about 2 km spacing at a ship speed of 6 knots. Seagliders carry hydrography, ADCP, oxygen, temperature and shear microstructure, and optical sensors. Shallower dives can increase sampling frequency at the cost of battery endurance.

Lagrangian floats use active buoyancy control and drag elements to follow three-dimensional water motion. Their payload includes dual temperature/conductivity sensors, oxygen and bio-optical sensors, and ADCPs measuring horizontal and vertical velocity structure and acoustic backscatter. They can operate in water-following or isopycnal-following modes, allowing direct frontal observations and an internal-wave comparison record.


**Figure 5.5.** The surface unit, tethered wave-powered propulsion unit, navigation instruments, and modular payload spaces support sustained measurements.

**Table 4. Wave Glider instruments**

| Instrument | Measurement |
|---|---|
| Vaisala WXT530 weather station | Two-dimensional winds, precipitation, humidity, temperature and air pressure |
| NovaLynx Gill R3-50 sonic anemometer | Three-dimensional winds, momentum and heat fluxes |
| RBRmaestro3 CTD and Sea-Bird GPCTD | Conductivity, temperature and depth |
| Turner Cyclops fluorometer | Chlorophyll-a fluorescence |
| RBRcoda ODOfast | Dissolved oxygen |
| Wetlabs C-star | Beam transmittance |
| Xsens MTI IMU | Position/attitude, surface displacement and directional wave spectra |
| Subsea Hemisphere V104 dual GPS | Position, surface displacement and directional wave spectra |
| PME 40 m thermistor chain | Temperature profiles |
| Teledyne Monitor 300 kHz ADCP | Current profiles |
| Nortek Signature 1000 with integrated IMU | Current profiles and Reynolds stress |

## Modeling, validation, and interpretation

Before the main campaigns, an observing-system simulation experiment will test sampling patterns, array dimensions, repeated coverage, and potential aliasing. The planned foundation is a simulation with 2 km horizontal spacing, 90 vertical levels, and both atmospheric and tidal forcing. Regional simulations will increase detail to a 250 m grid with 270 vertical levels over the synoptic region and a 30 m nonhydrostatic grid over the fast-sampling region. These models will predict statistical vertical heat fluxes and contaminating variability, while concurrent simulations will use high-quality forcing and boundary conditions from larger-scale data-assimilating systems. ROMS and MITgcm provide complementary model frameworks.

For Goal 1, observations will be mapped into coordinates moving with water or with the feature. This will produce evolving two- or three-dimensional descriptions of density, velocity, gradients, turbulence, optics, and trajectories. Seaglider sections, satellite altimetry, and broad DopplerScatt maps will supply the surrounding mesoscale context. The ensemble of features will be used to assess model physics.

For Goal 2, remotely sensed current/SST structure will be compared with winds and fluxes, and measured vertical velocities will be compared with process predictions. Wave spectra and breaking changes across fronts will help test the mechanisms of stress modulation. Goal 3 will use both direct co-located comparisons and scaling against atmospheric forcing to connect centimeter-depth radar currents, drifter currents, and ADCP currents in approximately the upper 1-60 m. For Goal 4, the length scale in the flux proxy will be developed with in situ guidance and then checked against simulations and mesoscale estimates.

## Implementation, resources, timeline, and risk

The pilot includes all aircraft systems, a research vessel, four Wave Gliders, and approximately 30 drifters. Seagliders and Lagrangian floats are omitted from the pilot to limit costs and because the team has extensive experience with those operations. The pilot will test platform/shore interoperability, space-time sampling, and agreement between velocity and gradient measurements. Surface drifters substitute for the frontal float during this limited operation. The pilot is scheduled for April 2020, with September 2020 as the backup.

The main campaigns are scheduled for April and September 2021, with September 2021 and April 2022 as backups. A six-month overall shift preserves the seasonal comparison. Aircraft requirements fit within the stated mass, power, duration, and altitude capabilities of the King Air, ER-2, and Twin Otter. Alternative aircraft are identified if the preferred platform is unavailable, although PRISM on a Twin Otter would sacrifice coverage and integration on a Gulfstream would incur additional effort. An ocean-class UNOLS vessel, nominally R/V Sally Ride, is requested for 79 days including scientific work, transit, mobilization, and demobilization.

The science-management structure divides aircraft observations, in situ observations, and analysis/modeling under the PI and Science Investigation Manager, with NASA Ames ESPO deployment support. Six postdoctoral researchers are planned for two-year appointments. Early modeling will optimize operations; rapid analysis of the first IOP will inform the second; later appointments focus on air-sea interaction, Wave Glider and wave data, and integrated frontal structure. Two science workshops per year are anticipated during the main analysis phase.

**Table 5. Aircraft accommodation requirements and capabilities**

| Payload | Parameter | Mass (lb) | Volume (ft³) | Altitude (ft) | Speed (knots) | Duration (hours) | Power (W) |
|---|---|---:|---|---|---|---:|---:|
| DopplerScatt and MOSES | Requirement | 497 | 33.6 | 28,000 | 240 | 5 | 1,700 |
| DopplerScatt and MOSES | Capability | 772 | 205 | 35,000 | 280 | 6 | 6,580 |
| DopplerScatt and MOSES | Margin, % | 55 | 510 | 25 | 17 | 20 | 287 |
| MASS | Requirement | 265 | 10 | 100–6,000 | 100 | 8 | 600 |
| MASS | Capability | 600 | 200 | 25,000 | 135 | 9 | 8,400 |
| MASS | Margin, % | 127 | 1,900 | >300 | 35 | 13 | 1,300 |
| PRISM | Requirement | <400 | Fit tested | 60,000–70,000 | 400–450 | 6 | 1,350 |
| PRISM | Capability | 2,600 | Fit tested | 70,000 | 450 | 8 | 6,580 |
| PRISM | Margin, % | N/A | N/A | N/A | N/A | 33 | 387 |


**Figure 6.1.** The program management office coordinates aircraft measurements, in situ observations, and data analysis and modeling, with field-deployment support from NASA Ames ESPO.

**Table 6. Major investigation milestones**

| Date or period | Milestone |
|---|---|
| 1 July 2019 | Project begins |
| April 2020 | Two-week pilot campaign |
| October 2020 | PO.DAAC data release 1 |
| April 2021 | First month-long main campaign |
| September 2021 | Second month-long main campaign |
| October 2021 | PO.DAAC data release 2 |
| March 2022 | PO.DAAC data release 3 |
| 2021–2022 | Data calibration, validation and delivery |
| 2022–2024 | Scientific analysis and publication |
| 30 June 2024 | Project ends |

| Phase | Proposed scientific and implementation milestones |
| --- | --- |
| July 2019-June 2020 | Historical-data/model analysis, pilot planning, instrument integration, confirmation and readiness reviews, April pilot with September backup. |
| July 2020-June 2021 | Pilot processing and intercomparison, improved algorithms and experiment plan, first main campaign in April 2021. |
| July 2021-June 2022 | Second main campaign in September 2021, rapid cross-campaign processing, early current/wind and environmental-dependence analyses. |
| July 2022-June 2023 | Full vertical-velocity and dissipation data set, derived-product improvements, relationship between surface currents and turbulence, publications. |
| July 2023-June 2024 | Final reprocessing, model-observation comparison, synthesis and publications, project close on 30 June 2024. |

Risks include fog/cloud/rain and weak winds, airspace restrictions, instrument malfunction or performance problems, and loss of autonomous platforms. Mitigations include seasonal scheduling, substantial campaign-duration margin, early air-traffic coordination, test flights, ground testing, spare components, and flexible scheduling. After an ocean-platform loss, the remaining array and ship tracks can be reorganized; early losses may be replaced from reserve. The mature instruments reduce development risk, but the measurement and scientific interpretation challenges remain. Risks will be reviewed at least quarterly.

**Table 7. Operational risks and mitigations**

| Risk | Initial C/L | Baseline mitigation | Residual C/L | Tracking period |
|---|---|---|---|---|
| Fog, clouds, rain or winds <3 m/s | 3/3 | Select campaign seasons using weather predictions; allow 200% campaign-duration margin for schedule and cost | 3/1 | Flight campaign |
| Airspace closure | 3/2 | Select a region without nearby warning areas; coordinate early and during operations with air traffic control | 3/1 | Flight campaign |
| Airborne instrument anomaly or failure | 4/1 | Ground testing and test flights; instrument teams available for repairs; schedule flexibility | 4/1 | Flight campaign |
| Airborne instrument performance | 5/2 | Ground testing and test flights; procure likely replacement components six months before campaigns | 5/1 | Pre-campaign ground tests and flights |
| Glider/Wave Glider instrument or platform loss | 4/1 | Laboratory testing; reorganize remaining platforms and ship tracks; replace early-campaign losses using reserve | 4/1 | Field campaign |

Consequence ratings C are 1: minimal; 2: small; 3: moderate mission risk or significant implementation risk; 4: significant mission risk or consumption of all contingency; and 5: operations failure or budget overrun. Likelihood ratings L are 1: <5%; 2: 5–25%; 3: 25–60%; 4: 60–90%; and 5: >90%.

## Data products, calibration, and access

DopplerScatt will retain redundant raw radar and motion data, produce geolocated calibrated scans, retrieve gridded winds and wind-corrected currents with formal errors, and derive vorticity, divergence, strain, and related Level-3 fields. Near-real-time quick looks support field targeting and quality checks. MASS data will be georeferenced using high-rate GPS/IMU information, with hyperspectral calibration and irradiance measurements supporting radiance and reflectance products. MOSES SST mosaics will be motion-corrected and adjusted for atmospheric path length, with cloud handling and near-real-time targeting products.

PRISM processing proceeds from digital counts to calibrated radiance, atmospheric correction, water-leaving reflectance, and biological products. Chlorophyll retrievals include both multiband satellite-comparable estimates and full-spectral retrievals; POC uses established optical relationships. Campaign water samples will validate these products, with CalCOFI observations providing an additional source. The traceability matrix lists PRISM chlorophyll uncertainty of approximately ±30%, MASS chlorophyll uncertainty of approximately ±50% over 0.01-10 mg m^-3 in optically deep water, and POC RMSD of about 30% over 10-1,000 mg m^-3.

Autonomous platforms store raw records onboard and telemeter subsets for control and rapid interpretation. Complete records will be recovered after deployment and quality controlled. Ship data include preserved raw observations, processed underway meteorology and hydrography, ADCP products, and quality-controlled CTD profiles in 1 m depth bins. Each experimental group is responsible for its products and for making Level-2 data available to other investigators and the public through the designated NASA archive.

The PI and Science Investigation Manager will oversee delivery of data to the designated NASA data center. Level-2 data will be available to the team and the public, with a target of six months after each campaign; product-specific deliveries and later reprocessing are scheduled below. PRISM and DopplerScatt products will be processed at JPL using their associated science data systems. The observations and publications will support NASA ocean, wind, topography, biology, and satellite-validation communities. The field program uses biodegradable drifters and established ship and aircraft operations, with aircraft arranged to meet NASA operating requirements.


**Figure 4.3.** Chlorophyll-a in Santa Monica Bay on 26 October 2015, measured by PRISM from the ER-2 near 65,000 ft, demonstrates about 11 km swath coverage with better than 20 m sampling.

**Table 8. Data-product delivery schedule**

| Level | Product | Lead | April 2020 campaign | April 2021 campaign | September 2021 campaign |
|---|---|---|---|---|---|
| 0 | In situ measurements | Shcherbina | June 2020 | June 2021 | November 2021 |
| 1 | In situ measurements | Shcherbina | October 2020 | October 2021 | March 2022 |
| 1 | DopplerScatt L0–L2 data | Perkovic-Martin | October 2020 | October 2021 | March 2022 |
| 3 | DopplerScatt L3 fields | Rodriguez | October 2020 | October 2021 | March 2022 |
| 1 | PRISM radiance | Mouroulis | October 2020 | October 2021 | March 2022 |
| 2 | PRISM water-leaving reflectance | Mouroulis | October 2020 | October 2021 | March 2022 |
| 3 | PRISM chlorophyll-a and POC | Mouroulis | October 2020 | October 2021 | March 2022 |
| 1 | MASS georeferenced lidar point cloud, radiance, infrared and visible imagery | Lenain | October 2020 | October 2021 | March 2022 |
| 2 | MASS bulk wave statistics, water-leaving reflectance and SST maps | Lenain | October 2020 | October 2021 | March 2022 |
| 3 | MASS directional wavenumber spectra, chlorophyll-a, infrared-derived currents, whitecap coverage and breaking statistics | Lenain | April 2021 | April 2022 | September 2023 |

## References

- Boccaletti, G., Ferrari, R., and Fox-Kemper, B. (2007). Mixed layer instabilities and restratification. *Journal of Physical Oceanography*, 37, 2228-2250.
- Capet, X., McWilliams, J. C., Molemaker, M. J., and Shchepetkin, A. F. (2008a-c). Mesoscale to submesoscale transition in the California Current System, Parts I-III. *Journal of Physical Oceanography*, 38.
- D'Asaro, E. A. et al. (2018). Ocean convergence and the dispersion of flotsam. *Proceedings of the National Academy of Sciences*.
- McWilliams, J. C. (2016). Submesoscale currents in the ocean. *Proceedings of the Royal Society A*, 472, 20160117.
- Omand, M. M. et al. (2015). Eddy-driven subduction exports particulate organic carbon from the spring bloom. *Science*, 348, 222-225.
- Melville, W. K. et al. (2016). The modular aerial sensing system. *Journal of Atmospheric and Oceanic Technology*, 33, 1169-1184.
- Mouroulis, P. et al. (2014). The Portable Remote Imaging Spectrometer (PRISM) coastal ocean sensor: design, characteristics and first flight results. *Applied Optics*, 53, 1363-1380.
- Rodriguez, E. et al. (2018). Estimating ocean vector winds and currents using a Ka-band pencil-beam Doppler scatterometer. *Remote Sensing*, 10, 576.
- Shcherbina, A. Y. et al. (2013). Statistics of vertical vorticity, divergence, and strain in a developed submesoscale turbulence field. *Geophysical Research Letters*, 40, 4706-4711.
- Stern, M. (1965). Interaction of a uniform wind stress with a geostrophic vortex. *Deep-Sea Research*, 12, 355-367.
- Su, Z. et al. (2018). Ocean submesoscales as a key component of the global heat budget. *Nature Communications*, 9.
- Todd, R. E. et al. (2017). Absolute velocity estimates from autonomous underwater gliders equipped with Doppler current profilers. *Journal of Atmospheric and Oceanic Technology*, 34, 309-333.
- Wang, J. et al. (2018). An observing system simulation experiment for the calibration and validation of the Surface Water and Ocean Topography sea surface height measurement using in-situ platforms. *Journal of Atmospheric and Oceanic Technology*, 35, 281-297.
