# Elasticsearch Live Search Comparison

## Run provenance

| Property | Value |
|---|---|
| Source revision | `5df0008cbcaf37d010fa299775c8d316778ff68d` |
| Query fixture SHA-256 | `493a42b25fc45cac50a49dd57d2a4652be4372a0edb206395df2c4147a1e2aab` |
| Elasticsearch | `9.4.2` |
| Index | `geo-series` |
| Mapping | `geo-series-v1` |
| Documents | 249736 |
| Retrieval | topk=5, deep=100, candidates=500, RRF k0=60, facet pool=100 |

## Model readiness

| Model | Query model | Revision | Vector field | Dimensions | Coverage |
|---|---|---|---|---:|---:|
| `bge_small_v15` | `BAAI/bge-small-en-v1.5` | `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` | `embedding_bge_384` | 384 | 249736 |
| `medcpt_v1` | `ncbi/MedCPT-Query-Encoder` | `d83a36cc6b8e3a5c5e9d9d6ba156808c1643dcbc` | `embedding_medcpt_768` | 768 | 249736 |
| `qwen3_06b_1024_v1` | `Qwen/Qwen3-Embedding-0.6B` | `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` | `embedding_qwen3_06b_1024` | 1024 | 249736 |
| `gemini_embedding_2_3072_v1` (context only) | `gemini-embedding-2` | — | `embedding_gemini_3072` | 3072 | 0 |

## Feature proof

| Feature | Status | Evidence |
|---|---|---|
| Index preflight | PASS | Elasticsearch 9.4.2 mapping and coverage |
| Exact lookup | PASS | lowercase gse1124 resolved to GSE1124 |
| Filters | PASS | OR-within and AND-across filters held |
| Blank facets | PASS | all_matches and own-filter omission held |
| Full hybrid | PASS | BM25+dense native RRF passed for all cases |
| Provenance | PASS | model field, dimensions, mapping, and mode matched |

## Query: control_childhood_malaria

**Search:** whole blood transcriptomics of children with severe malaria

**Intent:** Traceable control related to the known GSE1124 record.

**Filters:** `{}`

### Full hybrid: native RRF (BM25 + dense)

| Rank | BGE | MedCPT | Qwen |
|---:|---|---|---|
| 1 | GSE1124 — Whole blood transcriptome of childhood malaria | GSE1124 — Whole blood transcriptome of childhood malaria | GSE1124 — Whole blood transcriptome of childhood malaria |
| 2 | GSE316842 — Transcriptomic Profiling of Blood-Stage Plasmodium falciparum in Children with Severe Malarial Anem… | GSE316842 — Transcriptomic Profiling of Blood-Stage Plasmodium falciparum in Children with Severe Malarial Anem… | GSE316842 — Transcriptomic Profiling of Blood-Stage Plasmodium falciparum in Children with Severe Malarial Anem… |
| 3 | GSE117613 — Whole-blood transcriptional signatures composed of erythropoietic and Nrf2-regulated genes differ b… | GSE83667 — Parasite in vivo blood transcriptomes from Malawian children with cerebral malaria | GSE83667 — Parasite in vivo blood transcriptomes from Malawian children with cerebral malaria |
| 4 | GSE230169 — Blood Transcriptomics Implicate Epigenetics in the Acquisition of Disease Tolerance to Malaria | GSE72058 — Activated neutrophils are associated with pediatric cerebral malaria vasculopathy in Malawian child… | GSE117613 — Whole-blood transcriptional signatures composed of erythropoietic and Nrf2-regulated genes differ b… |
| 5 | GSE72058 — Activated neutrophils are associated with pediatric cerebral malaria vasculopathy in Malawian child… | GSE230169 — Blood Transcriptomics Implicate Epigenetics in the Acquisition of Disease Tolerance to Malaria | GSE230169 — Blood Transcriptomics Implicate Epigenetics in the Acquisition of Disease Tolerance to Malaria |

### Diagnostic components

| Rank | BM25 | BGE dense | MedCPT dense | Qwen dense |
|---:|---|---|---|---|
| 1 | GSE188427 — Whole blood transcriptomics in RSV infected children | GSE1124 — Whole blood transcriptome of childhood malaria | GSE156791 — Distinct metabolic perturbations associated with pediatric Plasmodium falciparum malaria during Int… | GSE1124 — Whole blood transcriptome of childhood malaria |
| 2 | GSE83667 — Parasite in vivo blood transcriptomes from Malawian children with cerebral malaria | GSE255403 — Defining the Inflammation and Immunity Transcriptome in Severe Malarial Anemia for Immunotherapeuti… | GSE116149 — Whole genome expression array of malaria infected human hosts | GSE316842 — Transcriptomic Profiling of Blood-Stage Plasmodium falciparum in Children with Severe Malarial Anem… |
| 3 | GSE316842 — Transcriptomic Profiling of Blood-Stage Plasmodium falciparum in Children with Severe Malarial Anem… | GSE316842 — Transcriptomic Profiling of Blood-Stage Plasmodium falciparum in Children with Severe Malarial Anem… | GSE72058 — Activated neutrophils are associated with pediatric cerebral malaria vasculopathy in Malawian child… | GSE255403 — Defining the Inflammation and Immunity Transcriptome in Severe Malarial Anemia for Immunotherapeuti… |
| 4 | GSE1124 — Whole blood transcriptome of childhood malaria | GSE230169 — Blood Transcriptomics Implicate Epigenetics in the Acquisition of Disease Tolerance to Malaria | GSE230169 — Blood Transcriptomics Implicate Epigenetics in the Acquisition of Disease Tolerance to Malaria | GSE83667 — Parasite in vivo blood transcriptomes from Malawian children with cerebral malaria |
| 5 | GSE123750 — U-BIOPRED blood transcriptomics from children with asthma or wheeze | GSE72058 — Activated neutrophils are associated with pediatric cerebral malaria vasculopathy in Malawian child… | GSE1124 — Whole blood transcriptome of childhood malaria | GSE230169 — Blood Transcriptomics Implicate Epigenetics in the Acquisition of Disease Tolerance to Malaria |

