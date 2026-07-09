---
title: GEO Metadata Index — Home (Map of Content)
tags: [moc, geo, search, rag]
status: planning
created: 2026-07-08
---

# 🧬 GEO Metadata Index

> A system that **indexes and serves NCBI GEO metadata** so that a search for *"single cell RNA"* actually surfaces 10x 3′, 10x 5′, Drop-seq, Smart-seq2, and SPLiT-seq datasets — and so that messy submitter fields (`sex = M/F/0/1`, free-text organism/tissue/disease) collapse onto a **common ontology**. Semantic + faceted + keyword search, exposed over **MCP** so an LLM can do the synthesis and conversation on top.

This is an [[41-Open-Questions|Obsidian-style]] planning vault. Start at [[00-Overview]] and follow the wikilinks.

## Map of Content

### The problem & the domain
- [[00-Overview]] — problem statement, goals, non-goals, the one-paragraph pitch
- [[10-GEO-Data-Model]] — GSE/GSM/GPL/GDS, SOFT/MINiML, access methods, scale
- [[11-The-Metadata-Problem]] — *why* keyword search fails (the single-cell worked example)

### The design
- [[20-Architecture-Overview]] — the whole system, end to end
- [[21-Ingestion-Pipeline]] — fetch → parse → normalize → embed → index
- [[22-Ontology-Normalization]] — field→ontology map, the mapping cascade, RAG vs. IDs
- [[23-Search-and-Retrieval]] — hybrid retrieval, query expansion, reranking
- [[24-Faceted-Search]] — facet model, ontology-backed hierarchical facets
- [[25-Embeddings-and-Cost]] — model options, **cost estimates**, the eval plan
- [[28-Embedding-Granularity]] — per-field vs whole-document embedding (field→mechanism routing)
- [[26-Datastore-Postgres]] — pgvector + ParadeDB `pg_search` (BM25 + faceting), why one Postgres
- [[27-MCP-Interface]] — the MCP server, its tools, and "the LLM is the RAG loop"

### Context & execution
- [[30-Prior-Art]] — MetaSRA, CELLxGENE, STARGEO, GEOmetadb, DISCO, OmicIDX…
- [[40-Roadmap]] — the spike plan, phased milestones
- [[41-Open-Questions]] — decisions still to make
- [[42-Build-Log]] — **what we've built, tried, and measured** (living progress log)
- [[90-Glossary]] — every acronym in one place
- [[99-Sources]] — all citations

## The 30-second version

1. **Ingest** all of GEO (~289k GSE series) via E-utilities + FTP; parse SOFT/MINiML.
2. **Normalize** the free-text fields onto controlled ontology IDs (organism→NCBITaxon, tissue→UBERON, cell type→CL, disease→MONDO, assay→EFO, sex→PATO) using a cheap-first cascade.
3. **Embed** each series into a vector, and index everything in **one Postgres** (`pgvector` for dense, `pg_search`/BM25 for lexical, columns + ancestor arrays for facets).
4. **Serve** hybrid search + facet counts + get-by-accession as an **MCP server**.
5. The **LLM client** (Claude, etc.) does query understanding, synonym expansion, and — because it's just calling tools — the summary and conversational answers for free.

→ Recommended v1 target and rationale live in [[40-Roadmap]].
