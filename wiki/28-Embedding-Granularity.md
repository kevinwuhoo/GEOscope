---
title: Embedding Granularity — per-field vs whole-document
tags: [embeddings, retrieval, design]
---

# 28 · Embedding Granularity

← [[Home]] · pairs with [[25-Embeddings-and-Cost]], [[23-Search-and-Retrieval]], [[24-Faceted-Search]]

> **The question:** "Should we embed each field for precise retrieval, or is one whole-document embedding enough?"
> **The reframe:** don't ask *"embed every field?"* — ask *"what's the right retrieval mechanism for **this** field?"* Only one bucket of fields wants an embedding at all.

## Route each field to its mechanism

| Field type | Examples | Right mechanism | Embed? |
|---|---|---|---|
| **Categorical / controlled** | organism, sex, assay, tissue, disease, platform, year, sample count | normalized ontology ID → **facet / `WHERE` filter** ([[22-Ontology-Normalization]], [[24-Faceted-Search]]) | ❌ |
| **Narrative free text** | title, summary, overall design, protocols | **dense embedding** (semantic recall) | ✅ |
| **Identifiers** | GSE/GPL accessions, gene symbols | **exact / FTS / trigram** ([[26-Datastore-Postgres]]) | ❌ |

### Why categorical fields should NOT be embedded
For "search precisely against one field," if that field is categorical the *most precise* tool is the **facet/filter**, not an embedding. `organism_id @> ARRAY['NCBITaxon:9606']` is exact and fast; embedding the token "human" is both wasteful and *less* precise (it'll fuzzily match "humanized mouse", etc.). This is the entire reason [[22-Ontology-Normalization|normalization]] is a separate track from embedding — it gives precision that embeddings can't.

## For the narrative fields: one embedding is enough (for v1)

The per-field-embedding question only really applies to the narrative bucket. There:

- **Default: one frozen document embedding** = the current `embed_text` composed
  from title, study type, raw organism names, summary, overall design, molecule,
  sample source, and characteristics. Normalized-label injection is a later
  controlled ablation. The fields describe the same study and real queries are
  study-level, so one vector is the simplest starting point. →
  [[25-Embeddings-and-Cost]], [[48-Alternate-Embedding-Bakeoff]]
- **The risk it trades against — dilution.** Averaging a 2,000-token summary with a one-line design statement can bury a short-but-important signal. Whether that actually hurts *your* queries is an **eval question**, not an a-priori one.

### When per-field / multi-vector embedding earns its complexity
Only when **all three** hold:
1. the field is **long free text** (not categorical), **and**
2. users issue **field-scoped semantic** queries against it specifically, **and**
3. the [[25-Embeddings-and-Cost#Eval|eval set]] shows concatenation is measurably diluting recall.

If so, the sweet spot is **a handful of embeddings per doc, not one-per-field**:
- e.g. a "study" vector (title+summary+design) + a "sample characteristics" vector.
- Store as a child table `geo_series_vec(gse, field, embedding)` with **one** HNSW index, so a query can optionally be scoped to a field (`WHERE field = 'summary'`) and per-field hits combined (max or RRF across a doc's vectors).
- Cost is N× vectors — still cheap at this corpus size, but more moving parts.

Avoid true one-embedding-per-field: short fields (`sex: M`) are pointless to embed, and you'd multiply index/maintenance cost for signal that belongs in facets anyway.

## How a precise field query actually gets served

Not by field embeddings — by **combining the tracks**:

```
query: "single cell RNA in liver"
  ├─ future facet:  tissue ancestors ⊇ UBERON:liver     ← precision (v2+)
  └─ dense search:  "single cell RNA" over narrative vec ← recall (semantic)
        + client assay expansion → 10x/Drop-seq/SPLiT-seq ← v1 client behavior
```

Facets give precision; the embedding gives fuzzy recall; expansion bridges vocabulary. Keeping them separate is what makes each one good at its job.

## Decision

> **v1 = one whole-document embedding + normalized fields as facets/filters + `pg_search` BM25 for exact tokens.** This already delivers *both* precise field search (facets) and semantic search (doc vector). Add per-field/multi-vector embeddings **only if the eval shows narrative dilution** — it's a measured refinement, not a starting point.

The temporary BGE, MedCPT, and Qwen columns in
[[48-Alternate-Embedding-Bakeoff]] do not reverse this decision: each column is
one alternative representation of the same whole GSE document. Search uses one
selected model at a time. Keeping candidates side by side makes the comparison
reproducible; it does not combine several field vectors at query time.

→ tracked as an open item in [[41-Open-Questions#Search]].

## Sources

- MedCPT article encoder (768-dim, ~512-token cap → chunk long summaries) — https://huggingface.co/ncbi/MedCPT-Article-Encoder
- Multi-vector / late-interaction context (why not one-per-field) — https://awesomeagents.ai/leaderboards/embedding-model-leaderboard-mteb-march-2026/
- Field routing mirrors the normalization/facet split — see [[22-Ontology-Normalization]], [[24-Faceted-Search]]
