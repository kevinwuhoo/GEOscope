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
- **Fields normalized:** `sex` (trivial), `organism` (near-deterministic), **`assay`** (EFO — powers the single-cell story). Add `tissue`/`disease` if time.
- **Search:** hybrid (pgvector + pg_search BM25 + RRF) with ontology-aware expansion.
- **Facets:** organism, assay, tissue, year, sample-count, single-cell flag; hierarchical for assay.
- **Serve:** MCP server (`search_datasets`, `get_dataset`, `facet_values`).
- **Eval:** ~50–100 labeled queries; measure before choosing the embedding model.

## Phased plan

### Phase 0 — Foundations (½–1 wk)
- [ ] Postgres up (ParadeDB Docker image = pgvector + pg_search). *(not yet — searching in-memory so far)*
- [x] Ingestion skeleton hitting `esearch`/`esummary` (JSON) → `geo-fetch-summaries`.
- [x] Land the corpus — **chose GEOmetadb bulk over a crawl**: 222,961 series into `data/processed/geo_series.jsonl`. → [[42-Build-Log]]
- [ ] **Build the eval set** (seed queries). *Note: single-cell case is weak on real data — [[42-Build-Log]] — reframe around conceptual queries.* → [[25-Embeddings-and-Cost#Eval]]

> **Progress beyond Phase 0:** already embedded all 223k (local `bge-small`) and validated in-memory semantic search — normally Phase 1 work. Full log: [[42-Build-Log]].

### Phase 1 — Search baseline (1 wk)
- [ ] Build embedding doc; embed with `text-embedding-3-small` (~$6 for full corpus).
- [ ] HNSW + BM25 indexes; RRF hybrid query. → [[26-Datastore-Postgres]]
- [ ] Prove: **"single cell RNA" retrieves 10x/Drop-seq/SPLiT-seq** studies. (Even pre-normalization, via dense + expansion.)
- [ ] Run eval; A/B `-3-small` vs **MedCPT** vs one open model.

### Phase 2 — Normalization + facets (1–2 wks)
- [ ] Cascade for `sex`, `organism`, `assay`. → [[22-Ontology-Normalization]]
- [ ] Materialize ancestor arrays for assay hierarchy. → [[24-Faceted-Search]]
- [ ] Fold normalized labels back into embedding doc; re-embed (cheap).
- [ ] Facet counts (native `GROUP BY`, disjunctive-correct).
- [ ] Measure normalization precision/coverage vs. hand labels.

### Phase 3 — MCP + demo (1 wk)
- [ ] MCP server exposing the tools. → [[27-MCP-Interface]]
- [ ] Drive it from Claude: expansion → search → drill-in → summary.
- [ ] Scale ingest to full ~289k; re-embed; sanity-check facet counts.

### Later / v2 (not the spike)
- Sample-level (GSM) indexing — 8.6M docs; the real scale decision (add `pgvectorscale` / StreamingDiskANN; `pg_search` faceting is already in from v1). Also the *correctness* fix for within-sample multi-field filtering (the [[24-Faceted-Search|series-aggregation caveat]]), not just scale. → [[26-Datastore-Postgres#Scale headroom]]
- More fields (tissue, disease, cell type, dev stage, ethnicity).
- Server-side cross-encoder reranking (MedCPT / bge-reranker).
- Incremental refresh cron (idempotent ingest already supports it).
- Human UI (if ever needed beyond MCP).

## Definition of done (spike)

A recorded thread where Claude, over MCP, answers *"find single-cell RNA datasets on human PBMCs and summarize the assay mix"* — returning real GSEs spanning multiple sc-technologies, with a faceted breakdown, and a grounded cited summary. That single demo exercises every layer.

## Rough effort

~4–5 focused weeks solo to the DoD demo on a scoped slice; the full-corpus scale-up is mostly wait-time on the crawl + a few dollars of embeddings.
