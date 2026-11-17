# Estimation and Association Analysis of Biomarkers for Tumor Immune Microenvironment

**Principal investigator:** Wei Sun, Fred Hutchinson Cancer Research Center  
**Application mechanism:** R21

## 1. Project overview

This project will develop statistical methods for estimating the immune-cell composition of bulk tumor samples from gene expression and DNA methylation, and for using those estimates in survival prediction while accounting for deconvolution uncertainty. The intended products are likelihood-based methods, open-source R software, and analyses of The Cancer Genome Atlas (TCGA), beginning with colon and lung cancer and extending across cancer types.

The longer-term goal is to create tumor immune microenvironment (TIME) biomarkers for epidemiological and translational studies. Possible future uses include defining tumor subtypes or predicting immunotherapy response. The present exploratory project focuses on composition estimation and prognostic survival signatures.

## 2. Background and rationale

The clinical success of immunotherapy in some patients emphasizes the importance of immune cells within tumors. Bulk-tumor omics contain information about those cells as well as tumor cells. Existing approaches have used a small set of marker genes, gene-set enrichment, support-vector regression, or nonnegative least squares. However, closely related immune cells may have similar molecular profiles; blood-derived reference profiles may differ from profiles in a tumor; and cell size and transcriptional activity influence total RNA contribution. Consequently, an RNA mixture proportion need not directly equal a cell-number proportion.

The proposed framework combines expression with methylation. Methylation is intended to help address transcriptional differences, while complementary signals may help separate related cell types. Reference-cell profiles will provide initialization rather than being assumed immutable between blood and TIME. A further contribution is to carry uncertainty into downstream analyses. Treating estimated cell fractions as observed without error can bias associations or distort precision. Joint modeling will instead treat these fractions as latent variables shared by omics and survival components.

## 3. Objectives and specific aims

1. **Estimate immune-cell composition.** Develop and evaluate a likelihood framework combining expression and methylation, accommodating differences between reference cells and TIME, and quantifying uncertainty. Once trained, the method should also support new samples with only one of the two omics modalities.
2. **Construct prognostic immune signatures.** Jointly model survival and molecular measurements with latent immune fractions, and stabilize prediction by borrowing information across molecularly similar cell types.

## 4. Preliminary work and research basis

Preliminary TCGA colon-cancer analyses estimated composition from expression and methylation separately. Even using a limited set of cell types and sample-by-sample estimation, the two modalities produced associated but nonidentical estimates. Samples with more than 750 somatic mutations were classified as hypermutated; these had lower estimated B-cell and higher monocyte proportions. Our preliminary analysis gives Wilcoxon p-values 0.046 and 0.005, respectively. These are preliminary associations motivating more complete joint modeling.


The investigators examined 9,401 CpGs shared between two studies with SD >0.02 in both, using 86 samples from ten cell types grouped into five broad categories. Although methylation across CpGs is bimodal, individual-CpG distributions within cell groups can be reasonably approximated by normal distributions. Analogous checks supported a log-normal model for expression. Simulated mixtures of five log-normal cell-type distributions yielded more accurate fraction estimates with a log-normal generalized linear model than with normal linear regression.

A separate preliminary validation used six methylation samples with known proportions for five cell types and 362 informative CpGs. Maximum likelihood gave residual sum of squares 0.029 versus 0.043 for nonnegative least squares. After perturbing 10% of probes, the mixture-based method gave 0.055 versus 0.155. These results support testing a component for features that do not follow the mixture model.

## 5. Research design and methods

### 5.1 Inputs, scale, and assumptions

The model assumes external measurements from purified cell types and prior estimates of tumor purity, for example from copy-number data. Expression must be on a scale where mixing is linear: read-depth-corrected gene counts for RNA-seq or pre-log microarray intensities. Methylation uses beta values, β = M/(M + U + 100), where M and U are methylated and unmethylated probe intensities. Expression is modeled as log-normal, and methylation as normal at each CpG. The choice seeks adequate fit and computational efficiency rather than the use of more elaborate count or beta distributions.

Let I be the number of tumor samples, J genes, K CpGs, and Q non-tumor cell types. Y and Z denote the tumor expression and methylation matrices. Fq and Hq denote reference measurements for cell type q, with potentially different numbers of replicates by modality. Let ρqi be the proportion of cell type q in tumor sample i and ηi its pre-estimated tumor purity. The fractions obey:

$$\sum_{q=1}^{Q}\rho_{qi}=1-\eta_i.$$

