---
title: Incremental Corpus Future State
tags: [future, ingestion, snapshots, embeddings, elasticsearch, incremental]
status: deferred-design
created: 2026-07-11
updated: 2026-07-11
---

# 54 · Incremental Corpus Future State

← [[Home]] · deferred successor to
[[53-Prefect-SOFT-ETL-and-Embedding-Prototype-Plan]] · informs later revisions
of [[51-Search-Database-Bakeoff-and-Elasticsearch-Plan]] and
[[52-Embedding-Bakeoff-Runbook]]

## Purpose

This page preserves the production-oriented endpoint we discussed so it is not
lost while the prototype deliberately uses existence-based idempotence and one
canonical version.

**Do not implement this design during the current prototype.** Its components
become useful only after real daily operation shows that source updates,
reproducible releases, embedding reuse across record revisions, or index rollback
justify their complexity.

## Future model

Each canonical GSE record eventually carries two identities:

- `record_hash`: every field that affects Elasticsearch metadata;
- `embed_text_hash`: only the neutral text consumed by embedding adapters.

An embedding is reusable by the tuple:

```text
(model_key, model_revision, wrapper_version, embed_text_hash)
```

Consequences:

- a new GSE receives new metadata and vectors;
- a metadata-only change updates Elasticsearch but reuses vectors;
- an `embed_text` change recomputes vectors only for that GSE;
- an unchanged GSE reuses both record and vectors;
- a model change creates a new model identity rather than overwriting old
  provenance.

## Daily snapshots are manifests, not copies

A future daily snapshot is an immutable list of the exact record versions that
formed the corpus that day:

```text
data/snapshots/2026-07-12/
  manifest.jsonl
  report.json
```

Example manifest row:

```json
{
  "gse": "GSE271800",
  "record_hash": "sha256:...",
  "embed_text_hash": "sha256:...",
  "record_uri": "..."
}
```

The snapshot does not duplicate every record or vector. A complete ordered
`series.jsonl` or aligned matrix can be materialized when a full rebuild or
formal evaluation needs it.

## Future vector storage

Keep a compact base matrix for each model and append small delta shards for new
or changed embedding texts:

```text
embeddings/<model-key>/
  base-20260711/vectors.npy
  base-20260711/ids.json
  deltas/20260712/vectors.npy
  deltas/20260712/ids.json
```

The manifest resolves a GSE and embedding-text hash to the correct stored row.
Periodic compaction creates a new base matrix by copying existing vector bytes;
it does not call the model again.

## Future Elasticsearch lifecycle

Use two update modes:

1. **Daily delta:** compare snapshot manifests and bulk-upsert only new/changed
   GSEs, attaching cached or newly computed vectors. Apply intentional deletions
   separately.
2. **Full release rebuild:** build an immutable versioned Elasticsearch index,
   validate it, then atomically move `geo-series-current`. Retain the previous
   index for rollback.

The full rebuild is appropriate for mapping/analyzer changes, corruption
recovery, formal releases, and periodic consistency checks. It is not necessary
for every daily metadata increment.

## Migration from the prototype

The future migration can preserve existing work:

1. Hash the one canonical record tree from [[53-Prefect-SOFT-ETL-and-Embedding-Prototype-Plan]].
2. Hash each record's `embed_text` and register rows from the existing canonical
   model matrix artifacts under their model configuration.
3. Emit the first snapshot manifest referencing those hashes.
4. Adopt each canonical model matrix as the first base matrix without
   recomputation.
5. Replace existence-only discovery with source identity/change detection.
6. Add delta comparison and versioned-index/alias release commands.

## Triggers for implementing this

Do not start until at least one is true:

- GEO replaces already-processed SOFT metadata often enough that manual deletion
  is error-prone;
- daily updates must be reproducible or auditable by date;
- embedding costs make cross-revision reuse materially valuable;
- more than one active record/model version must coexist;
- a managed Elasticsearch release needs zero-downtime alias rollback;
- restoring the corpus from canonical artifacts has become an operational SLO.

Until then, the simpler prototype is the source of truth.
