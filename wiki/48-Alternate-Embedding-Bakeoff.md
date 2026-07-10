---
title: Alternate Embedding Bake-off Proposal
tags: [embeddings, pgvector, evaluation, proposal, v1]
status: approved-design
created: 2026-07-10
---

# 48 · Alternate Embedding Bake-off Proposal

← [[Home]] · extends [[25-Embeddings-and-Cost]] · evaluated through
[[46-Retrieval-Evaluation-Plan]] · implemented by
[[49-Alternate-Embedding-Bakeoff-Implementation-Plan]]

## Decision

For this **(v1)** prototype, store one whole-document embedding column per model
variant while the models are being compared:

```sql
embedding                     vector(384)   -- existing BGE baseline
embedding_medcpt_768          vector(768)   -- candidate
embedding_qwen3_06b_1024     vector(1024)  -- candidate
```

Keep `series.embedding` exactly as it is. Add the two nullable candidate columns,
load and validate each independently, and create one cosine HNSW index per
completed column. Select the active variant through deployment configuration;
do not expose a model selector to search or MCP users.

This is a temporary model bake-off, not a move to per-field or multi-vector
retrieval. Each variant still represents the same single GSE-level narrative
document described in [[28-Embedding-Granularity]]. After evaluation, one model
becomes the production default. The unused candidate columns may remain for
reproducibility during the spike and can be dropped in a later cleanup.

## Why columns are the right prototype tradeoff

Three storage patterns were considered:

| Pattern | Strength | Cost | Decision |
|---|---|---|---|
| One column per model on `series` | Direct row alignment, simple kNN SQL, one explicit index per model | Wider table and a migration for each new model | **Use for this three-variant bake-off** |
| Child table `series_embeddings(model_key, series_id, embedding)` | Scales to many models | Mixed dimensions need model-specific expression/partial indexes and every search adds a join | Defer unless variants become long-lived |
| One table per model | Strong physical isolation | Duplicated loading, query, and lifecycle code | Reject for the prototype |

