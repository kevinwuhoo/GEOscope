---
title: Architecture Overview
tags: [architecture, design]
---

# 20 · Architecture Overview

← [[Home]]

## One picture

```mermaid
flowchart TB
  subgraph Ingest["① Ingestion (batch, offline) — see 21"]
    A[GEOmetadb SQLite snapshot\nchosen v1 bulk source] --> B[Aggregate GSE + GSM\nto geo_series.jsonl]
    T[Metadata-only SOFT top-up\npost-2024, later] -.-> B
    B --> D[Current series documents\n222,961 GSEs]
  end

  subgraph Norm["② Normalization — see 22"]
    D --> E[Field extraction\nkey:value + prose]
    E --> F[Cheap-first mapping\nexact/rules now; bounded LLM validation later]
    F --> G[Current v1: organism/sex IDs\n+ assay category/detail]
    F -. tissue gate / ancestors .-> V2[More ontology fields\n+ hierarchy (v2+)]
  end

  subgraph Index["③ Indexing (v1) — see 25/26"]
    D --> H[Freeze current embed_text\ntitle+type+organism+summary+design\n+molecule/source/characteristics]
    H --> I[Embed\nBGE baseline / MedCPT / Qwen bake-off]
    G --> J[(Postgres)]
    I --> J
    D --> J
  end

  subgraph Serve["④ Serve (v1) — see 23/24/27"]
    J --> K[Hybrid retrieval\npgvector + pg_search BM25 + RRF]
    K --> L[Facet counts\nimplemented SQL GROUP BY]
    K --> M[[Private remote MCP\nStreamable HTTP + auth]]
  end

  subgraph Client["⑤ LLM client (Claude via MCP)"]
    M --> N[Query understanding\n+ synonym expansion]
    N --> M
    M --> O[Ranked list ➜ LLM summary ➜ conversation]
  end
```

## The five layers

| # | Layer | Does | Note |
|---|---|---|---|
| ① | **Ingestion** | Aggregate the chosen GEOmetadb snapshot; retain SOFT top-up tooling | Batch/re-runnable. [[21-Ingestion-Pipeline]] |
| ② | **Normalization** | Free text → controlled ontology IDs | The hard, valuable part. [[22-Ontology-Normalization]] |
| ③ | **Indexing** | Build + embed docs, write to Postgres | Runtime/storage are measured in the bake-off. [[25-Embeddings-and-Cost]] |
| ④ | **Serve** | Hybrid search, facets, get — behind invite-only remote MCP | One Postgres does it all. [[26-Datastore-Postgres]] |
| ⑤ | **Client** | LLM drives search, synthesizes answers | Not ours to build. [[27-MCP-Interface]] |

## The load-bearing decisions

1. **One Postgres for everything.** `pgvector` (dense) + `pg_search`/ParadeDB (BM25 + fast facets) + plain columns/arrays (filters + ontology facets). No separate Elasticsearch/vector-DB to sync. Matches your Postgres preference. Rationale & the alternatives considered: [[26-Datastore-Postgres]].
2. **Series-level (GSE) documents in v1.** The current snapshot has 222,961
   documents, not 8.6M samples. Sample-level is a v2 scale step. [[40-Roadmap]]
3. **Retrieval is ours; generation is the LLM's.** We ship an MCP server; the client does expansion + summary + chat. [[27-MCP-Interface]]
4. **Normalization is a cheap-first cascade.** Today it feeds flat filters and
   facets. Injecting normalized labels into `embed_text` is a later controlled
   document ablation, after the model bake-off. [[22-Ontology-Normalization]]
5. **Everything measured against a small eval set** — embedding model, mapper,
   and expansion are A/B'd, not guessed. The side-by-side embedding design is
   [[48-Alternate-Embedding-Bakeoff]]. [[25-Embeddings-and-Cost]]

## Data-flow contract (what each stage hands off)

- Ingest **(current v1)** → `data/processed/geo_series.jsonl` aggregated from
  GEOmetadb, then the implemented `series` table.
- Normalize **(current v1)** → `series.organism_ids[]`, `sex_ids[]`,
  `assay_categories[]`, `assay_labels[]` plus status columns. Tissue, disease,
  cell type, and ancestor closure remain **(v2+)**.
- Index → implemented `series` with one active whole-document vector; during the
  **(v1)** bake-off, two temporary candidate columns plus
  `embedding_variant_state` preserve provenance.
- Serve → MCP `search_datasets` returns
  `{results:[...], facets:{...}, retrieval_version, embedding_variant}`;
  `get_dataset` and `facet_values` use the exact bounded contracts in [[27-MCP-Interface]].

Schema detail in [[26-Datastore-Postgres]].

## Sources

Synthesis note — external citations live in the layer notes it links: [[21-Ingestion-Pipeline]], [[22-Ontology-Normalization]], [[23-Search-and-Retrieval]], [[24-Faceted-Search]], [[26-Datastore-Postgres]] — and the full index [[99-Sources]].
