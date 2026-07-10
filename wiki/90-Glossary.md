---
title: Glossary
tags: [glossary, reference]
---

# 90 · Glossary

← [[Home]]

## GEO / NCBI
- **GEO** — Gene Expression Omnibus, NCBI's functional-genomics data repository.
- **GSE / GSM / GPL / GDS** — Series (study) / Sample / Platform / curated DataSet. → [[10-GEO-Data-Model]]
- **Platform (GPL)** — the measurement instrument (array chip w/ probe table, or sequencer model). GEO clones each platform **per organism**, so raw GPL = instrument × species — facet on derived **`instrument_model`** (organism stripped) + GPL **`technology`** attr instead. Platform ≠ assay. → [[10-GEO-Data-Model#Platforms (GPL) — the organism-cloning gotcha|GPL gotcha]]
- **SOFT** — Simple Omnibus Format in Text; GEO's line-based metadata format.
- **MINiML** — XML rendering of SOFT (MIAME-compliant).
- **E-utilities** — NCBI's programmatic API (`esearch`/`esummary`/`efetch`/`elink`); GEO lives in the `gds` DB.
- **SRA** — Sequence Read Archive; GEO auto-deposits raw sequencing data here (source of `library_*` fields).
- **library_strategy / source / selection** — SRA controlled-vocab assay fields. `RNA-Seq` covers both bulk and single-cell (the crux of [[11-The-Metadata-Problem]]).

## Ontologies (→ [[22-Ontology-Normalization]])
- **EFO** — Experimental Factor Ontology (EBI hub ontology for expression metadata).
- **NCBITaxon** — organism/species.
- **UBERON** — cross-species anatomy/tissue.
- **CL** — Cell Ontology (cell types; a DAG).
- **MONDO** — merged disease ontology (harmonizes DOID/OMIM/Orphanet/NCIT).
- **DOID** — Human Disease Ontology (older; subsumed by MONDO).
- **OBI** — Ontology for Biomedical Investigations (assays/methods).
- **PATO** — Phenotype And Trait Ontology (used for **sex**: male `PATO:0000384`, female `PATO:0000383`).
- **HANCESTRO** — human ancestry/ethnicity.
- **HsapDv / MmusDv** — human / mouse developmental stages.
- **Cellosaurus (CVCL)** — cell lines.
- **DAG** — directed acyclic graph; ontologies where a term has multiple parents (→ ancestor arrays for facets).
- **transitive-ancestor closure** — the full set of a term's ancestors; materialized per record for hierarchical facets.

## Mapping tools
- **OLS4** — EBI Ontology Lookup Service; search/lookup API across ontologies.
- **BioPortal** — NCBO ontology repository + Annotator.
- **Zooma** — EBI text→ontology annotation, curated-first with OLS fallback + confidence tiers.
- **text2term** — Python mapper (default TF-IDF; also Zooma/BioPortal/edit-distance).
- **OAK (`oaklib`)** — Ontology Access Kit; unified ontology ops + text annotation.
- **OntoGPT / SPIRES** — LLM extraction + ontology grounding.

## Search / RAG
- **Dense / sparse retrieval** — embedding kNN vs. term-based (BM25).
- **BM25** — the standard lexical ranking function. Our chosen provider is
  **ParadeDB `pg_search`**; current facets use separate SQL counts. Postgres
  native FTS `ts_rank` is not BM25. → [[26-Datastore-Postgres]]
- **`pg_search` / `pdb.score` / `pdb.agg`** — ParadeDB's BM25 search operator
  (`@@@`), relevance score, and optional single-pass facet aggregation. v1 uses
  `pg_search` for lexical retrieval and explicit SQL `GROUP BY` for facets.
- **RRF** — Reciprocal Rank Fusion; `Σ 1/(k+rank)`, k≈60; rank-based, no score normalization.
- **HNSW** — graph ANN index for vectors.
- **Reranker / cross-encoder** — re-scores `(query, doc)` jointly for top-k; e.g. MedCPT Cross-Encoder, bge-reranker.
- **Query expansion** — enriching a query with synonyms/related terms (ideally ontology-grounded).
- **HyDE** — Hypothetical Document Embeddings (embed a generated pseudo-answer).
- **Facet** — `(value, count)` buckets over a result set; disjunctive = OR-within/AND-across.

## Models
- **BGE-small-en-v1.5** — current 384-dimensional whole-document retrieval baseline.
- **MedCPT** — NCBI biomedical retriever (paired Query+Article bi-encoders;
  768 dimensions) in the v1 bake-off.
- **Qwen3-Embedding-0.6B** — open embedding model tested at its full
  1,024-dimensional output in the v1 bake-off.
- **text-embedding-3-small/large** — hosted OpenAI embeddings considered in
  early research, not part of the fixed local-model bake-off.
- **SapBERT / BioLORD** — biomedical *entity/synonym* embedders (for normalization, not document search).

## Infra
- **pgvector** — Postgres vector extension (HNSW, `halfvec`, iterative scans).
- **ParadeDB / pg_search** — Postgres BM25 plus optional Tantivy-backed
  aggregation; v1 uses it for lexical ranking.
- **pgvectorscale** — StreamingDiskANN + quantization for scale-up.
- **MCP** — Model Context Protocol; how the LLM client calls our search tools. → [[27-MCP-Interface]]

## Sources

- Master project reference index — [[99-Sources]]
- BGE small v1.5 — https://huggingface.co/BAAI/bge-small-en-v1.5
- MedCPT paper — https://academic.oup.com/bioinformatics/article/39/11/btad651/7335842
- Qwen3-Embedding-0.6B — https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
- pgvector — https://github.com/pgvector/pgvector
- ParadeDB `pg_search` — https://www.paradedb.com/blog/introducing-search
- Model Context Protocol — https://modelcontextprotocol.io/
