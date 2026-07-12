---
title: Roadmap & Milestones
tags: [roadmap, plan, milestones]
---

# 40 · Roadmap & Milestones

> **Primary-path cutover (2026-07-12):** Elasticsearch plus
> `gemini_embedding_2_3072_v1` (3,072 dimensions) is the current target.
> `geo-soft-etl` owns canonical materialization, paid Gemini embedding, bulk
> indexing, and audit as one fail-closed Prefect run. PostgreSQL milestones
> remain recorded as completed historical experiments, not deployment work.

← [[Home]] · decisions in [[41-Open-Questions]]

Framing: this is a **spike**. Optimize for learning speed and a demoable end-to-end thread, not durability.

## Recommended v1 scope

- **Corpus:** the 222,961-GSE GEOmetadb snapshot remains the measured baseline.
  The crawler has already accumulated more than 244k stripped family SOFT files;
  the current task turns those into one canonical per-GSE record tree through
  existence-based Prefect ETL.
- **Unit:** **series (GSE)**, not samples. Aggregate sample fields up to the series.
- **Fields normalized:** `sex` (PATO IDs), `organism` (NCBITaxon IDs), and
  **`assay`** (controlled category/detail labels; EFO grounding is deferred).
  Tissue is the next bounded experiment, not a prerequisite for this tranche.
- **Search:** one local Elasticsearch 9.4.2 container for BM25, dense vectors,
  filters, facets, and native RRF. The working Postgres/ParadeDB implementation
  remains the parity baseline; the same scripts later point at a managed host. →
  [[51-Search-Database-Bakeoff-and-Elasticsearch-Plan]]
- **Facets:** organism, sex, assay category, and assay detail first. Tissue and
  hierarchy follow the tissue decision gate.
- **Serve:** invite-only remote MCP server
  (`search_datasets`, `get_dataset`, `facet_values`) over Streamable HTTP.
- **Eval:** start with a 16-query pooled human review; expand only after it proves
  useful.

## Immediate non-tissue workstreams

1. [[44-Normalization-Tests-and-Assay-Hardening-Plan|Tests + assay hardening]] —
   **implemented in code**; the remaining deployment step is the targeted
   refresh of the three persisted assay columns.
2. [[45-Normalized-Filters-and-Facets-Plan|Normalized filters + facets]] —
   **implemented in code**, including filtered BM25/dense/hybrid retrieval,
   disjunctive counts, and API exposure. Creating the four optional GIN indexes
   on the shared database remains an explicit database-change step.
3. [[53-Prefect-SOFT-ETL-and-Embedding-Prototype-Plan|Prefect SOFT ETL]] — parse
   **implemented and extended**: parse missing stripped SOFT outputs, build the
   Gemini artifact, load Elasticsearch, and audit coverage in one required run.
4. [[46-Retrieval-Evaluation-Plan|Mini retrieval evaluation]] — pool BM25, dense,
   and hybrid results for 16 fixed queries and measure Recall@20, NDCG@10, and
   MRR@20 with reviewed qrels.
5. [[47-MCP-Server-Plan|Private remote MCP server]] — expose search, exact GSE
   lookup, and facet discovery over authenticated Streamable HTTP after Track 2's
   contract is stable.
6. [[52-Embedding-Bakeoff-Runbook|Alternate embedding bake-off]] — compare BM25
   with BGE, MedCPT, Qwen, and full-dimension Gemini dense/hybrid pipelines using
   provider-neutral artifacts and reviewed GEO qrels.
7. [[51-Search-Database-Bakeoff-and-Elasticsearch-Plan|Local Elasticsearch]] —
   **implemented as the primary local path** with GSE-keyed upserts, Gemini
   3,072-dimensional vectors, retrieval, filters, and facets. Managed
   provisioning and alias lifecycle remain future work.

Dependencies: the historical Postgres tracks, canonical contract, Prefect ETL,
and local Elasticsearch adapter have landed. Model-quality promotion still
depends on reviewed qrels; managed deployment and tissue mapping are separate.

## Phased plan

### Phase 0 — Foundations (½–1 wk)
- [x] Postgres baseline up (ParadeDB with pgvector + pg_search); 222,961 series loaded.
- [x] Build and validate the local Elasticsearch-only replacement and switch
  the application default. → [[51-Search-Database-Bakeoff-and-Elasticsearch-Plan]]
