# Evaluation results

Twenty shared proposals, six methods, and 120 reports. Scores range from 1 to 5. Intervals are 95% paired proposal-bootstrap intervals from 10,000 resamples.

## 1. Review quality (n = 20)

| Method | Overall quality ↑ | Value recognition ↑ | Judgment & grounding ↑ |
| --- | --- | --- | --- |
| SpaceBioReview | 3.55 [3.35, 3.75] | 4.45 [4.25, 4.65] | 3.20 [2.95, 3.45] |
| Single Research Agent | 3.05 [2.85, 3.25] | 4.00 [3.70, 4.25] | 2.45 [2.20, 2.75] |
| Static Committee | 3.10 [2.90, 3.30] | 4.05 [3.90, 4.25] | 2.65 [2.40, 2.90] |
| Without Consultation | 2.75 [2.50, 3.00] | 3.80 [3.55, 4.05] | 2.25 [2.10, 2.45] |
| smolagents | 3.10 [2.90, 3.30] | 4.00 [3.85, 4.15] | 2.75 [2.55, 2.95] |
| Thorne Section-wise | 2.90 [2.65, 3.20] | 3.85 [3.70, 4.00] | 2.70 [2.40, 3.00] |

## 2. Pairwise comparisons

| Comparator | Full wins | Ties | Full losses | Win / tie / loss % | Overall-quality difference [95% CI] |
| --- | --- | --- | --- | --- | --- |
| Single Research Agent | 19 | 1 | 0 | 95 / 5 / 0 | 0.50 [0.25, 0.75] |
| Static Committee | 14 | 6 | 0 | 70 / 30 / 0 | 0.45 [0.25, 0.65] |
| Without Consultation | 19 | 1 | 0 | 95 / 5 / 0 | 0.80 [0.55, 1.05] |
| smolagents | 14 | 3 | 3 | 70 / 15 / 15 | 0.45 [0.20, 0.70] |
| Thorne Section-wise | 14 | 5 | 1 | 70 / 25 / 5 | 0.65 [0.35, 0.95] |

Pairs are ranked by overall quality, then judgment and grounding, then value recognition. Equal score triples are ties. Differences are SpaceBioReview minus the comparator.

## 3. Overall-quality distribution

| Method | 1 | 2 | 3 | 4 | 5 |
| --- | --- | --- | --- | --- | --- |
| SpaceBioReview | 0 | 0 | 9 | 11 | 0 |
| Single Research Agent | 0 | 2 | 15 | 3 | 0 |
| Static Committee | 0 | 1 | 16 | 3 | 0 |
| Without Consultation | 0 | 6 | 13 | 1 | 0 |
| smolagents | 0 | 1 | 16 | 3 | 0 |
| Thorne Section-wise | 0 | 5 | 12 | 3 | 0 |

## 4. Runtime and report length

Runtime uses the 16 proposals with recorded durations for every method. Report length uses all 20 proposals and includes references. Values are medians [Q1, Q3].

| Method | Runtime, minutes (n = 16) | Report words (n = 20) |
| --- | --- | --- |
| SpaceBioReview | 16.6 [15.0, 21.5] | 5,019 [4,595, 5,559] |
| Single Research Agent | 8.8 [7.8, 9.9] | 4,219 [3,878, 4,745] |
| Static Committee | 9.3 [8.8, 10.0] | 4,514 [4,242, 4,840] |
| Without Consultation | 15.3 [13.6, 16.3] | 4,975 [4,655, 5,297] |
| smolagents | 2.8 [2.1, 3.3] | 3,256 [3,047, 3,442] |
| Thorne Section-wise | 2.9 [2.7, 3.3] | 3,246 [2,674, 3,552] |

## 5. Report-level scores

