---
title: Roadmap & Milestones
tags: [roadmap, plan, milestones]
---

# 40 · Roadmap & Milestones

← [[Home]] · decisions in [[41-Open-Questions]]

Framing: this is a **spike**. Optimize for learning speed and a demoable end-to-end thread, not durability.

## Recommended v1 scope

- **Corpus:** you chose *all of GEO*. For the very first crawl, I recommend starting with a **scoped slice (human + mouse, RNA-seq/scRNA-seq)** to iterate fast, then widening to the full ~289k once the pipeline is proven. Same code, smaller `esearch` term. (This is an [[41-Open-Questions|open question]] — full-first is fine too, just slower to iterate.)
- **Unit:** **series (GSE)**, not samples. ~289k docs. Aggregate sample fields up to the series.
- **Fields normalized:** `sex` (PATO IDs), `organism` (NCBITaxon IDs), and
  **`assay`** (controlled category/detail labels; EFO grounding is deferred).
  Tissue is the next bounded experiment, not a prerequisite for this tranche.
- **Search:** hybrid (pgvector + pg_search BM25 + RRF) with ontology-aware expansion.
- **Facets:** organism, sex, assay category, and assay detail first. Tissue and
  hierarchy follow the tissue decision gate.
- **Serve:** MCP server (`search_datasets`, `get_dataset`, `facet_values`).
- **Eval:** start with a 16-query pooled human review; expand only after it proves
  useful.

## Immediate non-tissue workstreams

1. [[44-Normalization-Tests-and-Assay-Hardening-Plan|Tests + assay hardening]] —
   add the test foundation, fix broad 10x/chromium matching, then refresh only the
   three persisted assay columns.
2. [[45-Normalized-Filters-and-Facets-Plan|Normalized filters + facets]] — the
   organism/sex/assay arrays are **already populated**; add GIN indexes, filtered
   retrieval, disjunctive counts, and API exposure.
3. [[46-Retrieval-Evaluation-Plan|Mini retrieval evaluation]] — pool BM25, dense,
   and hybrid results for 16 fixed queries and measure Recall@20, NDCG@10, and
   MRR@20 with reviewed qrels.
4. [[47-MCP-Server-Plan|Local MCP server]] — expose search, exact GSE lookup, and
   facet discovery over stdio after Track 2's contract is stable.

Dependencies: Track 1 precedes assay facets; Track 2 unlocks the three filtered
evaluation cases and MCP. Track 3 and Track 4 are otherwise independent once
Track 2 lands, so neither needs to wait for tissue mapping.

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
- [ ] Prove: **"single cell RNA" retrieves 10x/Drop-seq/SPLiT-seq** studies. (Even pre-normalization, via dense + expansion.)
- [ ] Run the measured baseline before choosing another embedding model. →
  [[46-Retrieval-Evaluation-Plan]]

### Phase 2 — Normalization + facets (1–2 wks)
- [x] Populate `sex`, `organism`, and `assay` columns for the full database. →
  [[22-Ontology-Normalization]]
- [ ] Harden assay matching and refresh its persisted columns. →
  [[44-Normalization-Tests-and-Assay-Hardening-Plan]]
- [ ] Add normalized filters, GIN indexes, and disjunctive facet counts. →
  [[45-Normalized-Filters-and-Facets-Plan]]
- [ ] Materialize ancestor arrays only after an ontology-backed field needs
  hierarchy. → [[24-Faceted-Search]]
- [ ] Fold normalized labels back into embedding doc; re-embed (cheap).
- [ ] Facet counts (native `GROUP BY`, disjunctive-correct). →
  [[45-Normalized-Filters-and-Facets-Plan]]
- [ ] Measure normalization precision/coverage vs. hand labels.

### Phase 3 — MCP + demo (1 wk)
- [ ] Local stdio MCP server exposing the three stable v1 tools. →
  [[47-MCP-Server-Plan]]
- [ ] Drive it from Claude: expansion → search → drill-in → summary.
- [ ] Scale ingest to full ~289k; re-embed; sanity-check facet counts.

### Later / v2 (not the spike)
- Sample-level (GSM) indexing — 8.6M docs; the real scale decision (add `pgvectorscale` / StreamingDiskANN; `pg_search` faceting is already in from v1). Also the *correctness* fix for within-sample multi-field filtering (the [[24-Faceted-Search|series-aggregation caveat]]), not just scale. → [[26-Datastore-Postgres#Scale headroom]]
- **Next normalization experiment:** ontology-derived deterministic candidates for `tissue`, with bounded LLM validation and a 100–200-value review set. → [[43-Tissue-Candidate-Generation-Plan]]
- More complex fields after the tissue decision gate (disease, cell type, dev stage, ethnicity).
- Server-side cross-encoder reranking (MedCPT / bge-reranker).
- Incremental refresh cron (idempotent ingest already supports it).
- Human UI (if ever needed beyond MCP).

## Definition of done (spike)

A recorded thread where Claude, over MCP, answers *"find single-cell RNA datasets on human PBMCs and summarize the assay mix"* — returning real GSEs spanning multiple sc-technologies, with a faceted breakdown, and a grounded cited summary. That single demo exercises every layer.

## Rough effort

~4–5 focused weeks solo to the DoD demo on a scoped slice; the full-corpus scale-up is mostly wait-time on the crawl + a few dollars of embeddings.
