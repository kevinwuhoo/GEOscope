---
title: Roadmap & Milestones
tags: [roadmap, plan, milestones]
---

# 40 · Roadmap & Milestones

← [[Home]] · decisions in [[41-Open-Questions]]

Framing: this is a **spike**. Optimize for learning speed and a demoable end-to-end thread, not durability.

## Recommended v1 scope

- **Corpus:** all 222,961 GSEs in the chosen GEOmetadb snapshot are already
  indexed. A metadata-only top-up from its 2024-02-29 cutoff to current GEO is a
  later freshness task, not a slice-vs-full decision.
- **Unit:** **series (GSE)**, not samples. Aggregate sample fields up to the series.
- **Fields normalized:** `sex` (PATO IDs), `organism` (NCBITaxon IDs), and
  **`assay`** (controlled category/detail labels; EFO grounding is deferred).
  Tissue is the next bounded experiment, not a prerequisite for this tranche.
- **Search:** hybrid (pgvector + pg_search BM25 + RRF); the LLM client owns v1
  synonym/query expansion. Deterministic ontology expansion is v2+.
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
3. [[46-Retrieval-Evaluation-Plan|Mini retrieval evaluation]] — pool BM25, dense,
   and hybrid results for 16 fixed queries and measure Recall@20, NDCG@10, and
   MRR@20 with reviewed qrels.
4. [[47-MCP-Server-Plan|Private remote MCP server]] — expose search, exact GSE
   lookup, and facet discovery over authenticated Streamable HTTP after Track 2's
   contract is stable.
5. [[48-Alternate-Embedding-Bakeoff|Alternate embedding bake-off]] — preserve the
   current BGE column, add MedCPT and Qwen columns, and compare all three through
   [[49-Alternate-Embedding-Bakeoff-Implementation-Plan|a reproducible build/load/eval plan]].

Dependencies: Track 1 and Track 2 have landed in code, so Track 3 and Track 4
can proceed independently. The persisted assay refresh affects assay-label
quality but does not block their scaffolding. Alternate-embedding infrastructure can be built in parallel, but
promotion depends on Track 3's reviewed qrels. None of these tracks needs to wait
for tissue mapping.

## Phased plan

### Phase 0 — Foundations (½–1 wk)
- [x] Postgres up (ParadeDB with pgvector + pg_search); 222,961 series loaded.
- [x] Ingestion skeleton hitting `esearch`/`esummary` (JSON) → `geo-fetch-summaries`.
- [x] Land the corpus — **chose GEOmetadb bulk over a crawl**: 222,961 series into `data/processed/geo_series.jsonl`. → [[42-Build-Log]]
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
- [ ] Build the side-by-side MedCPT/Qwen candidates, re-pool, and choose from
  measured evidence. → [[48-Alternate-Embedding-Bakeoff]],
  [[49-Alternate-Embedding-Bakeoff-Implementation-Plan]]

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
- [ ] Top up post-2024 GEO records only after the fixed-corpus eval/model choice,
  then rebuild and sanity-check counts as a distinct freshness release.

### Later / v2 (not the spike)
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