- [x] Ingestion skeleton hitting `esearch`/`esummary` (JSON) → `geo-fetch-summaries`.
- [x] Land the corpus — **chose GEOmetadb bulk over a crawl**: 222,961 series into `data/processed/geo_series.jsonl`. → [[42-Build-Log]]
- [x] Materialize downloaded stripped SOFT into canonical per-GSE records,
  build Gemini storage, and load/audit Elasticsearch. →
  [[53-Prefect-SOFT-ETL-and-Embedding-Prototype-Plan]]
- [ ] **Build the eval set** (seed queries + pooled judgments). →
  [[46-Retrieval-Evaluation-Plan]]

> **Progress beyond Phase 0:** already embedded all 223k (local `bge-small`) and validated in-memory semantic search — normally Phase 1 work. Full log: [[42-Build-Log]].

### Phase 1 — Search baseline (1 wk)
- [x] Build embedding doc; embed all 222,961 series locally with
  `bge-small-en-v1.5`.
- [x] HNSW + BM25 indexes; RRF hybrid query. → [[26-Datastore-Postgres]]
- [ ] Measure the fixed conceptual/cross-vocabulary cases, including single-cell
  and spatial-transcriptomics queries, rather than assuming the single-cell
  keyword story is the main win. → [[46-Retrieval-Evaluation-Plan]]
- [ ] Run the measured baseline before choosing another embedding model. →
  [[46-Retrieval-Evaluation-Plan]]
- [ ] Build the side-by-side MedCPT/Qwen/Gemini candidates, re-pool all nine
  retrieval systems, and choose from measured evidence. →
  [[52-Embedding-Bakeoff-Runbook]]

### Phase 2 — Normalization + facets (1–2 wks)
- [x] Populate `sex`, `organism`, and `assay` columns for the full database. →
  [[22-Ontology-Normalization]]
- [x] Harden assay matching and add the targeted refresh command. →
  [[44-Normalization-Tests-and-Assay-Hardening-Plan]], [[42-Build-Log]]
- [ ] Run the targeted assay-column refresh on the shared database after
  explicit database-change approval.
- [x] Add normalized filters, disjunctive facet counts, and API exposure. →
  [[45-Normalized-Filters-and-Facets-Plan]], [[42-Build-Log]]
- [ ] Apply the four optional GIN indexes to the shared database after explicit
  database-change approval.
- [ ] Materialize ancestor arrays only after an ontology-backed field needs
  hierarchy. → [[24-Faceted-Search]]
- [ ] After the model bake-off, run normalized-label injection as a separate
  document-composition ablation; do not confound the current model comparison.
- [ ] Measure normalization precision/coverage vs. hand labels.

### Phase 3 — MCP + demo (1 wk)
- [ ] Invite-only remote FastMCP server exposing the three stable v1 tools over
  authenticated Streamable HTTP. →
  [[47-MCP-Server-Plan]]
- [ ] Drive it from Claude: expansion → search → drill-in → summary.
- [ ] Load newly materialized SOFT records into local Elasticsearch with
  idempotent GSE-keyed upserts; do not add cloud provisioning to the demo.

### Later / v2 (not the spike)
- Content-hashed records, immutable daily manifests, reusable vector deltas,
  source-change detection, and versioned ES alias releases. →
  [[54-Incremental-Corpus-Future-State]]
- Sample-level (GSM) indexing — 8.6M docs; the real scale decision (consider
  `pgvectorscale` / StreamingDiskANN and benchmark `pdb.agg` against current SQL
  facets). It is also the *correctness* fix for within-sample multi-field
  filtering (the [[24-Faceted-Search|series-aggregation caveat]]), not just
  scale. → [[26-Datastore-Postgres#Scale headroom]]
- **Next normalization experiment:** ontology-derived deterministic candidates for `tissue`, with bounded LLM validation and a 100–200-value review set. → [[43-Tissue-Candidate-Generation-Plan]]
- More complex fields after the tissue decision gate (disease, cell type, dev stage, ethnicity).
- Server-side cross-encoder reranking (MedCPT / bge-reranker).
- Incremental refresh cron (idempotent ingest already supports it).
- Human UI (if ever needed beyond MCP).
- Public/anonymous MCP access, self-service invitations, and multi-tenant
  administration.

## Definition of done (spike)

A recorded thread where an invited user, through the hosted MCP endpoint, asks
a conceptual/cross-vocabulary question such as *"find spatially resolved gene
expression studies in tissue sections and summarize the assay mix"*—returning
real GSEs, a faceted breakdown, and a grounded cited summary. Keep the
single-cell query as one eval case, not the headline claim.

## Rough effort

Treat earlier week estimates as historical planning guesses. The full
222,961-series snapshot is already indexed; remaining effort is evaluation,
deployment, and optional freshness—not a first crawl.
