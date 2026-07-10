---
title: Open Questions
tags: [decisions, open-questions]
---

# 41 · Open Questions

← [[Home]]

Decisions still to make. ✅ = answered by you already; ❓ = needs a call.

## Answered (from the kickoff)
- ✅ **Corpus ambition:** all of GEO; the fixed v1 comparison uses the complete
  222,961-series snapshot already loaded, followed by a separately versioned
  freshness top-up if the spike continues.
- ✅ **Output:** ranked list first; summary/conversation via LLM over MCP. → [[27-MCP-Interface]]
- ✅ **Ambition:** prototype / spike. → [[40-Roadmap]]
- ✅ **Infra:** Postgres-first and cost-conscious; compare local embedding
  pipelines with measured compute, memory, latency, and storage. →
  [[25-Embeddings-and-Cost]], [[26-Datastore-Postgres]]
- ✅ **Initial MCP audience:** invite-only access for you and selected coworkers;
  public/self-service access is deferred. → [[47-MCP-Server-Plan]]
- ✅ **Third v1 normalized field:** assay was implemented alongside organism and
  sex; tissue is the next bounded ontology experiment. →
  [[22-Ontology-Normalization]], [[43-Tissue-Candidate-Generation-Plan]]
- ✅ **Initial corpus/source:** use the full 222,961-series GEOmetadb snapshot
  for the fixed spike; defer post-2024 metadata-only top-up until after the
  retrieval/model decision. → [[21-Ingestion-Pipeline]], [[42-Build-Log]]

## Still open

### Scope & unit
- ❓ **Series-only, or also persist per-sample rows now?** Storing raw samples now (cheap) preserves the v2 option without committing to indexing 8.6M docs.

### Normalization
- ❓ **Confidence threshold `τ`** for auto-accepting tier-3 similarity mappings — set empirically from the eval.
- ❓ **How to surface uncertainty** in facets: hide low-confidence mappings, or show them tagged "predicted"?
- ❓ **Sex convention:** PATO (matches CELLxGENE/expression world) — confirm we don't need NCIT for any downstream consumer.

### Search
- ❓ **Hybrid: fuse or route?** Keep lexical `pg_search`/BM25 for exact IDs
  regardless; whether to RRF-fuse with dense depends on the embedding model—
  decide from eval. → [[23-Search-and-Retrieval]]
- ❓ **Embedding granularity:** one whole-doc embedding (v1 default) vs per-field/multi-vector. Split only if the eval shows narrative dilution. → [[28-Embedding-Granularity]]
- ✅ **Lexical / facet implementation:** **ParadeDB `pg_search`** supplies BM25;
  explicit disjunctive SQL `GROUP BY` supplies the current four facets. Benchmark
  `pdb.agg` only if counts become limiting. Self-hosted ParadeDB remains the
  spike environment; managed-hosting extension availability is a later concern.
  → [[26-Datastore-Postgres]]
- ❓ **Reranking in v1?** Probably defer to the LLM client; revisit if eval shows top-k ordering is weak.

### Platform / ops
- ❓ **Self-host vs managed Postgres?** Spike = self-host (ParadeDB Docker) so `pg_search` is available. Managed later needs `pg_search` availability check.
- ❓ **Where does this live long-term?** Personal project, internal BillionToOne tool, or public? Affects data-refresh cadence and whether we harden ingest.
- ❓ **Embedding model final pick (v1)** — compare the existing
  `bge-small-en-v1.5` baseline with paired MedCPT and
  `Qwen3-Embedding-0.6B` (1,024 dimensions), then promote only from the reviewed
  retrieval eval. → [[48-Alternate-Embedding-Bakeoff]],
  [[49-Alternate-Embedding-Bakeoff-Implementation-Plan]]
- ❓ **MCP identity provider and host (v1)** — transport and invite policy are fixed,
  but the OAuth issuer, public domain, and host remain deployment choices. →
  [[47-MCP-Server-Plan]]

### Product
- ❓ **Refresh expectations** — one-time index for the spike, or living/refreshed? (Ingest is idempotent either way.)

## Parking lot (interesting, later)
- Gene-signature search (à la RummaGEO) on top of the metadata index.
- Cross-linking to SRA runs for one-click data pull.
- Sample-type classifier (MetaSRA's cell-line/tissue/primary/stem categories).
- Embedding the *normalized ontology graph* itself for smarter expansion.
