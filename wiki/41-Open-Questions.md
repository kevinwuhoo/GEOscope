---
title: Open Questions
tags: [decisions, open-questions]
---

# 41 · Open Questions

← [[Home]]

Decisions still to make. ✅ = answered by you already; ❓ = needs a call.

## Answered (from the kickoff)
- ✅ **Corpus:** all of GEO (I suggest a scoped first slice to iterate, then widen — see below).
- ✅ **Output:** ranked list first; summary/conversation via LLM over MCP. → [[27-MCP-Interface]]
- ✅ **Ambition:** prototype / spike. → [[40-Roadmap]]
- ✅ **Infra:** Postgres-first; open to a good open embedding; cost-conscious (it's trivial anyway). → [[25-Embeddings-and-Cost]], [[26-Datastore-Postgres]]

## Still open

### Scope & unit
- ❓ **Full corpus first, or a human+mouse RNA-seq slice first?** I lean *slice-first* for iteration speed; the crawl code is identical. Your call on patience vs. completeness.
- ❓ **Series-only, or also persist per-sample rows now?** Storing raw samples now (cheap) preserves the v2 option without committing to indexing 8.6M docs.

### Normalization
- ❓ **Which 3rd field for the spike:** `assay` (EFO, best for the single-cell demo) vs `tissue` (UBERON, common filter). I lean `assay`.
- ❓ **Confidence threshold `τ`** for auto-accepting tier-3 similarity mappings — set empirically from the eval.
- ❓ **How to surface uncertainty** in facets: hide low-confidence mappings, or show them tagged "predicted"?
- ❓ **Sex convention:** PATO (matches CELLxGENE/expression world) — confirm we don't need NCIT for any downstream consumer.

### Search
- ❓ **Hybrid: fuse or route?** Keep lexical (native FTS) for exact IDs regardless; whether to RRF-fuse with dense depends on the embedding model — decide from eval. → [[23-Search-and-Retrieval]]
- ❓ **Embedding granularity:** one whole-doc embedding (v1 default) vs per-field/multi-vector. Split only if the eval shows narrative dilution. → [[28-Embedding-Granularity]]
- ✅ **Lexical / facet engine:** **ParadeDB `pg_search`** (real BM25 + first-class faceting), self-hosted via the ParadeDB Docker image. Facets are the priority, so this is committed. Managed-hosting availability (RDS/Aurora often lack it) is the one thing to revisit for production. → [[26-Datastore-Postgres]]
- ❓ **Reranking in v1?** Probably defer to the LLM client; revisit if eval shows top-k ordering is weak.

### Platform / ops
- ❓ **Self-host vs managed Postgres?** Spike = self-host (ParadeDB Docker) so `pg_search` is available. Managed later needs `pg_search` availability check.
- ❓ **Where does this live long-term?** Personal project, internal BillionToOne tool, or public? Affects data-refresh cadence and whether we harden ingest.
- ❓ **Embedding model final pick** — `text-embedding-3-small` to start; MedCPT/open contender decided by eval.

### Product
- ❓ **Who's the user?** You + a few colleagues, or a broader internal audience? Sets the bar for the MCP tool ergonomics and whether a human UI is ever needed.
- ❓ **Refresh expectations** — one-time index for the spike, or living/refreshed? (Ingest is idempotent either way.)

## Parking lot (interesting, later)
- Gene-signature search (à la RummaGEO) on top of the metadata index.
- Cross-linking to SRA runs for one-click data pull.
- Sample-type classifier (MetaSRA's cell-line/tissue/primary/stem categories).
- Embedding the *normalized ontology graph* itself for smarter expansion.