| Project | Method | Overall | Value | Grounding | Delivery status |
| --- | --- | --- | --- | --- | --- |
| nci_hdrp_r01_coronado | SpaceBioReview | 4 | 4 | 4 | completed |
| nci_hdrp_r01_coronado | SingleResearchAgent | 3 | 4 | 2 | completed |
| nci_hdrp_r01_coronado | StaticResearchCommittee | 3 | 4 | 3 | completed |
| nci_hdrp_r01_coronado | WithoutConsultation | 2 | 3 | 2 | completed |
| nci_hdrp_r01_coronado | smolagents | 3 | 4 | 2 | completed |
| nci_hdrp_r01_coronado | ThorneSectionWise | 4 | 4 | 4 | completed |
| nhgri_r01_brothers | SpaceBioReview | 4 | 5 | 4 | completed |
| nhgri_r01_brothers | SingleResearchAgent | 4 | 4 | 4 | completed |
| nhgri_r01_brothers | StaticResearchCommittee | 4 | 4 | 4 | completed |
| nhgri_r01_brothers | WithoutConsultation | 3 | 4 | 2 | completed |
| nhgri_r01_brothers | smolagents | 3 | 4 | 3 | completed |
| nhgri_r01_brothers | ThorneSectionWise | 4 | 4 | 4 | completed |
| nci_epi_r03_prizment | SpaceBioReview | 3 | 4 | 2 | completed |
| nci_epi_r03_prizment | SingleResearchAgent | 2 | 2 | 2 | completed |
| nci_epi_r03_prizment | StaticResearchCommittee | 3 | 4 | 2 | completed |
| nci_epi_r03_prizment | WithoutConsultation | 3 | 3 | 2 | completed |
| nci_epi_r03_prizment | smolagents | 3 | 4 | 3 | completed |
| nci_epi_r03_prizment | ThorneSectionWise | 2 | 3 | 2 | completed |
| nhgri_r01_mcguire | SpaceBioReview | 4 | 4 | 4 | completed |
| nhgri_r01_mcguire | SingleResearchAgent | 3 | 4 | 3 | completed |
| nhgri_r01_mcguire | StaticResearchCommittee | 3 | 3 | 2 | completed |
| nhgri_r01_mcguire | WithoutConsultation | 3 | 4 | 3 | completed |
| nhgri_r01_mcguire | smolagents | 3 | 4 | 3 | completed |
| nhgri_r01_mcguire | ThorneSectionWise | 4 | 4 | 4 | completed |
| nci_epi_r21_sun | SpaceBioReview | 4 | 5 | 4 | completed |
| nci_epi_r21_sun | SingleResearchAgent | 3 | 3 | 2 | completed |
| nci_epi_r21_sun | StaticResearchCommittee | 3 | 4 | 3 | completed |
| nci_epi_r21_sun | WithoutConsultation | 3 | 4 | 3 | completed |
| nci_epi_r21_sun | smolagents | 3 | 4 | 3 | completed |
| nci_epi_r21_sun | ThorneSectionWise | 3 | 3 | 3 | completed |
| nidcd_r01_cherney_heinemann | SpaceBioReview | 4 | 5 | 3 | completed |
| nidcd_r01_cherney_heinemann | SingleResearchAgent | 3 | 4 | 3 | completed |
| nidcd_r01_cherney_heinemann | StaticResearchCommittee | 4 | 5 | 3 | completed |
| nidcd_r01_cherney_heinemann | WithoutConsultation | 3 | 3 | 2 | completed |
| nidcd_r01_cherney_heinemann | smolagents | 3 | 5 | 2 | completed |
| nidcd_r01_cherney_heinemann | ThorneSectionWise | 3 | 4 | 2 | completed |
| nci_hdrp_r01_braithwaite | SpaceBioReview | 4 | 4 | 4 | fixed_by_me |
| nci_hdrp_r01_braithwaite | SingleResearchAgent | 3 | 4 | 2 | completed |
| nci_hdrp_r01_braithwaite | StaticResearchCommittee | 4 | 4 | 3 | completed |
| nci_hdrp_r01_braithwaite | WithoutConsultation | 3 | 4 | 3 | completed |
| nci_hdrp_r01_braithwaite | smolagents | 3 | 4 | 2 | completed |
| nci_hdrp_r01_braithwaite | ThorneSectionWise | 2 | 3 | 2 | completed |
| nci_hdrp_r01_salsman | SpaceBioReview | 3 | 4 | 3 | completed |
| nci_hdrp_r01_salsman | SingleResearchAgent | 3 | 4 | 2 | completed |
| nci_hdrp_r01_salsman | StaticResearchCommittee | 3 | 4 | 3 | completed |
| nci_hdrp_r01_salsman | WithoutConsultation | 3 | 4 | 3 | completed |
| nci_hdrp_r01_salsman | smolagents | 3 | 4 | 3 | completed |
| nci_hdrp_r01_salsman | ThorneSectionWise | 3 | 4 | 2 | completed |
| nidcd_r01_carney | SpaceBioReview | 4 | 5 | 3 | completed |
| nidcd_r01_carney | SingleResearchAgent | 4 | 4 | 3 | completed |
| nidcd_r01_carney | StaticResearchCommittee | 3 | 5 | 2 | completed |
| nidcd_r01_carney | WithoutConsultation | 2 | 4 | 2 | completed |
| nidcd_r01_carney | smolagents | 4 | 4 | 3 | completed |
| nidcd_r01_carney | ThorneSectionWise | 2 | 4 | 2 | completed |
| nci_hdrp_r03_sprague | SpaceBioReview | 3 | 4 | 3 | completed |
| nci_hdrp_r03_sprague | SingleResearchAgent | 3 | 4 | 2 | completed |
| nci_hdrp_r03_sprague | StaticResearchCommittee | 3 | 4 | 3 | completed |
| nci_hdrp_r03_sprague | WithoutConsultation | 3 | 3 | 2 | completed |
| nci_hdrp_r03_sprague | smolagents | 4 | 4 | 3 | completed |
| nci_hdrp_r03_sprague | ThorneSectionWise | 3 | 4 | 3 | completed |
| niaid_r01_faubion | SpaceBioReview | 4 | 5 | 4 | fixed_by_me |
| niaid_r01_faubion | SingleResearchAgent | 4 | 5 | 4 | completed |
| niaid_r01_faubion | StaticResearchCommittee | 3 | 4 | 3 | completed |
| niaid_r01_faubion | WithoutConsultation | 4 | 5 | 3 | completed |
| niaid_r01_faubion | smolagents | 3 | 4 | 3 | completed |
| niaid_r01_faubion | ThorneSectionWise | 2 | 4 | 2 | completed |
| nidcd_r01_crane | SpaceBioReview | 4 | 5 | 3 | completed |
| nidcd_r01_crane | SingleResearchAgent | 3 | 5 | 3 | completed |
| nidcd_r01_crane | StaticResearchCommittee | 3 | 4 | 2 | completed |
| nidcd_r01_crane | WithoutConsultation | 3 | 4 | 2 | completed |
| nidcd_r01_crane | smolagents | 3 | 4 | 3 | completed |
| nidcd_r01_crane | ThorneSectionWise | 3 | 4 | 2 | completed |
| niaid_r21_petrie | SpaceBioReview | 4 | 4 | 3 | completed |
| niaid_r21_petrie | SingleResearchAgent | 3 | 4 | 3 | completed |
| niaid_r21_petrie | StaticResearchCommittee | 3 | 4 | 3 | completed |
| niaid_r21_petrie | WithoutConsultation | 3 | 4 | 2 | completed |
| niaid_r21_petrie | smolagents | 4 | 4 | 3 | completed |
| niaid_r21_petrie | ThorneSectionWise | 3 | 4 | 3 | completed |
| nidcd_r21_chow | SpaceBioReview | 3 | 4 | 3 | completed |
| nidcd_r21_chow | SingleResearchAgent | 3 | 4 | 2 | completed |
| nidcd_r21_chow | StaticResearchCommittee | 2 | 4 | 2 | completed |
| nidcd_r21_chow | WithoutConsultation | 3 | 4 | 2 | completed |
| nidcd_r21_chow | smolagents | 2 | 3 | 2 | completed |
| nidcd_r21_chow | ThorneSectionWise | 3 | 4 | 3 | completed |
| nidcd_r01_mccreery | SpaceBioReview | 3 | 5 | 2 | completed |
| nidcd_r01_mccreery | SingleResearchAgent | 3 | 4 | 2 | completed |
| nidcd_r01_mccreery | StaticResearchCommittee | 3 | 4 | 2 | completed |
| nidcd_r01_mccreery | WithoutConsultation | 2 | 3 | 2 | completed |
| nidcd_r01_mccreery | smolagents | 3 | 4 | 3 | completed |
| nidcd_r01_mccreery | ThorneSectionWise | 3 | 4 | 3 | completed |
| nhgri_r21_korngiebel | SpaceBioReview | 4 | 4 | 3 | fixed_by_me |
| nhgri_r21_korngiebel | SingleResearchAgent | 2 | 4 | 2 | completed |
| nhgri_r21_korngiebel | StaticResearchCommittee | 3 | 4 | 2 | completed |
| nhgri_r21_korngiebel | WithoutConsultation | 3 | 4 | 2 | completed |
| nhgri_r21_korngiebel | smolagents | 3 | 4 | 3 | completed |
| nhgri_r21_korngiebel | ThorneSectionWise | 3 | 4 | 3 | completed |
| nidcd_r01_gifford_camarata | SpaceBioReview | 3 | 5 | 3 | completed |
| nidcd_r01_gifford_camarata | SingleResearchAgent | 3 | 4 | 2 | completed |
| nidcd_r01_gifford_camarata | StaticResearchCommittee | 3 | 4 | 2 | completed |
| nidcd_r01_gifford_camarata | WithoutConsultation | 2 | 3 | 2 | completed |
| nidcd_r01_gifford_camarata | smolagents | 3 | 4 | 3 | completed |
| nidcd_r01_gifford_camarata | ThorneSectionWise | 3 | 4 | 3 | completed |
| niaid_r01_gandhi | SpaceBioReview | 3 | 4 | 3 | completed |
| niaid_r01_gandhi | SingleResearchAgent | 3 | 4 | 2 | completed |
| niaid_r01_gandhi | StaticResearchCommittee | 3 | 4 | 3 | completed |
| niaid_r01_gandhi | WithoutConsultation | 2 | 4 | 2 | completed |
| niaid_r01_gandhi | smolagents | 3 | 4 | 2 | completed |
| niaid_r01_gandhi | ThorneSectionWise | 2 | 4 | 2 | completed |
| nhgri_r21_stern | SpaceBioReview | 3 | 4 | 3 | completed |
| nhgri_r21_stern | SingleResearchAgent | 3 | 4 | 2 | completed |
| nhgri_r21_stern | StaticResearchCommittee | 3 | 4 | 3 | completed |
| nhgri_r21_stern | WithoutConsultation | 2 | 4 | 2 | completed |
| nhgri_r21_stern | smolagents | 3 | 4 | 3 | completed |
| nhgri_r21_stern | ThorneSectionWise | 3 | 4 | 3 | completed |
| nasa_evs3_farrar_smode_2018 | SpaceBioReview | 3 | 5 | 3 | fixed_by_me |
| nasa_evs3_farrar_smode_2018 | SingleResearchAgent | 3 | 5 | 2 | completed |
| nasa_evs3_farrar_smode_2018 | StaticResearchCommittee | 3 | 4 | 3 | completed |
| nasa_evs3_farrar_smode_2018 | WithoutConsultation | 3 | 5 | 2 | completed |
| nasa_evs3_farrar_smode_2018 | smolagents | 3 | 4 | 3 | completed |
| nasa_evs3_farrar_smode_2018 | ThorneSectionWise | 3 | 4 | 2 | completed |

