---
title: GEOscope — Home (Map of Content)
tags: [moc, geo, search, rag]
status: working-prototype
created: 2026-07-08
---

# 🧬 GEOscope

> **Project identity:** **GEOscope** is the public name for the GEO Metadata
> Index prototype: an instrument for seeing what literal GEO search misses.
> The name, audience, marketing page, and live-demo boundary are documented in
> [[58-GEOscope-Marketing-and-Live-Demo]].

> **Current primary path (2026-07-12):** Prefect materializes canonical GSE
> records, builds/resumes `gemini_embedding_2_3072_v1` (3,072 dimensions), and
> must load and audit Elasticsearch before the run succeeds. Elasticsearch is
> the only primary online datastore; PostgreSQL remains historical comparison
> code. BGE, MedCPT, and Qwen are development/evaluation only. Start with
> [[57-Canonical-Production-Pipeline]], [[20-Architecture-Overview]], and
> [[21-Ingestion-Pipeline]].

> **MCP implementation (2026-07-12):** The private FastMCP service is merged on
> `main` with Elasticsearch-backed BM25/dense/hybrid retrieval, exact GSE lookup,
> closed facets, Gemini query embeddings, JWT/JWKS invitation checks, bounded
> HTTP admission, Docker packaging, and a live three-tool smoke. Hosting it
> behind the final HTTPS/OAuth edge remains deployment work. See
> [[27-MCP-Interface]] and [[47-MCP-Server-Plan]].

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
- [[58-GEOscope-Marketing-and-Live-Demo]] — public identity, marketing narrative, React page, and FastAPI live-demo adapter
- [[20-Architecture-Overview]] — the whole system, end to end
- [[21-Ingestion-Pipeline]] — GEOmetadb baseline plus the new stripped-SOFT canonical-record path
- [[22-Ontology-Normalization]] — field→ontology map, the mapping cascade, RAG vs. IDs
- [[23-Search-and-Retrieval]] — hybrid retrieval, query expansion, reranking
- [[24-Faceted-Search]] — facet model, ontology-backed hierarchical facets
- [[25-Embeddings-and-Cost]] — Gemini production decision, measured cost, and historical model evaluation
- [[28-Embedding-Granularity]] — per-field vs whole-document embedding (field→mechanism routing)
- [[26-Datastore-Postgres]] — historical pgvector + ParadeDB baseline (retained code, not a primary path)
- [[51-Search-Database-Bakeoff-and-Elasticsearch-Plan]] — database bakeoff and the local-first Elasticsearch-only plan
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
- [[47-MCP-Server-Plan]] — historical Track 4 plan; the Elasticsearch-backed service is implemented and deployment remains
- [[48-Alternate-Embedding-Bakeoff]] — approved proposal: one temporary column per model
- [[49-Alternate-Embedding-Bakeoff-Implementation-Plan]] — resumable builds, loading, evaluation, and active-model integration
- [[50-Coworker-Handoff-Prompts]] — copy-ready prompts for the remote MCP and embedding owners
- [[51-Search-Database-Bakeoff-and-Elasticsearch-Plan]] — Postgres/Qdrant/Elastic bakeoff notes and migration plan
- [[52-Embedding-Bakeoff-Runbook]] — BGE/MedCPT/Qwen/Gemini build, load, evaluation, and promotion runbook
- [[53-Prefect-SOFT-ETL-and-Embedding-Prototype-Plan]] — current simple plan: missing SOFT→record→embedding exactly once
- [[54-Incremental-Corpus-Future-State]] — deferred content-hash, snapshot, vector-delta, and alias endpoint
- [[55-Prefect-and-Local-Elasticsearch-Coworker-Prompts]] — current copy-ready ETL/embedding and local-ES handoffs
- [[90-Glossary]] — every acronym in one place
- [[99-Sources]] — all citations

## The 30-second version

1. **Ingest** stripped family SOFT with a local Prefect flow. One canonical JSON
   record per GSE is complete when it exists; later runs process only missing or
   explicitly deleted outputs. The 222,961-series GEOmetadb corpus remains the
   measured baseline during migration.
2. **Normalize** organism→NCBITaxon, sex→PATO, and assay→closed category/detail
   labels today. Tissue→UBERON is the next experiment; disease/cell type and
   hierarchy are v2+.
3. **Embed** the completed canonical JSON record set with Gemini's lower-cost
   Batch API, then index BM25, `embedding_gemini_3072`, filters, and facets in
   Elasticsearch. Alternate model artifacts remain development-only and live
   outside the production artifact root.
4. **Serve** hybrid search + facet counts + get-by-accession through the merged
   **invite-only FastMCP service**; deploy the packaged ASGI app behind the
   production HTTPS/OAuth edge.
5. The **LLM client** (Claude, etc.) does query understanding, synonym expansion, and — because it's just calling tools — the summary and conversational answers for free.

→ Recommended v1 target and rationale live in [[40-Roadmap]].
