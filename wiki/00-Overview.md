---
title: Overview
tags: [geo, overview]
---

# 00 · Overview

← [[Home]]

## Problem statement

[NCBI GEO](https://www.ncbi.nlm.nih.gov/geo/) is the largest public repository of functional-genomics data — **~289k series (GSE), ~8.6M samples (GSM), ~28.6k platforms (GPL)** as of 2026 ([counts](https://www.ncbi.nlm.nih.gov/geo/)). Its metadata is overwhelmingly **submitter-authored free text**, so its native Entrez search is brittle in two specific ways:

1. **Synonym / vocabulary blindness.** A search for *"single cell RNA"* misses datasets whose metadata only says *"10x"*, *"Chromium"*, *"Drop-seq"*, *"Smart-seq2"*, or *"SPLiT-seq"*. There is **no structured field** that marks a dataset as single-cell — `library_strategy` is just `RNA-Seq`, same as bulk. The discriminating text lives in prose fields (`!Series_summary`, `!Series_overall_design`, `!Sample_extract_protocol_ch1`). See the worked example in [[11-The-Metadata-Problem]].
2. **No harmonized values.** The same concept is written a dozen ways: `sex = M | F | male | female | 0 | 1`; organism as `human | Homo sapiens | H. sapiens`. You can't build clean filters on raw values.

## What we're building

A **metadata index + search service** over GEO that provides:

- **Semantic / vector search** — meaning-based retrieval so conceptually-equivalent studies co-retrieve regardless of wording. → [[23-Search-and-Retrieval]]
- **Ontology normalization** — map free-text fields onto controlled ontology IDs so values are comparable and facetable. → [[22-Ontology-Normalization]]
- **Faceted search** — filter/drill-down by organism, assay, tissue, disease, sample count, year… including **ontology-hierarchy** facets (pick "T cell" → get all descendant cell types). → [[24-Faceted-Search]]
- **Keyword search** — exact matching for accessions, gene symbols, platform IDs (`GPL24676`). → hybrid with the above.
- **An MCP interface** — so an LLM can drive the search and layer summarization/conversation on top. → [[27-MCP-Interface]]

## North star

The ideal end state: a **clean ontology layer + advanced search + strict, controlled enums** that *both an LLM (over MCP) and a human* can query with precision. Concretely — every facetable field is a **closed vocabulary of ontology IDs + labels**, not free text, so a query like `assay = 10x 5′ scRNA-seq AND organism = Homo sapiens` is unambiguous and returns exactly what it says. The LLM's job is to **translate** messy natural language into those strict enum constraints (plus semantic search for the fuzzy part); the enums keep it honest. → [[22-Ontology-Normalization]], [[24-Faceted-Search]], [[27-MCP-Interface]]

## Is this "just RAG"? Is normalization "just embeddings"?

Two questions worth settling up front (you raised both):

- **"Maybe ontology mapping is handled by RAG/embeddings — I'm not sure."** Partly. Embeddings give you *recall* (fuzzy semantic matching for search). But for **facets** you need **discrete, correct ontology IDs** — you can't `GROUP BY` a cosine similarity. So normalization and search are *different jobs*: normalization produces clean IDs (deterministic lookup + embeddings + LLM grounding, a cascade), and those IDs both power facets **and** enrich the text you embed. Details and the evidence in [[22-Ontology-Normalization]].
- **"I effectively want a RAG solution."** Yes — but the best split is: **you build retrieval, the LLM does generation.** Expose search as MCP tools; the calling model handles query expansion + answer synthesis. → [[27-MCP-Interface]].

## Goals (v1 spike)

- Prove the *"single cell RNA" → all sc-technologies* retrieval on real GEO data.
- Prove ontology normalization on 2–3 fields end to end (sex, organism, one hard one: tissue or assay).
- One Postgres, hybrid search, basic facets, an MCP server Claude can call.
- A small **eval set** so embedding/mapping choices are measured, not guessed. → [[25-Embeddings-and-Cost]]

## Non-goals (for now)

- Not re-processing expression *data* (we index **metadata**, not counts matrices).
- Not per-sample (GSM) indexing in v1 — series-level (GSE) first; 8.6M samples is a v2 scale decision. → [[40-Roadmap]]
- Not building our own chat UI / generation layer — the MCP client provides it.
- Not a curation platform (cf. [[30-Prior-Art|STARGEO]]); normalization is automated.

## Constraints (from you)

- **Prototype / spike**, not production.
- **Postgres-first**; open to a high-performance **open embedding model** if it's competitive.
- **Cost comes out of pocket** → estimate embedding cost; prefer cheap where quality is equal. (Spoiler: embedding all of GEO once is **single-digit dollars** — see [[25-Embeddings-and-Cost]]. Cost is *not* the constraint; quality and effort are.)

## Prior art, in one line

Nobody has shipped an ontology-faceted, natural-language search engine over the **full** GEO corpus. Existing tools are access-only ([[30-Prior-Art|GEOmetadb]], OmicIDX), SRA-focused (MetaSRA), curation-bound (STARGEO), or single-cell-only (CELLxGENE, DISCO). That gap is the opportunity. → [[30-Prior-Art]]

## Sources

- GEO holdings / counts — https://www.ncbi.nlm.nih.gov/geo/
- No structured single-cell field in GEO — https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8121533/

Full index: [[99-Sources]].