### Hybrid facet evidence

| Model | Facet | Scope | Candidates | Top buckets |
|---|---|---|---:|---|
| `bge_small_v15` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:9606 (77), NCBITaxon:5833 (11), NCBITaxon:10090 (6) |
| `bge_small_v15` | `sex_ids` | `candidate_pool` | 100 | PATO:0000383 (31), PATO:0000384 (31) |
| `bge_small_v15` | `assay_categories` | `candidate_pool` | 100 | expression (seq) (49), expression (array) (38), methylation (array) (5) |
| `bge_small_v15` | `assay_labels` | `candidate_pool` | 100 | scRNA-seq (8), spatial transcriptomics (2), 10x Chromium (1) |
| `medcpt_v1` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:9606 (85), NCBITaxon:5833 (8), NCBITaxon:10090 (3) |
| `medcpt_v1` | `sex_ids` | `candidate_pool` | 100 | PATO:0000383 (37), PATO:0000384 (37) |
| `medcpt_v1` | `assay_categories` | `candidate_pool` | 100 | expression (seq) (48), expression (array) (41), methylation (array) (6) |
| `medcpt_v1` | `assay_labels` | `candidate_pool` | 100 | scRNA-seq (4), 10x Chromium (1), bisulfite-seq (1) |
| `qwen3_06b_1024_v1` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:9606 (86), NCBITaxon:10090 (8), NCBITaxon:5833 (4) |
| `qwen3_06b_1024_v1` | `sex_ids` | `candidate_pool` | 100 | PATO:0000383 (32), PATO:0000384 (32) |
| `qwen3_06b_1024_v1` | `assay_categories` | `candidate_pool` | 100 | expression (seq) (57), expression (array) (31), methylation (array) (5) |
| `qwen3_06b_1024_v1` | `assay_labels` | `candidate_pool` | 100 | scRNA-seq (10), 10x Chromium (3), spatial transcriptomics (3) |

## Query: human_tumor_exhausted_t_cells

**Search:** single-cell RNA sequencing of exhausted CD8 T cells in human solid tumors

**Intent:** Find human tumor immune-state scRNA-seq datasets.

**Filters:** `{"assay_labels":["scRNA-seq"],"organism_ids":["NCBITaxon:9606"]}`

### Full hybrid: native RRF (BM25 + dense)

| Rank | BGE | MedCPT | Qwen |
|---:|---|---|---|
| 1 | GSE244433 — Human single cell RNA-sequencing reveals a targetable CD8+ exhausted T cell population that maintai… | GSE244433 — Human single cell RNA-sequencing reveals a targetable CD8+ exhausted T cell population that maintai… | GSE335452 — Single-cell RNA-seq and spatial transcriptomics characterize CD8+ exhausted T cells in pancreatic d… |
| 2 | GSE335452 — Single-cell RNA-seq and spatial transcriptomics characterize CD8+ exhausted T cells in pancreatic d… | GSE185206 — Systematic lineage tracing reveals clonal progenitors and long-term persistence of tumor-specific T… | GSE244433 — Human single cell RNA-sequencing reveals a targetable CD8+ exhausted T cell population that maintai… |
| 3 | GSE194105 — single-cell RNA and TCR sequencing of TRM CD8+ T cells sorted from 8 HGSOC tumors | GSE210264 — BLIMP1 and NR4A3 Transcription Factors Reciprocally Regulate Antitumor CAR T-cell Stemness and Exha… | GSE212797 — Dynamic CD8+ T cell responses to cancer immunotherapy in human regional lymph nodes are disrupted b… |
| 4 | GSE193371 — single-cell RNA and TCR sequencing of TRM and ReCirulating CD8+ T cells sorted from 4 HGSOC tumors | GSE123813 — Clonal replacement of tumor-specific T cells following PD-1 blockade [single cells] | GSE301591 — TGF-βRII/IL-15 Immunotherapeutic complex targets exhausted CD8+ T cell subsets in lymph nodes and t… |
| 5 | GSE172158 — Single-cell RNA-seq of T cells in B-ALL patients reveals an exhausted subset with remarkably hetero… | GSE123812 — Clonal replacement of tumor-specific T cells following PD-1 blockade [bulk RNA] | GSE172158 — Single-cell RNA-seq of T cells in B-ALL patients reveals an exhausted subset with remarkably hetero… |

### Diagnostic components

| Rank | BM25 | BGE dense | MedCPT dense | Qwen dense |
|---:|---|---|---|---|
| 1 | GSE244433 — Human single cell RNA-sequencing reveals a targetable CD8+ exhausted T cell population that maintai… | GSE280433 — Single Cell RNAseq of CD8 positive Lymphocyte T from Lung Tumor in Human patients | GSE211504 — Single‑cell analysis of peripheral CD8+ T cell responses in patients receiving checkpoint blockade… | GSE280433 — Single Cell RNAseq of CD8 positive Lymphocyte T from Lung Tumor in Human patients |
| 2 | GSE194105 — single-cell RNA and TCR sequencing of TRM CD8+ T cells sorted from 8 HGSOC tumors | GSE172158 — Single-cell RNA-seq of T cells in B-ALL patients reveals an exhausted subset with remarkably hetero… | GSE314072 — Functionally heterogeneous intratumoral CD4+CD8+ double positive T cells can give rise to single po… | GSE160243 — Single Cell RNAseq of CD8 positive Lymphocyte T |
| 3 | GSE335452 — Single-cell RNA-seq and spatial transcriptomics characterize CD8+ exhausted T cells in pancreatic d… | GSE335452 — Single-cell RNA-seq and spatial transcriptomics characterize CD8+ exhausted T cells in pancreatic d… | GSE123139 — Dysfunctional CD8+ T cells form a proliferative, dynamically regulated compartment within human mel… | GSE218258 — CD39 Identifies Tumor-Reactive CD8 T cells in Patients With Lung Cancer [scRNA] |
| 4 | GSE124888 — Single-cell RNA sequencing of BCSCs from solid tumors taken from NOD/SCID mice | GSE99254 — T cell landscape of non-small cell lung cancer revealed by deep single-cell RNA sequencing | GSE123813 — Clonal replacement of tumor-specific T cells following PD-1 blockade [single cells] | GSE335452 — Single-cell RNA-seq and spatial transcriptomics characterize CD8+ exhausted T cells in pancreatic d… |
| 5 | GSE193371 — single-cell RNA and TCR sequencing of TRM and ReCirulating CD8+ T cells sorted from 4 HGSOC tumors | GSE244433 — Human single cell RNA-sequencing reveals a targetable CD8+ exhausted T cell population that maintai… | GSE123812 — Clonal replacement of tumor-specific T cells following PD-1 blockade [bulk RNA] | GSE172158 — Single-cell RNA-seq of T cells in B-ALL patients reveals an exhausted subset with remarkably hetero… |