pgvector can store mixed dimensions in an untyped `vector` child-table column,
but only same-dimension rows can share an index; the documented solution uses
model-specific expression and partial indexes
([pgvector FAQ](https://github.com/pgvector/pgvector#can-i-store-vectors-with-different-dimensions-in-the-same-column)).
That flexibility is unnecessary for three fixed variants. Typed columns catch a
wrong dimension at the database boundary and keep each HNSW query obvious.

## The three deployable pipelines

The experiment compares complete query/document pipelines, not just model names:

| Variant key | Document encoder | Query encoder/policy | Dimension | Database column |
|---|---|---|---:|---|
| `bge_small_v15` | `BAAI/bge-small-en-v1.5`, current unprompted document path | Same model with the current retrieval instruction | 384 | `embedding` |
| `medcpt_v1` | `ncbi/MedCPT-Article-Encoder`, title/body pair, CLS pooling | `ncbi/MedCPT-Query-Encoder`, CLS pooling | 768 | `embedding_medcpt_768` |
| `qwen3_06b_1024_v1` | `Qwen/Qwen3-Embedding-0.6B`, no document prompt | Same model with its `query` prompt, full output dimension | 1024 | `embedding_qwen3_06b_1024` |

The BGE model card documents its short-query retrieval instruction and no
document instruction
([BGE model card](https://huggingface.co/BAAI/bge-small-en-v1.5)). MedCPT
publishes paired article and query encoders whose outputs share a
768-dimensional space; its examples use a 512-token article input, 64-token
query input, and CLS representations
([article encoder](https://huggingface.co/ncbi/MedCPT-Article-Encoder),
[query encoder](https://huggingface.co/ncbi/MedCPT-Query-Encoder)). Qwen's
0.6B model supports up to 1,024 dimensions and instructs retrieval users to
prompt queries but not documents
([Qwen model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)).

Use the full 1,024-dimensional Qwen representation first. Its lower-dimensional
MRL outputs are interesting only if the full model wins on quality but misses an
operational storage/latency goal; they are not a fourth initial database column.

## Fixed registry, not dynamic SQL

`src/geo_index/embedding_variants.py` will be the sole registry for:

- public/internal variant key;
- document and query model IDs;
- database column and HNSW index names;
- dimension and artifact prefix;
- input shape, pooling, normalization, maximum lengths, and batch size;
- query prefix or prompt policy.

Callers supply a variant key. Code resolves it before model or SQL work. SQL
identifiers come only from this registry and are still rendered with
`psycopg.sql.Identifier`; a user-provided string is never interpolated as a
column or index name.

The registry is configuration, not a claim that a build is ready. Readiness also
requires a complete artifact manifest, full database coverage, and a valid HNSW
index.

## Comparable document input

All variants use the same committed
`data/processed/geo_series.jsonl` and the same GSE order. Do not change document
composition, normalized-label injection, or corpus membership during the
bake-off; doing so would confound model and document changes.

BGE and Qwen receive the existing `embed_text`. MedCPT receives its native
two-part article input without duplicating the title:

```python
title = record["title"]
prefix = f"Title: {title}\n" if title else ""
body = record["embed_text"].removeprefix(prefix)
article = [title, body]
```

Record truncation counts and the maximum observed pre-truncation token length per
variant so a MedCPT loss can be distinguished from a model-quality loss.

## Artifact and database provenance

Each candidate first becomes a local matrix plus aligned IDs:

```text
data/processed/embeddings_medcpt_768.npy
data/processed/embeddings_medcpt_768.ids.json
data/processed/embeddings_medcpt_768.manifest.json

data/processed/embeddings_qwen3_06b_1024.npy
data/processed/embeddings_qwen3_06b_1024.ids.json
data/processed/embeddings_qwen3_06b_1024.manifest.json
```

The manifest records:

- variant and schema version;
- input JSONL SHA-256 and ID-list SHA-256;
- document-template version;
- requested and resolved model revisions;
- document/query input, prompt, max-length, pooling, and normalization policy;
- row count, dimension, dtype, vector SHA-256, and truncation statistics;
- build time and library/runtime versions.

The builder resolves the floating model revision before loading and records that
commit. A later reproduction uses the resolved revision. The existing BGE
artifact is adopted as legacy provenance: compute hashes and validate its shape,
but leave unknown historical revision/build-time fields null rather than
inventing them. Before binding that artifact hash to the existing database
column, stream and compare every stored BGE vector in artifact-ID order; counts,
dimensions, and index validity alone do not prove matrix identity.

The database stores a copy of the artifact manifest and restart state in
`embedding_variant_state`. The code registry remains authoritative for safe
identifiers; database metadata proves which artifact populated a column.
Evaluation and container builds use only canonical manifests exported back from
that ready database state, preventing a stale working copy from becoming
deployment provenance.

## Load and index lifecycle

For each candidate:

1. Build or resume its `float32` artifact.
2. Reject nonfinite, wrong-dimensional, wrong-count, misaligned, or materially
   non-normalized rows.
3. Add the typed column without touching `embedding`.
4. Load in committed batches, updating restart state in the same transaction as
   each batch.
5. Verify every artifact GSE exists and full non-null coverage equals the
   artifact count.
6. Mark the variant complete.
7. Build its HNSW cosine index only after completion.
8. Refuse dense/hybrid search if the selected variant is incomplete.

pgvector recommends building HNSW after the initial data load and notes that
HNSW trades higher memory/build time for its query speed/recall profile
([pgvector HNSW documentation](https://github.com/pgvector/pgvector#hnsw)).

## Storage budget

pgvector documents `vector` storage as `4 * dimensions + 8` bytes per value
([vector type](https://github.com/pgvector/pgvector#vector-type)). For the current
222,961 series, raw vector payload is approximately:

| Variant | Bytes/vector | Raw payload |
|---|---:|---:|
| BGE 384 | 1,544 | 328.3 MiB |
| MedCPT 768 | 3,080 | 654.9 MiB |
| Qwen 1,024 | 4,104 | 872.6 MiB |

The two new columns add about 1.49 GiB of raw vectors. Table overhead, write
amplification during loading, and two HNSW indexes require additional space, so
the operational check is measured `pg_total_relation_size` and per-index size,
not the raw estimate alone.

## Search and MCP contract

Deployment settings, the CLI, and the evaluator accept a whitelisted variant
key and resolve it once to a validated, ready registry object. Dense/hybrid
retrieval receives that ready object—not a user-supplied column—and uses its
query encoder, dimension, column, and index. BM25 remains independent of
embedding choice.

The private MCP service is configured with one `GEO_EMBEDDING_VARIANT`. It:

- lazily loads only that variant's query encoder;
- never accepts a client-supplied model or column;
- returns `embedding_variant`/`retrieval_version` in structured output;
- fails startup or readiness if the active embedding variant is not provenance-
  bound, complete, indexed, and available in the image's model cache.

This keeps model experimentation out of the public tool schema.

## Evaluation and promotion gate

Use the same reviewed query set and qrels from
[[46-Retrieval-Evaluation-Plan]]. Compare exactly seven systems:

```text
bm25
bge_small_v15/dense
bge_small_v15/hybrid
medcpt_v1/dense
medcpt_v1/hybrid
qwen3_06b_1024_v1/dense
qwen3_06b_1024_v1/hybrid
```

Pool the union of all seven systems to depth 20 and review newly surfaced
candidates. Never score an alternate model's unjudged result as irrelevant.
Report Recall@20, NDCG@10, and MRR@20 overall and by conceptual, filtered, and
exact slices, plus:

- per-query wins/losses;
- encoder-load, query-encoding, and database-only latency;
- artifact build time plus runtime/device details;
- truncation rate;
- one-worker idle/warmed/peak RSS and supported accelerator peak memory;
- table/index bytes.

For latency, lock HNSW build settings (`m=16`,
`ef_construction=64`) and query settings (`hnsw.ef_search=100`,
`hnsw.iterative_scan=relaxed_order`) across all variants. Warm each encoder and
index, interleave systems/query order with fixed seed `20260710`, run five timed
repetitions, and report median/p95 separately for encoder loading, query
encoding, and database retrieval. Quality metrics use one captured, tie-stable
ranking per system; approximate ANN candidate membership is not mislabeled as
exact retrieval.

With only 16 initial queries, close results are inconclusive. Promote a candidate
only when it:

1. improves aggregate NDCG@10 over the BGE baseline;
2. improves at least one semantic/conceptual slice;
3. does not reduce any protected `conceptual`, `filtered`, or `exact` slice by
   more than 0.05 NDCG@10;
4. stays inside the deployment's measured latency and memory budget.

Record the intended host's numeric latency, warmed RSS, and peak RSS limits in
the run configuration before applying gate 4. Measure one worker before model
load, after encoder warm-up, and with 50 ms RSS sampling during encoding; report
supported accelerator peaks separately. Without an intended host and explicit
limits, name only a provisional quality winner rather than promoting it.

If those criteria disagree or the margin is small, expand the reviewed query set
around the disagreements before switching. Do not train a selector, regression
model, or learned fusion model.

## Dependencies and parallel work

- Build the registry/adapters first. After that, artifact work and the isolated
  `embedding_store.py` core may proceed in parallel; one owner merges shared
  `pyproject.toml`/`pg_hybrid.py` integration.
- Final model comparison depends on Track 3's reviewed qrels.
- Track 4 can build its remote transport/auth/tool surface in parallel using the
  current BGE default.
- The only Track 4 integration is a later active-variant setting and
  retrieval-version output. Track 4 must not implement model storage.

## Rejected scope

- Arbitrary user-added model definitions.
- A public per-request model selector.
- Per-field or multi-vector GSE retrieval.
- Automatic promotion based on one aggregate score.
- Learned routing, regression, or reranking.
- Deleting or rewriting the current BGE column before comparison.
- Maintaining every experiment column indefinitely in a production schema.

## Sources

- BGE small v1.5 model card — https://huggingface.co/BAAI/bge-small-en-v1.5
- MedCPT Article Encoder — https://huggingface.co/ncbi/MedCPT-Article-Encoder
- MedCPT Query Encoder — https://huggingface.co/ncbi/MedCPT-Query-Encoder
- MedCPT paper — https://academic.oup.com/bioinformatics/article/39/11/btad651/7335842
- Qwen3-Embedding-0.6B model card — https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
- pgvector storage, dimensions, and indexes — https://github.com/pgvector/pgvector
- pgvector mixed-dimension indexing — https://github.com/pgvector/pgvector#can-i-store-vectors-with-different-dimensions-in-the-same-column
- pgvector HNSW guidance — https://github.com/pgvector/pgvector#hnsw
- pgvector vector storage formula — https://github.com/pgvector/pgvector#vector-type