## 6. Proposal-level comparisons

| Project | Comparator | Full outcome | Reason |
| --- | --- | --- | --- |
| nci_hdrp_r01_coronado | WithoutConsultation | win | E scores higher on overall quality (4 vs. 2), prioritized accordingly. |
| nci_hdrp_r01_coronado | smolagents | win | E scores higher on overall quality (4 vs. 3), prioritized accordingly. |
| nci_hdrp_r01_coronado | ThorneSectionWise | tie | Overall quality, Judgment and grounding, and Value recognition scores are identical (4, 4, 4), recorded as a tie. |
| nci_hdrp_r01_coronado | SingleResearchAgent | win | E scores higher on overall quality (4 vs. 3), prioritized accordingly. |
| nci_hdrp_r01_coronado | StaticResearchCommittee | win | E scores higher on overall quality (4 vs. 3), prioritized accordingly. |
| nhgri_r01_brothers | ThorneSectionWise | win | Given equal scores on preceding indicators, F scores higher on scientific value recognition (5 vs. 4), and is prioritized accordingly. |
| nhgri_r01_brothers | StaticResearchCommittee | win | Given equal scores on preceding indicators, F scores higher on scientific value recognition (5 vs. 4), and is prioritized accordingly. |
| nhgri_r01_brothers | WithoutConsultation | win | F scores higher on overall quality (4 vs. 3), and is prioritized accordingly. |
| nhgri_r01_brothers | SingleResearchAgent | win | Given equal scores on preceding indicators, F scores higher on scientific value recognition (5 vs. 4), and is prioritized accordingly. |
| nhgri_r01_brothers | smolagents | win | F scores higher on overall quality (4 vs. 3), and is prioritized accordingly. |
| nci_epi_r03_prizment | SingleResearchAgent | win | B scores higher on Overall quality (3 vs. 2), prioritized accordingly. |
| nci_epi_r03_prizment | WithoutConsultation | win | With preceding indicators tied, B scores higher on Value recognition (4 vs. 3), prioritized accordingly. |
| nci_epi_r03_prizment | smolagents | loss | With preceding indicators tied, D scores higher on Judgment and grounding (3 vs. 2), prioritized accordingly. |
| nci_epi_r03_prizment | ThorneSectionWise | win | B scores higher on Overall quality (3 vs. 2), prioritized accordingly. |
| nci_epi_r03_prizment | StaticResearchCommittee | tie | Overall quality, Judgment and grounding, and Value recognition scores are identical (3, 2, 4), recorded as a tie. |
| nhgri_r01_mcguire | ThorneSectionWise | tie | The three scores for Overall quality, Value recognition, and Judgment and grounding are identical (4, 4, 4), recorded as a tie. |
| nhgri_r01_mcguire | SingleResearchAgent | win | F scores higher on Overall quality (4 vs. 3) and is therefore preferred. |
| nhgri_r01_mcguire | StaticResearchCommittee | win | F scores higher on Overall quality (4 vs. 3) and is therefore preferred. |
| nhgri_r01_mcguire | WithoutConsultation | win | F scores higher on Overall quality (4 vs. 3) and is therefore preferred. |
| nhgri_r01_mcguire | smolagents | win | F scores higher on Overall quality (4 vs. 3) and is therefore preferred. |
| nci_epi_r21_sun | SingleResearchAgent | win | E scores higher on overall quality (4 vs. 3), and priority is given accordingly. |
| nci_epi_r21_sun | WithoutConsultation | win | E scores higher on overall quality (4 vs. 3), and priority is given accordingly. |
| nci_epi_r21_sun | smolagents | win | E scores higher on overall quality (4 vs. 3), and priority is given accordingly. |
| nci_epi_r21_sun | StaticResearchCommittee | win | E scores higher on overall quality (4 vs. 3), and priority is given accordingly. |
| nci_epi_r21_sun | ThorneSectionWise | win | E scores higher on overall quality (4 vs. 3), and priority is given accordingly. |
| nidcd_r01_cherney_heinemann | smolagents | win | F scores higher on Overall quality (4 vs. 3); priority is given accordingly. |
| nidcd_r01_cherney_heinemann | WithoutConsultation | win | F scores higher on Overall quality (4 vs. 3); priority is given accordingly. |
| nidcd_r01_cherney_heinemann | SingleResearchAgent | win | F scores higher on Overall quality (4 vs. 3); priority is given accordingly. |
| nidcd_r01_cherney_heinemann | ThorneSectionWise | win | F scores higher on Overall quality (4 vs. 3); priority is given accordingly. |
| nidcd_r01_cherney_heinemann | StaticResearchCommittee | tie | Overall quality, Judgment and grounding, and Value recognition are identical (4, 3, 5); recorded as a tie. |
| nci_hdrp_r01_braithwaite | SingleResearchAgent | win | E scores higher on Overall quality (4 vs. 3), prioritized on this basis. |
| nci_hdrp_r01_braithwaite | StaticResearchCommittee | win | Given tied scores on preceding indicators, E scores higher on Judgment and grounding (4 vs. 3), prioritized on this basis. |
| nci_hdrp_r01_braithwaite | smolagents | win | E scores higher on Overall quality (4 vs. 3), prioritized on this basis. |
| nci_hdrp_r01_braithwaite | ThorneSectionWise | win | E scores higher on Overall quality (4 vs. 2), prioritized on this basis. |
| nci_hdrp_r01_braithwaite | WithoutConsultation | win | E scores higher on Overall quality (4 vs. 3), prioritized on this basis. |
| nci_hdrp_r01_salsman | StaticResearchCommittee | tie | The scores for Overall quality, Judgment and grounding, and Value recognition are identical (3, 3, 4), recorded as a tie. |
| nci_hdrp_r01_salsman | WithoutConsultation | tie | The scores for Overall quality, Judgment and grounding, and Value recognition are identical (3, 3, 4), recorded as a tie. |
| nci_hdrp_r01_salsman | ThorneSectionWise | win | Given equal scores on the preceding indicators, B scores higher on Judgment and grounding (3 vs. 2) and is prioritized accordingly. |
| nci_hdrp_r01_salsman | smolagents | tie | The scores for Overall quality, Judgment and grounding, and Value recognition are identical (3, 3, 4), recorded as a tie. |
| nci_hdrp_r01_salsman | SingleResearchAgent | win | Given equal scores on the preceding indicators, B scores higher on Judgment and grounding (3 vs. 2) and is prioritized accordingly. |
| nidcd_r01_carney | ThorneSectionWise | win | C scores higher on Overall quality (4 vs. 2); prioritize on this basis. |
| nidcd_r01_carney | StaticResearchCommittee | win | C scores higher on Overall quality (4 vs. 3); prioritize on this basis. |
| nidcd_r01_carney | SingleResearchAgent | win | With preceding indicators tied, C scores higher on Value recognition (5 vs. 4); prioritize on this basis. |
| nidcd_r01_carney | WithoutConsultation | win | C scores higher on Overall quality (4 vs. 2); prioritize on this basis. |
| nidcd_r01_carney | smolagents | win | With preceding indicators tied, C scores higher on Value recognition (5 vs. 4); prioritize on this basis. |
| nci_hdrp_r03_sprague | WithoutConsultation | win | Given identical scores on preceding metrics, C scores higher on Judgment and Grounding (3 vs. 2), and is prioritized accordingly. |
| nci_hdrp_r03_sprague | ThorneSectionWise | tie | Overall Quality, Judgment and Grounding, and Value Recognition scores are identical (3, 3, 4); recorded as a tie. |
| nci_hdrp_r03_sprague | SingleResearchAgent | win | Given identical scores on preceding metrics, C scores higher on Judgment and Grounding (3 vs. 2), and is prioritized accordingly. |
| nci_hdrp_r03_sprague | smolagents | loss | E scores higher on Overall Quality (4 vs. 3), and is prioritized accordingly. |
| nci_hdrp_r03_sprague | StaticResearchCommittee | tie | Overall Quality, Judgment and Grounding, and Value Recognition scores are identical (3, 3, 4); recorded as a tie. |
| niaid_r01_faubion | StaticResearchCommittee | win | B scores higher on Overall quality (4 vs. 3), so B is prioritized. |
| niaid_r01_faubion | WithoutConsultation | win | With preceding indicators tied, B scores higher on Judgment and grounding (4 vs. 3), so B is prioritized. |
| niaid_r01_faubion | ThorneSectionWise | win | B scores higher on Overall quality (4 vs. 2), so B is prioritized. |
| niaid_r01_faubion | SingleResearchAgent | tie | Overall quality, Judgment and grounding, and Value recognition all have identical scores (4, 4, 5), recorded as a tie. |
| niaid_r01_faubion | smolagents | win | B scores higher on Overall quality (4 vs. 3), so B is prioritized. |
| nidcd_r01_crane | ThorneSectionWise | win | D scores higher on overall quality (4 vs. 3); prioritize accordingly. |
| nidcd_r01_crane | SingleResearchAgent | win | D scores higher on overall quality (4 vs. 3); prioritize accordingly. |
| nidcd_r01_crane | StaticResearchCommittee | win | D scores higher on overall quality (4 vs. 3); prioritize accordingly. |
| nidcd_r01_crane | WithoutConsultation | win | D scores higher on overall quality (4 vs. 3); prioritize accordingly. |
| nidcd_r01_crane | smolagents | win | D scores higher on overall quality (4 vs. 3); prioritize accordingly. |
| niaid_r21_petrie | ThorneSectionWise | win | A scores higher on Overall quality (4 vs. 3), and is prioritized accordingly. |
| niaid_r21_petrie | WithoutConsultation | win | A scores higher on Overall quality (4 vs. 3), and is prioritized accordingly. |
| niaid_r21_petrie | smolagents | tie | Overall quality, Judgment and grounding, and Value recognition scores are identical (4, 3, 4), recorded as a tie. |
| niaid_r21_petrie | SingleResearchAgent | win | A scores higher on Overall quality (4 vs. 3), and is prioritized accordingly. |
| niaid_r21_petrie | StaticResearchCommittee | win | A scores higher on Overall quality (4 vs. 3), and is prioritized accordingly. |
| nidcd_r21_chow | WithoutConsultation | win | Given equal scores on preceding indicators, F scores higher on Judgment and Grounding (3 vs. 2), and is prioritized accordingly. |
| nidcd_r21_chow | SingleResearchAgent | win | Given equal scores on preceding indicators, F scores higher on Judgment and Grounding (3 vs. 2), and is prioritized accordingly. |
| nidcd_r21_chow | ThorneSectionWise | tie | Overall quality, Judgment and grounding, and Value recognition scores are identical (3, 3, 4); recorded as a tie. |
| nidcd_r21_chow | smolagents | win | F scores higher on Overall quality (3 vs. 2), and is prioritized accordingly. |
| nidcd_r21_chow | StaticResearchCommittee | win | F scores higher on Overall quality (3 vs. 2), and is prioritized accordingly. |
| nidcd_r01_mccreery | WithoutConsultation | win | F scores higher on Overall quality (3 vs. 2), so it is prioritized. |
| nidcd_r01_mccreery | smolagents | loss | With preceding indicators tied, B scores higher on Judgment and grounding (3 vs. 2), so it is prioritized. |
| nidcd_r01_mccreery | ThorneSectionWise | loss | With preceding indicators tied, C scores higher on Judgment and grounding (3 vs. 2), so it is prioritized. |
| nidcd_r01_mccreery | StaticResearchCommittee | win | With preceding indicators tied, F scores higher on Value recognition (5 vs. 4), so it is prioritized. |
| nidcd_r01_mccreery | SingleResearchAgent | win | With preceding indicators tied, F scores higher on Value recognition (5 vs. 4), so it is prioritized. |
| nhgri_r21_korngiebel | smolagents | win | F scores higher on Overall quality (4 vs. 3), so it is prioritized accordingly. |
| nhgri_r21_korngiebel | SingleResearchAgent | win | F scores higher on Overall quality (4 vs. 2), so it is prioritized accordingly. |
| nhgri_r21_korngiebel | ThorneSectionWise | win | F scores higher on Overall quality (4 vs. 3), so it is prioritized accordingly. |
| nhgri_r21_korngiebel | StaticResearchCommittee | win | F scores higher on Overall quality (4 vs. 3), so it is prioritized accordingly. |
| nhgri_r21_korngiebel | WithoutConsultation | win | F scores higher on Overall quality (4 vs. 3), so it is prioritized accordingly. |
| nidcd_r01_gifford_camarata | SingleResearchAgent | win | Given equal scores on preceding indicators, E scores higher on Judgment and Grounding (3 vs. 2), and is prioritized accordingly. |
| nidcd_r01_gifford_camarata | smolagents | win | Given equal scores on preceding indicators, E scores higher on Value Recognition (5 vs. 4), and is prioritized accordingly. |
| nidcd_r01_gifford_camarata | WithoutConsultation | win | E scores higher on Overall Quality (3 vs. 2), and is prioritized accordingly. |
| nidcd_r01_gifford_camarata | ThorneSectionWise | win | Given equal scores on preceding indicators, E scores higher on Value Recognition (5 vs. 4), and is prioritized accordingly. |
| nidcd_r01_gifford_camarata | StaticResearchCommittee | win | Given equal scores on preceding indicators, E scores higher on Judgment and Grounding (3 vs. 2), and is prioritized accordingly. |
| niaid_r01_gandhi | ThorneSectionWise | win | C scores higher on Overall quality (3 vs. 2) and is prioritized accordingly. |
| niaid_r01_gandhi | smolagents | win | With preceding indicators tied, C scores higher on Judgment and grounding (3 vs. 2) and is prioritized accordingly. |
| niaid_r01_gandhi | SingleResearchAgent | win | With preceding indicators tied, C scores higher on Judgment and grounding (3 vs. 2) and is prioritized accordingly. |
| niaid_r01_gandhi | WithoutConsultation | win | C scores higher on Overall quality (3 vs. 2) and is prioritized accordingly. |
| niaid_r01_gandhi | StaticResearchCommittee | tie | Overall quality, Judgment and grounding, and Value recognition all have identical scores (3, 3, 4) and are recorded as a tie. |
| nhgri_r21_stern | smolagents | tie | Overall quality, Judgment and grounding, and Value recognition scores are identical (3, 3, 4); recorded as a tie. |
| nhgri_r21_stern | WithoutConsultation | win | B scores higher on Overall quality (3 vs. 2); prioritized on this basis. |
| nhgri_r21_stern | StaticResearchCommittee | tie | Overall quality, Judgment and grounding, and Value recognition scores are identical (3, 3, 4); recorded as a tie. |
| nhgri_r21_stern | SingleResearchAgent | win | Given identical scores on preceding indicators, B scores higher on Judgment and grounding (3 vs. 2); prioritized on this basis. |
| nhgri_r21_stern | ThorneSectionWise | tie | Overall quality, Judgment and grounding, and Value recognition scores are identical (3, 3, 4); recorded as a tie. |
| nasa_evs3_farrar_smode_2018 | smolagents | win | Given tied scores on preceding indicators, A scores higher on Value recognition (5 vs. 4), and is prioritized accordingly. |
| nasa_evs3_farrar_smode_2018 | SingleResearchAgent | win | Given tied scores on preceding indicators, A scores higher on Judgment and grounding (3 vs. 2), and is prioritized accordingly. |
| nasa_evs3_farrar_smode_2018 | ThorneSectionWise | win | Given tied scores on preceding indicators, A scores higher on Judgment and grounding (3 vs. 2), and is prioritized accordingly. |
| nasa_evs3_farrar_smode_2018 | StaticResearchCommittee | win | Given tied scores on preceding indicators, A scores higher on Value recognition (5 vs. 4), and is prioritized accordingly. |
| nasa_evs3_farrar_smode_2018 | WithoutConsultation | win | Given tied scores on preceding indicators, A scores higher on Judgment and grounding (3 vs. 2), and is prioritized accordingly. |