### Hybrid facet evidence

| Model | Facet | Scope | Candidates | Top buckets |
|---|---|---|---:|---|
| `bge_small_v15` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:10090 (59), NCBITaxon:9606 (44), NCBITaxon:9541 (1) |
| `bge_small_v15` | `sex_ids` | `candidate_pool` | 100 | PATO:0000383 (8), PATO:0000384 (6) |
| `bge_small_v15` | `assay_categories` | `candidate_pool` | 100 | expression (seq) (97), other (18), genome binding (seq) (2) |
| `bge_small_v15` | `assay_labels` | `candidate_pool` | 100 | scRNA-seq (50), 10x Chromium (12), Smart-seq2 (7) |
| `medcpt_v1` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:10090 (56), NCBITaxon:9606 (45), NCBITaxon:9541 (1) |
| `medcpt_v1` | `sex_ids` | `candidate_pool` | 100 | PATO:0000383 (6), PATO:0000384 (5) |
| `medcpt_v1` | `assay_categories` | `candidate_pool` | 100 | expression (seq) (94), other (31), genome binding (seq) (5) |
| `medcpt_v1` | `assay_labels` | `candidate_pool` | 100 | scRNA-seq (64), 10x Chromium (18), ATAC-seq (7) |
| `qwen3_06b_1024_v1` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:10090 (70), NCBITaxon:9606 (30), NCBITaxon:9541 (1) |
| `qwen3_06b_1024_v1` | `sex_ids` | `candidate_pool` | 100 | PATO:0000383 (5), PATO:0000384 (4) |
| `qwen3_06b_1024_v1` | `assay_categories` | `candidate_pool` | 100 | expression (seq) (98), other (21), genome binding (seq) (3) |
| `qwen3_06b_1024_v1` | `assay_labels` | `candidate_pool` | 100 | scRNA-seq (49), 10x Chromium (12), ATAC-seq (5) |

## Query: mouse_brain_spatial_injury

**Search:** spatial transcriptomics of mouse hippocampus after traumatic brain injury

**Intent:** Find mouse spatial-expression studies involving brain injury.

**Filters:** `{"organism_ids":["NCBITaxon:10090"]}`

### Full hybrid: native RRF (BM25 + dense)

| Rank | BGE | MedCPT | Qwen |
|---:|---|---|---|
| 1 | GSE214701 — RNA-seq reveals Nup62 as a potential regulator for cell division after traumatic brain injury in mi… | GSE282909 — Widespread and cell-type-specific transcriptomic reorganization following mild traumatic brain inju… | GSE101901 — Single cell sequencing of hippocampus tissues in traumatic brain injury |
| 2 | GSE230253 — The hippocampus on Blast-related Traumatic Brain Injury at Single-cell Resolution | GSE226208 — Shared inflammatory glial cell signature after brain injury, revealed by spatial, temporal and cell… | GSE230253 — The hippocampus on Blast-related Traumatic Brain Injury at Single-cell Resolution |
| 3 | GSE282909 — Widespread and cell-type-specific transcriptomic reorganization following mild traumatic brain inju… | GSE327284 — Heterogenous microglial reactivity contrasts with stable vascular transcriptional programs in mouse… | GSE282909 — Widespread and cell-type-specific transcriptomic reorganization following mild traumatic brain inju… |
| 4 | GSE292715 — Single-Cell Sequencing Reveals the Impact of Mild Traumatic Brain Injury on the Hippocampus | GSE101901 — Single cell sequencing of hippocampus tissues in traumatic brain injury | GSE292715 — Single-Cell Sequencing Reveals the Impact of Mild Traumatic Brain Injury on the Hippocampus |
| 5 | GSE101901 — Single cell sequencing of hippocampus tissues in traumatic brain injury | GSE180862 — Systems spatiotemporal dynamics of traumatic brain injury at single cell resolution | GSE214701 — RNA-seq reveals Nup62 as a potential regulator for cell division after traumatic brain injury in mi… |

### Diagnostic components