Cell-type expression parameters are means and variances on the log scale; methylation parameters are means and variances on the beta-value scale. Tumor-cell parameters form a separate component. Normal mixture means and variances are calculated from the cell fractions and their component parameters. Sums of log-normal expression contributions are approximated by a log-normal distribution. 


For methylation, the mixture mean and variance are

$$\widetilde\nu_{ki}=\eta_i\nu_{k0}+\sum_{q=1}^Q\rho_{qi}\nu_{kq},\qquad
\widetilde\tau_{ki}^{2}=\eta_i^2\tau_{k0}^{2}+\sum_{q=1}^Q\rho_{qi}^{2}\tau_{kq}^{2}.$$

For expression, write $w_{0i}=\eta_i$ for tumor cells and $w_{qi}=\rho_{qi}$ for non-tumor cells, and let $\gamma_{jq}=\exp(\mu_{jq}+\sigma_{jq}^2/2)$. The approximating log-normal distribution matches mixture moments:

$$\widetilde\sigma_{ji}^{2}=\log\left[1+\frac{\sum_{q=0}^Qw_{qi}^{2}\gamma_{jq}^{2}(e^{\sigma_{jq}^{2}}-1)}{(\sum_{q=0}^Qw_{qi}\gamma_{jq})^2}\right],\qquad
\widetilde\mu_{ji}+\frac{\widetilde\sigma_{ji}^{2}}{2}=\log\left(\sum_{q=0}^Qw_{qi}\gamma_{jq}\right).$$

### 5.2 Aim 1: likelihood estimation

The likelihood combines four sources of information: tumor expression conditional on tumor and non-tumor expression parameters and cell fractions; tumor methylation conditional on corresponding methylation parameters and fractions; purified-cell expression; and purified-cell methylation. Schematically,

$$L(\Theta)=p(Y\mid\Theta_{0F},\{\Theta_{qF}\},\rho)\,p(Z\mid\Theta_{0H},\{\Theta_{qH}\},\rho)\prod_q p(F_q\mid\Theta_{qF})\prod_q p(H_q\mid\Theta_{qH}).$$

A coordinate-ascent algorithm will initialize reference-cell parameters from the purified-cell data and tumor-cell parameters from relatively high-purity samples. It will then alternate between estimating cell fractions conditional on molecular profiles and re-estimating those profiles conditional on fractions until convergence. This allows the many tumor samples to refine blood-derived references. The approach can be used when only one modality has reference data and may be extended to other molecular modalities.

Systematic differences between RNA- and methylation-derived composition estimates will inform multiplicative cell-size/transcriptional-activity factors for the expression profiles. For example, low RNA contribution from neutrophils relative to their DNA-derived fraction may indicate fewer transcripts per cell. Such factors are identifiable when tumor sample number exceeds immune-cell-type number.

Reference expression data will include more than 800 samples covering 28 cell types from published datasets. Methylation references will combine Illumina 450k data from six studies, using control samples in case–control datasets. Cell-type-specific quantile normalization will harmonize datasets; preliminary principal-component plots support removal of major study effects while retaining cellular differences.



### 5.3 Feature robustness and validation

A two-component mixture will distinguish informative genes or CpGs from noise features that do not follow the deconvolution model. This is intended to improve robustness to blood–TIME differences and discordant molecular features. The preliminary known-mixture experiment provides an initial test of this strategy, but more extensive validation is planned.

Simulated bulk tissues will be generated by weighted mixtures of measured cell-specific expression and methylation. The proposed method will be compared with existing expression- and methylation-based deconvolution methods. Experimental datasets with flow-cytometry-established composition will provide additional benchmarks. Published measurements of immune cells isolated from TIME will assess the estimated tumor-context expression profiles.

The TCGA analyses will estimate both immune fractions and cell-type-specific expression and methylation within each cancer type. Composition estimates will also be used to cluster tumors, with a weighted Kullback–Leibler distance in which a fraction's contribution is inversely weighted by its estimation variance. Once molecular profiles have been estimated, new tumor samples can be deconvolved using expression, methylation, or both. TCGA sampling may underrepresent highly immune-infiltrated tumors because of preparation criteria; the investigators will account for that limitation when interpreting population patterns.


### 5.4 Aim 2: joint survival and omics model

Let Ti be the observed event or censoring time, δi indicate death, and Xi denote additional clinical covariates. The joint likelihood multiplies the Aim 1 omics likelihood by survival contributions conditional on latent fractions, tumor purity, and Xi:

