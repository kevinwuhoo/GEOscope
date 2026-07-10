---
title: Coworker Handoff Prompts
tags: [handoff, prompts, mcp, embeddings, implementation]
status: ready-to-send
created: 2026-07-10
updated: 2026-07-10
---

# 50 · Coworker Handoff Prompts

← [[Home]] · Track 4 plan: [[47-MCP-Server-Plan]] · embedding decision:
[[48-Alternate-Embedding-Bakeoff]] · embedding implementation:
[[49-Alternate-Embedding-Bakeoff-Implementation-Plan]]

These two tracks can start in parallel. The MCP owner builds against the current
BGE default. The embedding owner starts Task 1 immediately; after its registry
lands, Task 2 and the isolated Task 3 storage core can overlap. Task 4 consumes
landed Track 2 APIs; Task 5 consumes embedding Task 4 plus the Track 3
evaluator/qrels; and Task 6 consumes embedding Tasks 3–4, the landed remote MCP
Track 4, ready exported manifests, and the recorded promotion decision. The
prompts below make those merge gates explicit.

## Prompt A — revised Track 4: private remote FastMCP server

```text
Please own Track 4: implement the invite-only remote MCP server for this
repository.

Start by reading these files in order:
1. wiki/27-MCP-Interface.md
2. wiki/45-Normalized-Filters-and-Facets-Plan.md
3. wiki/47-MCP-Server-Plan.md
4. wiki/49-Alternate-Embedding-Bakeoff-Implementation-Plan.md, specifically
   Task 6's later MCP integration boundary

Treat wiki/47-MCP-Server-Plan.md as the implementation source of truth and work
through its checkboxes in order, using tests before implementation and small,
reviewable commits.

The outcome is a standalone FastMCP 3 ASGI service over Streamable HTTP at
/mcp, hosted behind HTTPS, with exactly three read-only tools:
search_datasets, get_dataset, and facet_values. It must use a provider-neutral
RemoteAuthProvider + JWT/JWKS resource-server design, require geo:read, and
allow only configured stable sub claims. It must be stateless, one worker,
explicitly Host/Origin protected, bounded, strict about input types, read-only
at the database layer, and safe to expose to invited coworkers. Do not add
prefix autocomplete, tissue tools, write/admin tools, a server-side LLM, or a
client-selectable embedding model.

Reuse the existing Track 2 search/facet contracts exactly. Search results need
one batched metadata-hydration query after ranking; do not issue N+1 queries.
The public service reports retrieval provenance but starts with the existing
bge_small_v15 default. Package the baseline query model inside the non-root
container so dense/hybrid calls require no runtime model download.

The identity provider, hosting vendor, DNS name, and TLS edge are intentionally
not selected. Build provider-neutral configuration and offline auth/discovery
tests. If real deployment credentials or a host are unavailable, finish all
code, offline tests, image checks, and documentation, then clearly leave only
the live hosted smoke steps pending—do not invent a provider or weaken auth.

Other coworkers may be changing embedding and evaluation code. Work in your
own branch, preserve unrelated changes, and do not implement alternate-model
storage. Coordinate before editing shared pyproject/uv.lock files, and leave
the variant-aware container replacement to embedding-plan Task 6.

Before handoff, run every offline command in the plan plus the full test suite.
Run opt-in Postgres/container/live checks only where the required environment is
available. Report: commits, files changed, tests and exact results, remaining
live prerequisites, and any deliberate deviation from the written contract.
```

## Prompt B — alternate embedding columns and bake-off

```text
Please own the alternate-embedding bake-off end to end for this repository.

Start by reading these files in order:
1. wiki/48-Alternate-Embedding-Bakeoff.md
2. wiki/49-Alternate-Embedding-Bakeoff-Implementation-Plan.md
3. wiki/45-Normalized-Filters-and-Facets-Plan.md
4. wiki/46-Retrieval-Evaluation-Plan.md
5. wiki/28-Embedding-Granularity.md
6. wiki/47-MCP-Server-Plan.md, only for the later integration boundary

Treat wiki/49-Alternate-Embedding-Bakeoff-Implementation-Plan.md as the
implementation source of truth and work through it task by task, using tests
before implementation and small, reviewable commits.

The approved prototype schema is one typed column and one independent cosine
HNSW index per fixed model pipeline:
- existing bge_small_v15: embedding vector(384), index series_hnsw
- medcpt_v1: embedding_medcpt_768 vector(768), index series_hnsw_medcpt_768
- qwen3_06b_1024_v1: embedding_qwen3_06b_1024 vector(1024), index series_hnsw_qwen3_06b_1024

Keep the existing BGE column untouched. Use the exact registry, paired MedCPT
article/query encoders, full 1,024-dimensional Qwen variant, identical document
corpus/order, manifest-pinned model revisions, resumable atomic artifacts,
artifact-scoped database coverage checks, restart-safe loading/indexing, and
whitelisted SQL identifiers defined in the plan. Do not add a generic child
table, per-field vectors, arbitrary model IDs, a public model selector, learned
routing, regression, reranking, or automatic promotion.

Enforce the comparison preflight: all three manifests must share the exact
input SHA, ordered-ID SHA, count, and document-template version. Apply the one
canonical retrieval profile, including its materialized-ANN re-sort and stable
GSE/value outer tie-breaks, so stored index settings, evaluation, and reported
provenance cannot drift.

Tasks 1–3 may proceed before the external Track 2/3/4 gates, but execute Task 1
before Task 2 and before integrating Task 3; only the isolated Task 3 storage
core may overlap Task 2 once the registry exists. Task 4 requires Track 2's
merged `SearchFilters` and filtered-retrieval APIs; they are on current main, so
verify your branch contains them before editing `pg_hybrid.py`. Embedding Task 5
waits for both embedding Task 4's variant-aware retrieval and Track 3's evaluator
code; its final seven-system comparison also waits for reviewed qrels. Embedding
Task 6 edits MCP and Docker files and must wait until embedding Tasks 3–4 and the
remote MCP Track 4 have landed, with ready manifests canonically exported from
the database. Build a candidate production image only after embedding Task 5
records the promotion decision.

Both this track's Task 1 and the MCP track's Task 1 edit `pyproject.toml` and
`uv.lock`. Coordinate ownership or merge one dependency change before the
other; do not independently regenerate and overwrite the lockfile. The selected
image must bake the manifest-pinned query model into an immutable non-root image
and prove offline dense/hybrid loading.

Unit tests must not download models or require Postgres. Keep real model builds,
database migration/loading, HNSW construction, and integration evaluation
behind the explicit plan commands and record their runtime/device/storage
facts. Never replace a completed artifact implicitly or treat unjudged pooled
results as irrelevant.

Before handoff, run all available focused and full verification commands. Give
me: commits, files changed, exact test results, artifact/database/index status
for each variant, the seven-system evaluation table when qrels are ready,
storage/latency/truncation observations, the promotion recommendation (or why
more judgments are needed), and any remaining dependency on evaluation Track 3
or remote MCP Track 4.
```