| Rank | BM25 | BGE dense | MedCPT dense | Qwen dense |
|---:|---|---|---|---|
| 1 | GSE101901 — Single cell sequencing of hippocampus tissues in traumatic brain injury | GSE282909 — Widespread and cell-type-specific transcriptomic reorganization following mild traumatic brain inju… | GSE282909 — Widespread and cell-type-specific transcriptomic reorganization following mild traumatic brain inju… | GSE282909 — Widespread and cell-type-specific transcriptomic reorganization following mild traumatic brain inju… |
| 2 | GSE284625 — spatial transcriptomics of mouse brain before and after activation | GSE226208 — Shared inflammatory glial cell signature after brain injury, revealed by spatial, temporal and cell… | GSE226208 — Shared inflammatory glial cell signature after brain injury, revealed by spatial, temporal and cell… | GSE236171 — Spatiotemporal trajectory analysis and validation of microglia activation in traumatic brain injury |
| 3 | GSE245683 — ATAC-seq of mouse cortex undergoing early degradation after traumatic brain injury (TBI) | GSE214701 — RNA-seq reveals Nup62 as a potential regulator for cell division after traumatic brain injury in mi… | GSE226211 — Shared inflammatory glial cell signature after brain injury, revealed by spatial, temporal and cell… | GSE230253 — The hippocampus on Blast-related Traumatic Brain Injury at Single-cell Resolution |
| 4 | GSE249918 — Microglia adopt longitudinal transcriptional changes after traumatic brain injury | GSE160763 — Single-cell sequencing of cortical cells following murine traumatic brain injury [scRNA-seq] | GSE180862 — Systems spatiotemporal dynamics of traumatic brain injury at single cell resolution | GSE223066 — Mapping the spatial transcriptomic signature of the hippocampus during memory consolidation |
| 5 | GSE230253 — The hippocampus on Blast-related Traumatic Brain Injury at Single-cell Resolution | GSE230253 — The hippocampus on Blast-related Traumatic Brain Injury at Single-cell Resolution | GSE226838 — Profiling the neuroimmune cascade in 3xTg mice exposed to successive mild traumatic brain injuries | GSE292715 — Single-Cell Sequencing Reveals the Impact of Mild Traumatic Brain Injury on the Hippocampus |

### Hybrid facet evidence

| Model | Facet | Scope | Candidates | Top buckets |
|---|---|---|---:|---|
| `bge_small_v15` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:10090 (67), NCBITaxon:10116 (19), NCBITaxon:9606 (9) |
| `bge_small_v15` | `sex_ids` | `candidate_pool` | 100 | PATO:0000384 (33), PATO:0000383 (6) |
| `bge_small_v15` | `assay_categories` | `candidate_pool` | 100 | expression (seq) (68), other (24), expression (array) (9) |
| `bge_small_v15` | `assay_labels` | `candidate_pool` | 100 | spatial transcriptomics (30), scRNA-seq (17), 10x Chromium (15) |
| `medcpt_v1` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:10090 (73), NCBITaxon:10116 (15), NCBITaxon:9606 (6) |
| `medcpt_v1` | `sex_ids` | `candidate_pool` | 100 | PATO:0000384 (32), PATO:0000383 (8) |
| `medcpt_v1` | `assay_categories` | `candidate_pool` | 100 | expression (seq) (69), other (23), expression (array) (11) |
| `medcpt_v1` | `assay_labels` | `candidate_pool` | 100 | spatial transcriptomics (24), scRNA-seq (21), 10x Chromium (14) |
| `qwen3_06b_1024_v1` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:10090 (66), NCBITaxon:10116 (22), NCBITaxon:9606 (7) |
| `qwen3_06b_1024_v1` | `sex_ids` | `candidate_pool` | 100 | PATO:0000384 (28), PATO:0000383 (6) |
| `qwen3_06b_1024_v1` | `assay_categories` | `candidate_pool` | 100 | expression (seq) (69), other (29), expression (array) (6) |
| `qwen3_06b_1024_v1` | `assay_labels` | `candidate_pool` | 100 | spatial transcriptomics (38), 10x Chromium (15), scRNA-seq (15) |

## Query: crispr_interferon_t_cells

**Search:** CRISPR knockout screen for regulators of interferon response in T cells

**Intent:** Find genetic perturbation screens despite terminology variation.

**Filters:** `{}`

### Full hybrid: native RRF (BM25 + dense)

