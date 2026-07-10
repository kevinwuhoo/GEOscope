---
title: GEO Metadata Index — Home (Map of Content)
tags: [moc, geo, search, rag]
status: planning
created: 2026-07-08
---

# 🧬 GEO Metadata Index

> A system that **indexes and serves NCBI GEO metadata** so conceptual queries
> can cross submitter vocabulary, while messy fields collapse onto controlled
> values that support precise filters and facets. The current v1 normalizes
> organism, sex, and assay; tissue and other complex ontology fields follow a
> measured decision gate. Semantic + faceted + keyword search is exposed over
> **MCP** so an LLM client can synthesize and converse on top.

This is an [[41-Open-Questions|Obsidian-style]] planning vault. Start at [[00-Overview]] and follow the wikilinks.

## Map of Content

### The problem & the domain
- [[00-Overview]] — problem statement, goals, non-goals, the one-paragraph pitch
- [[10-GEO-Data-Model]] — GSE/GSM/GPL/GDS, SOFT/MINiML, access methods, scale
- [[11-The-Metadata-Problem]] — *why* keyword search fails (the single-cell worked example)

### The design
- [[20-Architecture-Overview]] — the whole system, end to end
- [[21-Ingestion-Pipeline]] — chosen bulk snapshot, rebuild order, and deferred top-up path
- [[22-Ontology-Normalization]] — field→ontology map, the mapping cascade, RAG vs. IDs
- [[23-Search-and-Retrieval]] — hybrid retrieval, query expansion, reranking
- [[24-Faceted-Search]] — facet model, ontology-backed hierarchical facets
- [[25-Embeddings-and-Cost]] — model options, measured runtime/storage, and the eval plan
- [[28-Embedding-Granularity]] — per-field vs whole-document embedding (field→mechanism routing)
- [[26-Datastore-Postgres]] — pgvector + ParadeDB `pg_search` BM25 + SQL facets, why one Postgres
- [[27-MCP-Interface]] — the MCP server, its tools, and "the LLM is the RAG loop"

### Context & execution
- [[30-Prior-Art]] — MetaSRA, CELLxGENE, STARGEO, GEOmetadb, DISCO, OmicIDX…
- [[40-Roadmap]] — the spike plan, phased milestones
- [[41-Open-Questions]] — decisions still to make
- [[42-Build-Log]] — **what we've built, tried, and measured** (living progress log)
- [[43-Tissue-Candidate-Generation-Plan]] — next normalization experiment: deterministic candidates + bounded LLM validation
- [[44-Normalization-Tests-and-Assay-Hardening-Plan]] — Track 1: tests + contextual assay rules + targeted assay refresh
- [[45-Normalized-Filters-and-Facets-Plan]] — Track 2: query/facet layer over the populated organism, sex, and assay arrays
- [[46-Retrieval-Evaluation-Plan]] — Track 3: 16-query pooled human evaluation, no trained model
- [[47-MCP-Server-Plan]] — Track 4: invite-only remote FastMCP server with three stable tools
- [[48-Alternate-Embedding-Bakeoff]] — approved proposal: one temporary column per model
- [[49-Alternate-Embedding-Bakeoff-Implementation-Plan]] — resumable builds, loading, evaluation, and active-model integration
- [[50-Coworker-Handoff-Prompts]] — copy-ready prompts for the remote MCP and embedding owners
- [[90-Glossary]] — every acronym in one place
- [[99-Sources]] — all citations

## The 30-second version

1. **Ingest** the chosen 222,961-series GEOmetadb snapshot; retain metadata-only
   SOFT tooling for a later post-2024 top-up.
2. **Normalize** organism→NCBITaxon, sex→PATO, and assay→closed category/detail
   labels today. Tissue→UBERON is the next experiment; disease/cell type and
   hierarchy are v2+.
3. **Embed** the frozen narrative document once per series and index it in **one
   Postgres** (`pgvector` for dense, `pg_search`/BM25 for lexical, four flat
   normalized arrays for current filters/facets).
4. **Serve** hybrid search + facet counts + get-by-accession as an
   **invite-only remote MCP server**.
5. The **LLM client** (Claude, etc.) does query understanding, synonym expansion, and — because it's just calling tools — the summary and conversational answers for free.

→ Recommended v1 target and rationale live in [[40-Roadmap]].