$$L(\Theta,\Theta_T)=p(Y,Z,\{F_q\},\{H_q\}\mid\Theta)\prod_i p[T_i\mid h(\rho_i,\eta_i),X_i,\Theta_T].$$

The survival contribution is

$$p(T_i\mid h_i,X_i)=\lambda(T_i\mid h_i,X_i)^{\delta_i}\exp\left[-\int_0^{T_i}\lambda(t\mid h_i,X_i)\,dt
ight].$$

For the exponential example, $\lambda(t\mid h_i,X_i)=\exp(h_i+X_i\kappa)$. The initial implementation will use a parametric hazard for computational efficiency, with exponential survival given as an example; a semiparametric formulation is an alternative. Blockwise coordinate ascent will update the omics parameters, survival parameters, and shared fractions iteratively.

Cell fractions and purity sum to one, so conventional unconstrained regression is inappropriate. Following the compositional-data approach, the predictor will use log fractions:

$$h(\rho_i,\eta_i)=\beta_0+\sum_q\beta_q\log(\rho_{qi})+\gamma\log(\eta_i),\qquad \sum_q\beta_q+\gamma=0.$$

Highly similar immune cells may be difficult to distinguish. For survival prediction, their effects need not be estimated independently without stabilization. Cell types will first be hierarchically clustered using both omics modalities; their dendrogram order supplies a one-dimensional ordering. A fused-lasso penalty, λΣ|βt − βt−1|, encourages adjacent cell types to have similar survival effects. BIC will select the penalty tuning parameter. Adjacent cell types tend to be similar, although adjacency does not guarantee similarity.

### 5.5 Prognostic evaluation and alternative datasets

Prediction rather than hypothesis testing is the main evaluation objective for Aim 2. Cross-validated C-index will compare the joint model against models using expression or methylation separately and against the conventional two-stage method that first estimates fractions and then treats them as known in survival regression. TCGA offers a broad benchmark across cancers, but its survival data may be heavily censored and are not necessarily cancer-specific. Other datasets with both molecular measurements and survival will therefore also be explored.

## 6. Implementation, resources, and schedule

Wei Sun contributes statistical genomics, copy-number, expression, and software expertise. Michael Wu contributes methylation and association-method expertise. Collaborations with Ulrike Peters and McGarry Houghton support colorectal- and lung-cancer applications. Fred Hutch provides statistical and computing resources for TCGA-scale analysis.

Aim 1 is planned for year 1 and Aim 2 for year 2. Methods, analyses, and software will be published. All methods will be implemented as open-source R packages, with computationally intensive components in C or C++ and Rcpp integration.

## 7. Ethics and data practices

The study uses existing public, de-identified omics and available clinical covariates, with no new recruitment or direct participant contact. Sources include TCGA, ENCODE, and published immune-cell datasets. The study qualifies for Exemption 4. Investigators will not seek identifying information, and data will be stored on designated secured machines accessible only to project personnel. No direct benefit to the original participants is anticipated; the expected benefit is methodological and scientific.

## 8. References

- Newman et al. (2015). Robust enumeration of cell subsets from tissue expression profiles. *Nature Methods*, 12(5), 453–457.
- Reinius et al. (2012). Differential DNA methylation in purified human blood cells: implications for cell lineage and studies on disease susceptibility. *PLoS ONE*, 7(7), e41361.
- Accomando et al. (2014). Quantitative reconstruction of leukocyte subsets using DNA methylation. *Genome Biology*, 15(3).
- De Simone et al. (2016). Transcriptional landscape of human tissue lymphocytes unveils uniqueness of tumor-infiltrating T regulatory cells. *Immunity*, 45(5), 1135–1147.
- Tirosh et al. (2016). Dissecting the multicellular ecosystem of metastatic melanoma by single-cell RNA-seq. *Science*, 352(6282), 189–196.
- Farlik et al. (2016). DNA methylation dynamics of human hematopoietic stem cell differentiation. *Cell Stem Cell*, 19(6), 808–822.
- Reynolds et al. (2014). Age-related variations in the methylome associated with gene expression in human monocytes and T cells. *Nature Communications*, 5, 5366.
- Zhang et al. (2013). Genome-wide DNA methylation analysis identifies hypomethylated genes regulated by FOXP3 in human regulatory T cells. *Blood*, 122(16), 2823–2836.

Additional reference datasets include the studies by Angelova, Limbach, Sallustio, Vento-Tormo, Coit, Schlums, and Kulis. The mathematical development also cites Lin et al. (2014) for constrained compositional regression and related penalized estimation.
