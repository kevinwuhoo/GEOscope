---
title: Embeddings & Cost
tags: [embeddings, cost, eval, models]
---

# 25 · Embeddings & Cost

← [[Home]] · production runbook: [[57-Canonical-Production-Pipeline]] · feeds
[[23-Search-and-Retrieval]]

## TL;DR

> **Production decision:** use only `gemini_embedding_2_3072_v1` at 3,072
> dimensions, stored in Elasticsearch as `embedding_gemini_3072`. Corpus
> generation uses the Gemini Batch API. BGE, MedCPT, and Qwen are
> development/evaluation only and must remain outside the production artifact
> root. The comparison material below is retained as experiment history. →
> [[57-Canonical-Production-Pipeline]], [[48-Alternate-Embedding-Bakeoff]]

## Production artifact and cost

The production artifact lives at
`data/processed/embedding_artifacts/gemini_embedding_2_3072_v1/`. Its required
files are `vectors.npy`, `ids.json`, and `metadata.json`; Batch request, result,
and state files preserve provider provenance and resume safety.

The completed 2026-07-12 delta encoded 39,168 new records in 40 successful
Batch shards and reused 249,736 existing Gemini rows. The conservative bound
was 95,459,736 tokens / `$9.5460`; Google did not return per-row token counts,
so the actual charge must be read from billing rather than reported as zero.

## Development/evaluation model history

The remaining sections describe the earlier local-model bake-off. They do not
select, configure, or constrain the canonical production pipeline.

## The document we embed (series-level)

The implemented `compose_embed_text` freezes one string per GSE from `title`,
study `type`, raw organism names, `summary`, `overall_design`, molecule names,
distinct sample `source_name` values, and aggregated sample `characteristics`.
The original BGE artifact was built **before** database normalization, so it does
not contain normalized assay/tissue/disease labels or ancestor terms.

The BGE/MedCPT/Qwen bake-off deliberately keeps that exact committed
`geo_series.jsonl` document and GSE order for every model. Injecting normalized
labels may be valuable, but it is a separate post-selection document ablation;
changing it now would confound model and document quality. Token/truncation
statistics are measured per variant instead of estimated.

> **One embedding per document, not one per field.** Categorical fields (organism, sex, assay…) are served by facets/filters, not embeddings; only the narrative bucket gets embedded, and one concatenated vector is the v1 default. The full reasoning + when to split into multi-vector is [[28-Embedding-Granularity]].

## Historical local-model cost and storage

- **API spend:** none for BGE, MedCPT, or Qwen because all three are self-hosted.
- **Compute:** not assumed free; record artifact build seconds, device, worker
  RSS, and query-encoding median/p95 in the bake-off.
- **Storage:** for the current 222,961 rows, the two new typed `vector` columns
  add about 1.49 GiB of raw payload before HNSW/table overhead, using pgvector's
  documented `4 * dimensions + 8` formula
  ([vector type](https://github.com/pgvector/pgvector#vector-type)). Measure
  actual table/index bytes before promotion. →
  [[48-Alternate-Embedding-Bakeoff#Storage budget]]
- **Future sample-level scale:** 8.6M documents is an infrastructure and
  correctness decision, not part of this series-level bake-off. → [[40-Roadmap]]

## Model candidates & when each wins

**Document retrieval (query → experiment summaries):**
- **MedCPT** (NCBI) — a paired article/query encoder trained for biomedical
  retrieval; the released encoders share a 768-dimensional space and use
  model-native article/query input contracts
  ([article model card](https://huggingface.co/ncbi/MedCPT-Article-Encoder),
  [query model card](https://huggingface.co/ncbi/MedCPT-Query-Encoder)).
- **Qwen3-Embedding-0.6B** — the approved general open contender, evaluated at
  its full 1,024 dimensions with a query instruction and unprompted documents
  ([official model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)).

**Ontology-term / entity matching (for [[22-Ontology-Normalization]], NOT document search):**
- **SapBERT** / **BioLORD-2023** — tuned for short biomedical entity/synonym matching. Do **not** use these for document retrieval, and don't use document embedders for fine-grained ontology matching — opposite optimization targets.

## Historical evaluation path

1. **Measure the existing `bge-small-en-v1.5` index first** — all 222,961
   series are already embedded, so it is the honest zero-incremental-cost baseline.
2. Build **MedCPT 768** and **Qwen3-Embedding-0.6B 1,024** into separate typed
   columns without replacing the baseline.
3. Re-pool all seven BM25/dense/hybrid systems against the same reviewed qrels.
4. Promote only from measured quality, latency, truncation, and storage evidence.

The approved proposal and executable plan are
[[48-Alternate-Embedding-Bakeoff]] and
[[49-Alternate-Embedding-Bakeoff-Implementation-Plan]].

## Eval — the thing worth building

Small, honest, reusable. Start with the 16 fixed pooled cases in
[[46-Retrieval-Evaluation-Plan]], then expand only if the first review changes a
decision.

- **Seed queries from the pain:** include “single cell RNA,” but emphasize the
  measured conceptual/cross-vocabulary cases such as spatial expression in
  tissue sections, CRISPR perturbation, and accession/gene-symbol exact hits.
- **Metrics:** Recall@20, NDCG@10, MRR. For normalization: precision + coverage per field vs. a hand-labeled sample (MetaSRA-style; expect high precision, watch recall).
- **What it decides:** embedding model; hybrid fuse-vs-route ([[23-Search-and-Retrieval]]); whether reranking earns its latency; the normalization confidence threshold `τ` ([[22-Ontology-Normalization]]).
- Build the measured baseline before changing models. →
  [[46-Retrieval-Evaluation-Plan]], then run the side-by-side follow-on in
  [[49-Alternate-Embedding-Bakeoff-Implementation-Plan]].

## Sources

- BGE small v1.5 — https://huggingface.co/BAAI/bge-small-en-v1.5
- MedCPT (Query / Article / paper) — https://huggingface.co/ncbi/MedCPT-Query-Encoder · https://huggingface.co/ncbi/MedCPT-Article-Encoder · https://academic.oup.com/bioinformatics/article/39/11/btad651/7335842
- Qwen3-Embedding-0.6B — https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
- Entity-matching embedders (SapBERT / BioLORD, for normalization not doc search) — https://aclanthology.org/2021.naacl-main.334/ · https://huggingface.co/FremyCompany/BioLORD-2023
- text2term TF-IDF benchmark — https://academic.oup.com/database/article/doi/10.1093/database/baae119/7912353
- pgvector vector storage formula — https://github.com/pgvector/pgvector#vector-type