| Rank | BGE | MedCPT | Qwen |
|---:|---|---|---|
| 1 | GSE140717 — A genome-wide CRISPR activation screen identifies ETV7 as a negative regulator of the interferon re… | GSE144142 — Knockout of CNOT1 and CNOT10 in primary CD4+ T-cells causes a type-1 interferon response | GSE144142 — Knockout of CNOT1 and CNOT10 in primary CD4+ T-cells causes a type-1 interferon response |
| 2 | GSE199813 — CRISPR screens unveil signal hubs for nutrient licensing of T cell immunity [CRISPR screen] | GSE140102 — CRISPR screen in regulatory T cells reveals ubiquitination modulators of Foxp3 stability | GSE140717 — A genome-wide CRISPR activation screen identifies ETV7 as a negative regulator of the interferon re… |
| 3 | GSE288118 — An arrayed CRISPR screen identifies knockout combinations improving antibody productivity in HEK293… | GSE254334 — CRISPR Screen identifies regulators for T cells activities in pancreatic cancer | GSE121710 — CRISPR screen for interferon-alpha inducible inhibitors of yellow fever virus replication |
| 4 | GSE330227 — In vivo genome-wide CRISPR screens in human T cells to enhance T cell therapy for solid tumors | GSE225235 — Genome-wide CRISPR screen to identify CD58 regulators | GSE199813 — CRISPR screens unveil signal hubs for nutrient licensing of T cell immunity [CRISPR screen] |
| 5 | GSE334846 — Discovery of Tcf7 regulators with clonally-resolved CRISPR screens identifies Trim28 as a mediator… | GSE232543 — Identifying gene combinations for targeting innate immune cells to enhance T cells activation [Cebp… | GSE233195 — Interferon inhibits a model RNA virus via a limited set of inducible effector genes [GW_CRISPR_Scre… |

### Diagnostic components

| Rank | BM25 | BGE dense | MedCPT dense | Qwen dense |
|---:|---|---|---|---|
| 1 | GSE254334 — CRISPR Screen identifies regulators for T cells activities in pancreatic cancer | GSE174255 — CRISPR activation and interference screens decode stimulation responses in primary human T cells [C… | GSE190604 — CRISPR activation and interference screens decode stimulation responses in primary human T cells [C… | GSE140717 — A genome-wide CRISPR activation screen identifies ETV7 as a negative regulator of the interferon re… |
| 2 | GSE245496 — CRISPR/Cas9 Transcription Factor Knockout Screen for Regulators of In Vitro Cardiac Differentiation | GSE190846 — CRISPR activation and interference screens decode stimulation responses in primary human T cells [S… | GSE190846 — CRISPR activation and interference screens decode stimulation responses in primary human T cells [S… | GSE174284 — CRISPR activation and interference screens decode stimulation responses in primary human T cells [B… |
| 3 | GSE212008 — Genome-wide pooled CRISPR knockout screen for novel regulators of macrophage efferocytosis. | GSE190604 — CRISPR activation and interference screens decode stimulation responses in primary human T cells [C… | GSE192827 — Genome-wide CRISPR screens decode cancer cell pathways that trigger gamma-delta T cell detection an… | GSE174255 — CRISPR activation and interference screens decode stimulation responses in primary human T cells [C… |
| 4 | GSE144162 — CRISPR screen for NMD regulators | GSE330227 — In vivo genome-wide CRISPR screens in human T cells to enhance T cell therapy for solid tumors | GSE162464 — A genetic screen in macrophages identifies new regulators of IFNg-inducible MHCII that contribute t… | GSE190604 — CRISPR activation and interference screens decode stimulation responses in primary human T cells [C… |
| 5 | GSE246208 — Genome-wide CRISPR/Cas9 knockout screen for novel regulators of mechanical stress on NLRP3 inflamma… | GSE174284 — CRISPR activation and interference screens decode stimulation responses in primary human T cells [B… | GSE154040 — STUB1 dampens IFNγ response in tumors by destabilizing the IFNγ receptor complex | GSE190846 — CRISPR activation and interference screens decode stimulation responses in primary human T cells [S… |

### Hybrid facet evidence

| Model | Facet | Scope | Candidates | Top buckets |
|---|---|---|---:|---|
| `bge_small_v15` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:9606 (67), NCBITaxon:10090 (32), NCBITaxon:9913 (2) |
| `bge_small_v15` | `sex_ids` | `candidate_pool` | 100 |  |
| `bge_small_v15` | `assay_categories` | `candidate_pool` | 100 | other (66), expression (seq) (34), genome binding (seq) (14) |
| `bge_small_v15` | `assay_labels` | `candidate_pool` | 100 | scRNA-seq (15), ATAC-seq (6), ChIP-seq (6) |
| `medcpt_v1` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:9606 (58), NCBITaxon:10090 (38), NCBITaxon:9913 (2) |
| `medcpt_v1` | `sex_ids` | `candidate_pool` | 100 | PATO:0000383 (3), PATO:0000384 (2) |
| `medcpt_v1` | `assay_categories` | `candidate_pool` | 100 | other (55), expression (seq) (37), genome binding (seq) (16) |
| `medcpt_v1` | `assay_labels` | `candidate_pool` | 100 | scRNA-seq (10), ChIP-seq (8), ATAC-seq (7) |
| `qwen3_06b_1024_v1` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:9606 (62), NCBITaxon:10090 (36), NCBITaxon:9913 (2) |
| `qwen3_06b_1024_v1` | `sex_ids` | `candidate_pool` | 100 | PATO:0000383 (2), PATO:0000384 (1) |
| `qwen3_06b_1024_v1` | `assay_categories` | `candidate_pool` | 100 | other (54), expression (seq) (37), genome binding (seq) (18) |
| `qwen3_06b_1024_v1` | `assay_labels` | `candidate_pool` | 100 | ATAC-seq (14), scRNA-seq (10), ChIP-seq (5) |

## Query: rare_disease_fibroblasts

**Search:** fibroblast transcriptomes from patients with rare inherited connective tissue disorders

**Intent:** Find patient-derived rare-disease fibroblast expression datasets.

**Filters:** `{}`

### Full hybrid: native RRF (BM25 + dense)

| Rank | BGE | MedCPT | Qwen |
|---:|---|---|---|
| 1 | GSE218012 — Whole-transcriptome analysis of dermal fibroblasts from patients with hypermobile Ehlers-Danlos syn… | GSE218012 — Whole-transcriptome analysis of dermal fibroblasts from patients with hypermobile Ehlers-Danlos syn… | GSE218012 — Whole-transcriptome analysis of dermal fibroblasts from patients with hypermobile Ehlers-Danlos syn… |
| 2 | GSE109448 — RNA-seq analysis of freshly isolated synovial fibroblast subsets from patients with rheumatoid arth… | GSE294791 — ANTXR2 Deficiency Promotes Cellular Senescence and Chondroid Differentiation in Hyaline Fibromatosi… | GSE69486 — Gene expression analysis of skin fibroblast cells from patients with Bipolar Disorder |
| 3 | GSE270199 — Transcriptomic analysis of fibroblast from 10 Ehlers-Danlos SYndrome (EDS) patients. | GSE69486 — Gene expression analysis of skin fibroblast cells from patients with Bipolar Disorder | GSE143789 — lncRNA and circular RNAs in orbital adipose/connective tissue from patients with thyroid-associated… |
| 4 | GSE83147 — RNA expression profiles in fibroblast-like synoviocytes from rheumatoid arthritis patients and trau… | GSE241724 — B3GALT6 mutations trigger sequential molecular events leading to compromised connective tissue biom… | GSE263294 — Patient-Derived Organoids Recapitulate Pathological Intrinsic and Phenotypic Features of Fibrous Dy… |
| 5 | GSE143789 — lncRNA and circular RNAs in orbital adipose/connective tissue from patients with thyroid-associated… | GSE270199 — Transcriptomic analysis of fibroblast from 10 Ehlers-Danlos SYndrome (EDS) patients. | GSE58038 — Exon Level Expression Profiling: a Novel Unbiased Transcriptome |

### Diagnostic components

| Rank | BM25 | BGE dense | MedCPT dense | Qwen dense |
|---:|---|---|---|---|
| 1 | GSE143789 — lncRNA and circular RNAs in orbital adipose/connective tissue from patients with thyroid-associated… | GSE119501 — Fibroblast RNA-Seq data from three genodermatoses (RDEB, XPC and KS) | GSE218012 — Whole-transcriptome analysis of dermal fibroblasts from patients with hypermobile Ehlers-Danlos syn… | GSE263294 — Patient-Derived Organoids Recapitulate Pathological Intrinsic and Phenotypic Features of Fibrous Dy… |
| 2 | GSE294791 — ANTXR2 Deficiency Promotes Cellular Senescence and Chondroid Differentiation in Hyaline Fibromatosi… | GSE218012 — Whole-transcriptome analysis of dermal fibroblasts from patients with hypermobile Ehlers-Danlos syn… | GSE125990 — Gene expression profiling of fibroblasts in a family with LMNA-related cardiomyopathy reveals molec… | GSE58038 — Exon Level Expression Profiling: a Novel Unbiased Transcriptome |
| 3 | GSE261503 — Limb connective tissue is organized in a continuum of promiscuous fibroblast identities during deve… | GSE263294 — Patient-Derived Organoids Recapitulate Pathological Intrinsic and Phenotypic Features of Fibrous Dy… | GSE304836 — Sequencing of human dermal fibroblasts | GSE215841 — First characterization of the transcriptome of lung fibroblasts of SSc patients and healthy donors… |
| 4 | GSE281559 — Inherited Variant in MRC2 Causes Cardiac Fibroblast Dysfunction and Increases Atrial Fibrillation S… | GSE90514 — Transcriptomic alterations in fibroblasts from Parkinson's disease patients carrying Parkin mutatio… | GSE316460 — RNA-seq analysis for wild-type fibroblasts and patient fibroblasts bearing pathogenic EPG5 mutations | GSE119501 — Fibroblast RNA-Seq data from three genodermatoses (RDEB, XPC and KS) |
| 5 | GSE175399 — Epigenome analysis of adipose/connective tissue from thyroid-associated ophthalmopathy | GSE138669 — Myofibroblast transcriptome indicates SFRP2+ fibroblast progenitors in systemic sclerosis skin | GSE72589 — Transcriptome-wide Quantitative Analysis of XLPDR-derived human dermal fibroblasts with POLA1 defic… | GSE185710 — Cross-tissue, single-cell stromal atlas identifies shared pathological fibroblast phenotypes in fou… |

### Hybrid facet evidence

| Model | Facet | Scope | Candidates | Top buckets |
|---|---|---|---:|---|
| `bge_small_v15` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:9606 (80), NCBITaxon:10090 (14), NCBITaxon:9031 (2) |
| `bge_small_v15` | `sex_ids` | `candidate_pool` | 100 | PATO:0000384 (15), PATO:0000383 (14) |
| `bge_small_v15` | `assay_categories` | `candidate_pool` | 100 | expression (seq) (66), expression (array) (22), genome variation (3) |
| `bge_small_v15` | `assay_labels` | `candidate_pool` | 100 | scRNA-seq (19), 10x Chromium (5), spatial transcriptomics (1) |
| `medcpt_v1` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:9606 (81), NCBITaxon:10090 (13), NCBITaxon:9031 (2) |
| `medcpt_v1` | `sex_ids` | `candidate_pool` | 100 | PATO:0000384 (14), PATO:0000383 (12) |
| `medcpt_v1` | `assay_categories` | `candidate_pool` | 100 | expression (seq) (64), expression (array) (19), genome binding (seq) (3) |
| `medcpt_v1` | `assay_labels` | `candidate_pool` | 100 | scRNA-seq (10), ATAC-seq (3), 10x Chromium (2) |
| `qwen3_06b_1024_v1` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:9606 (71), NCBITaxon:10090 (22), NCBITaxon:9031 (3) |
| `qwen3_06b_1024_v1` | `sex_ids` | `candidate_pool` | 100 | PATO:0000384 (15), PATO:0000383 (13) |
| `qwen3_06b_1024_v1` | `assay_categories` | `candidate_pool` | 100 | expression (seq) (69), expression (array) (18), other (3) |
| `qwen3_06b_1024_v1` | `assay_labels` | `candidate_pool` | 100 | scRNA-seq (17), 10x Chromium (5), spatial transcriptomics (1) |

## Query: ribosome_er_stress

**Search:** ribosome profiling during endoplasmic reticulum stress

**Intent:** Find Ribo-seq or ribosome-footprinting stress experiments.

**Filters:** `{}`

### Full hybrid: native RRF (BM25 + dense)

| Rank | BGE | MedCPT | Qwen |
|---:|---|---|---|
| 1 | GSE208391 — RNA sequencing of HCC cells after endoplasmic reticulum stress induced by tunicamycin | GSE296877 — Unexpected combination of increased protein translation and decreased endoplasmic reticulum stress… | GSE150058 — Transcriptional changes associated with endoplasmic reticulum stress in the eye imaginal disc of Dr… |
| 2 | GSE233555 — Calibrated ribosome profiling assesses the dynamics of ribosomal flux on transcripts | GSE201134 — EMD37 triggers endoplasmic reticulum stress in cancer cells | GSE115161 — Regulation of Translation Elongation Revealed by Ribosome Profiling [Dataset_4] |
| 3 | GSE99763 — Lipid bilayer stress-activated IRE-1 modulates autophagy during endoplasmic reticulum stress | GSE254349 — G3BP2B stress granules regulate mRNA expression under ER stress [RNAseq_Exp1] | GSE99763 — Lipid bilayer stress-activated IRE-1 modulates autophagy during endoplasmic reticulum stress |
| 4 | GSE201134 — EMD37 triggers endoplasmic reticulum stress in cancer cells | GSE124561 — Epithelial endoplasmic reticulum stress orchestrates a protective IgA response I | GSE115162 — Regulation of Translation Elongation Revealed by Ribosome Profiling |
| 5 | GSE85540 — A ribosome profiling study of mRNA cleavage by the endonuclease RelE | GSE81792 — Transcriptome response to endoplasmic reticulum stress in rat oligodendrocyte precursor cells | GSE201134 — EMD37 triggers endoplasmic reticulum stress in cancer cells |

### Diagnostic components

| Rank | BM25 | BGE dense | MedCPT dense | Qwen dense |
|---:|---|---|---|---|
| 1 | GSE99763 — Lipid bilayer stress-activated IRE-1 modulates autophagy during endoplasmic reticulum stress | GSE233555 — Calibrated ribosome profiling assesses the dynamics of ribosomal flux on transcripts | GSE90070 — A unique ISR Program Determines Cellular Responses to Chronic Stress | GSE115161 — Regulation of Translation Elongation Revealed by Ribosome Profiling [Dataset_4] |
| 2 | GSE201134 — EMD37 triggers endoplasmic reticulum stress in cancer cells | GSE85540 — A ribosome profiling study of mRNA cleavage by the endonuclease RelE | GSE68265 — Dissection of the translational impacts of the PERK pathway | GSE115162 — Regulation of Translation Elongation Revealed by Ribosome Profiling |
| 3 | GSE301955 — Endoplasmic Reticulum Stress-Driven Nucleotide Catabolism Fuels Prostate Cancer | GSE200491 — Ribosome profiling and RNA-seq of an acute glucose starvation timecourse and 5 day growth course in… | GSE129757 — Whole transcriptome profiles and ribosome profiling of thapsigargin- and tunicamycin-treated LN308… | GSE115160 — Regulation of Translation Elongation Revealed by Ribosome Profiling [Dataset_3] |
| 4 | GSE296877 — Unexpected combination of increased protein translation and decreased endoplasmic reticulum stress… | GSE53743 — Translational profiling in the unfolded protein response | GSE113171 — Mapping multi-layered regulation in response to environmental stress | GSE115158 — Regulation of Translation Elongation Revealed by Ribosome Profiling [Dataset_1] |
| 5 | GSE61949 — Genome-wide analysis of the endoplasmic reticulum stress response during lignocellulase synthesis i… | GSE64488 — High Precision Analysis of Translational Pausing in Bacteria Lacking EFP by Ribosome Profiling | GSE53743 — Translational profiling in the unfolded protein response | GSE115159 — Regulation of Translation Elongation Revealed by Ribosome Profiling [Dataset_2] |

### Hybrid facet evidence

| Model | Facet | Scope | Candidates | Top buckets |
|---|---|---|---:|---|
| `bge_small_v15` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:9606 (42), NCBITaxon:10090 (38), NCBITaxon:4932 (13) |
| `bge_small_v15` | `sex_ids` | `candidate_pool` | 100 | PATO:0000384 (8), PATO:0000383 (1) |
| `bge_small_v15` | `assay_categories` | `candidate_pool` | 100 | expression (seq) (72), other (36), expression (array) (12) |
| `bge_small_v15` | `assay_labels` | `candidate_pool` | 100 | Ribo-seq (46), CUT&RUN (2), 10x Chromium (1) |
| `medcpt_v1` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:9606 (51), NCBITaxon:10090 (33), NCBITaxon:4932 (6) |
| `medcpt_v1` | `sex_ids` | `candidate_pool` | 100 | PATO:0000384 (8), PATO:0000383 (2) |
| `medcpt_v1` | `assay_categories` | `candidate_pool` | 100 | expression (seq) (73), other (20), expression (array) (14) |
| `medcpt_v1` | `assay_labels` | `candidate_pool` | 100 | Ribo-seq (27), CUT&RUN (2), 10x Chromium (1) |
| `qwen3_06b_1024_v1` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:9606 (37), NCBITaxon:10090 (36), NCBITaxon:4932 (14) |
| `qwen3_06b_1024_v1` | `sex_ids` | `candidate_pool` | 100 | PATO:0000384 (7), PATO:0000383 (1) |
| `qwen3_06b_1024_v1` | `assay_categories` | `candidate_pool` | 100 | expression (seq) (71), other (39), expression (array) (11) |
| `qwen3_06b_1024_v1` | `assay_labels` | `candidate_pool` | 100 | Ribo-seq (46), CUT&RUN (2), 10x Chromium (1) |

## Query: airway_viral_infection

**Search:** airway epithelial response to respiratory viral infection

**Intent:** Find airway infection-response datasets across virus names.

**Filters:** `{}`

### Full hybrid: native RRF (BM25 + dense)

| Rank | BGE | MedCPT | Qwen |
|---:|---|---|---|
| 1 | GSE138167 — RNA sequencing of primary bronchial airway epithelial cells from young children with and without CF… | GSE138167 — RNA sequencing of primary bronchial airway epithelial cells from young children with and without CF… | GSE138167 — RNA sequencing of primary bronchial airway epithelial cells from young children with and without CF… |
| 2 | GSE89880 — Age-associated Changes in the Respiratory Epithelial Response to Influenza Infection [hNEC] | GSE89882 — Age-associated Changes in the Respiratory Epithelial Response to Influenza Infection | GSE184384 — Epithelial Plasticity and Innate Immune Activation Promote Lung Tissue Remodeling following Respira… |
| 3 | GSE89881 — Age-associated Changes in the Respiratory Epithelial Response to Influenza Infection [biopsy] | GSE146532 — RNA-sequencing of bronchial epithelial cells from an adult cohort including asthmatics, COPD and he… | GSE89882 — Age-associated Changes in the Respiratory Epithelial Response to Influenza Infection |
| 4 | GSE146532 — RNA-sequencing of bronchial epithelial cells from an adult cohort including asthmatics, COPD and he… | GSE279463 — Human Primary Airway Epithelium Response to HCoV-229E Infection | GSE262298 — Identification and Targeting of Regulators of SARS-CoV-2-Host interactions in the Airway Epithelium… |
| 5 | GSE189537 — TGF-beta treated airway epithelium | GSE184384 — Epithelial Plasticity and Innate Immune Activation Promote Lung Tissue Remodeling following Respira… | GSE146532 — RNA-sequencing of bronchial epithelial cells from an adult cohort including asthmatics, COPD and he… |

### Diagnostic components

| Rank | BM25 | BGE dense | MedCPT dense | Qwen dense |
|---:|---|---|---|---|
| 1 | GSE262298 — Identification and Targeting of Regulators of SARS-CoV-2-Host interactions in the Airway Epithelium… | GSE61141 — Phenotypic responses of differentiated asthmatic human airway epithelial cultures to rhinovirus | GSE138167 — RNA sequencing of primary bronchial airway epithelial cells from young children with and without CF… | GSE138167 — RNA sequencing of primary bronchial airway epithelial cells from young children with and without CF… |
| 2 | GSE262299 — Identification and Targeting of Regulators of SARS-CoV-2-Host interactions in the Airway Epithelium… | GSE138167 — RNA sequencing of primary bronchial airway epithelial cells from young children with and without CF… | GSE146532 — RNA-sequencing of bronchial epithelial cells from an adult cohort including asthmatics, COPD and he… | GSE286901 — Rhinovirus triggers distinct host responses through differential engagement of epithelial innate im… |
| 3 | GSE89882 — Age-associated Changes in the Respiratory Epithelial Response to Influenza Infection | GSE146532 — RNA-sequencing of bronchial epithelial cells from an adult cohort including asthmatics, COPD and he… | GSE286262 — Single cell profiling to determine influence of wheeze and early-life viral infection on developmen… | GSE286616 — Rhinovirus triggers distinct host responses through differential engagement of epithelial innate im… |
| 4 | GSE89880 — Age-associated Changes in the Respiratory Epithelial Response to Influenza Infection [hNEC] | GSE206680 — RHINOVIRUS INFECTION OF THE AIRWAY EPITHELIUM ENHANCES MAST CELL IMMUNE RESPONSES VIA EPITHELIAL-DE… | GSE279463 — Human Primary Airway Epithelium Response to HCoV-229E Infection | GSE61141 — Phenotypic responses of differentiated asthmatic human airway epithelial cultures to rhinovirus |
| 5 | GSE89881 — Age-associated Changes in the Respiratory Epithelial Response to Influenza Infection [biopsy] | GSE296526 — Inflammatory, transcriptomic and cell fate responses underlying the mammalian transmission of avian… | GSE71766 — A Systems Approach to Understanding Human Rhinovirus and Influenza Virus Infection | GSE146532 — RNA-sequencing of bronchial epithelial cells from an adult cohort including asthmatics, COPD and he… |

### Hybrid facet evidence

| Model | Facet | Scope | Candidates | Top buckets |
|---|---|---|---:|---|
| `bge_small_v15` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:9606 (64), NCBITaxon:10090 (26), NCBITaxon:9541 (5) |
| `bge_small_v15` | `sex_ids` | `candidate_pool` | 100 | PATO:0000383 (11), PATO:0000384 (8) |
| `bge_small_v15` | `assay_categories` | `candidate_pool` | 100 | expression (seq) (71), expression (array) (23), other (8) |
| `bge_small_v15` | `assay_labels` | `candidate_pool` | 100 | scRNA-seq (18), 10x Chromium (3), ATAC-seq (1) |
| `medcpt_v1` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:9606 (79), NCBITaxon:10090 (19), NCBITaxon:7955 (1) |
| `medcpt_v1` | `sex_ids` | `candidate_pool` | 100 | PATO:0000383 (19), PATO:0000384 (19) |
| `medcpt_v1` | `assay_categories` | `candidate_pool` | 100 | expression (seq) (80), expression (array) (14), other (7) |
| `medcpt_v1` | `assay_labels` | `candidate_pool` | 100 | scRNA-seq (18), 10x Chromium (2), ATAC-seq (1) |
| `qwen3_06b_1024_v1` | `organism_ids` | `candidate_pool` | 100 | NCBITaxon:9606 (65), NCBITaxon:10090 (25), NCBITaxon:9541 (5) |
| `qwen3_06b_1024_v1` | `sex_ids` | `candidate_pool` | 100 | PATO:0000383 (12), PATO:0000384 (10) |
| `qwen3_06b_1024_v1` | `assay_categories` | `candidate_pool` | 100 | expression (seq) (73), expression (array) (21), other (6) |
| `qwen3_06b_1024_v1` | `assay_labels` | `candidate_pool` | 100 | scRNA-seq (15), 10x Chromium (2), ATAC-seq (1) |

## Pairwise overlap@5

| Query | Mode | BGE/MedCPT | BGE/Qwen | MedCPT/Qwen |
|---|---|---:|---:|---:|
| `control_childhood_malaria` | dense | 3 | 4 | 2 |
| `control_childhood_malaria` | hybrid | 4 | 4 | 4 |
| `human_tumor_exhausted_t_cells` | dense | 0 | 3 | 0 |
| `human_tumor_exhausted_t_cells` | hybrid | 1 | 3 | 1 |
| `mouse_brain_spatial_injury` | dense | 2 | 2 | 1 |
| `mouse_brain_spatial_injury` | hybrid | 2 | 5 | 2 |
| `crispr_interferon_t_cells` | dense | 2 | 4 | 2 |
| `crispr_interferon_t_cells` | hybrid | 0 | 2 | 1 |
| `rare_disease_fibroblasts` | dense | 1 | 2 | 0 |
| `rare_disease_fibroblasts` | hybrid | 2 | 2 | 2 |
| `ribosome_er_stress` | dense | 1 | 0 | 0 |
| `ribosome_er_stress` | hybrid | 1 | 2 | 1 |
| `airway_viral_infection` | dense | 2 | 3 | 2 |
| `airway_viral_infection` | hybrid | 2 | 2 | 4 |

> This is a qualitative live smoke comparison, not a relevance judgment or model-selection result.
