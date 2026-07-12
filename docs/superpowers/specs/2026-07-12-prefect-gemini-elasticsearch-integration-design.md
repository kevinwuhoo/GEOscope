# Prefect Gemini and Elasticsearch Integration Design

## Goal

Extend the canonical SOFT Prefect flow so an explicitly authorized run can
update the BGE and Gemini embedding artifacts, then load the complete canonical
record and vector set into Elasticsearch. Separately, finish the already-running
Gemini corpus build, validate its published artifact, load it into the current
local Elasticsearch index, and prove full Gemini vector coverage.

## Safety defaults

Ordinary `geo-soft-etl` runs remain local, offline, and non-billing. Gemini and
Elasticsearch are enabled only by explicit command-line flags:

- `--with-gemini` adds `gemini_embedding_2_3072_v1` after the existing BGE step;
- `--allow-paid-gemini` is required whenever `--with-gemini` is used;
- `--gemini-concurrency` defaults to `1` and is forwarded only to Gemini;
- `--load-elasticsearch` enables the final network write after every requested
  embedding step succeeds.

The production command uses Gemini concurrency `4`. The flow never starts a
second Gemini coordinator against an existing temporary state directory.

## Existing contracts retained

- Canonical records remain under `data/processed/series_records`.
- Canonical artifacts remain three-file directories under
  `data/processed/embedding_artifacts/<model-key>`.
- `build_missing_embeddings(...)` remains the only embedding execution owner.
- Elasticsearch remains `geo-series`, keyed by GSE with bulk `index` actions.
- The existing mapping already contains `embedding_gemini_3072` with 3,072
  dimensions; no mapping revision or index reset is required.
- Elasticsearch credentials remain environment-only.

## Prefect flow interface

`geo_soft_etl(...)` gains keyword parameters:

```python
with_gemini: bool = False
allow_paid_gemini: bool = False
gemini_concurrency: int = 1
load_elasticsearch: bool = False
```

The CLI exposes matching flags. It rejects `--with-gemini` without
`--allow-paid-gemini` and rejects concurrency below one before discovering or
materializing records.

The flow performs these stages in order:

1. Discover and materialize missing canonical records in the existing bounded
   Prefect task pool.
2. Update `bge_small_v15` with `replace_gses=created_gses`.
3. When enabled, update `gemini_embedding_2_3072_v1` with the same replacement
   set, the paid authorization, and the configured concurrency.
4. If all requested embeddings succeed and Elasticsearch loading is enabled,
   load canonical records with every available registered artifact. The
   current corpus has BGE, MedCPT, Qwen, and—after this run—Gemini, so the load
   preserves all four vector fields.
5. Require Gemini coverage to equal the Elasticsearch document count. A
   missing artifact, partial join, bulk failure, or incomplete coverage makes
   the flow unsuccessful.

Embedding stages are serialized. Prefect's thread pool remains limited to SOFT
parsing; it does not create multiple embedding writers or multiple
Elasticsearch loaders.

## Elasticsearch preservation and coverage

Bulk `index` replaces the complete document source. Loading only Gemini would
therefore remove existing BGE, MedCPT, or Qwen fields. Both the one-time
production load and the Prefect load call `load_index(...)` with the full
registered model-key sequence so all available canonical vectors are joined
into each replacement document.

Before the Prefect Elasticsearch stage, the Gemini artifact is validated with
the existing canonical artifact loader. After bulk refresh, the flow checks:

```text
vector_coverage["embedding_gemini_3072"] == document_count
```

The one-time live upload uses the same default all-model loader behavior and
performs the same coverage check through the generated load report plus a live
Elasticsearch count query.

## Reporting

Replace the single-model embedding report fields with explicit stage reports:

```python
@dataclass(frozen=True)
class EmbeddingStepReport:
    model_key: str
    status: str | None
    error: str | None

@dataclass(frozen=True)
class ElasticsearchStepReport:
    status: str
    document_count: int | None
    vector_coverage: dict[str, int]
    error: str | None
```

`EtlReport` contains `embeddings: tuple[EmbeddingStepReport, ...]` and
`elasticsearch: ElasticsearchStepReport | None`. `succeeded` requires zero
record failures, no embedding error, and no requested Elasticsearch error.
The atomic JSON report remains `soft_etl_report.json`.

## Failure behavior

- Invalid paid/concurrency flags fail before record work.
- Every requested embedding is reported separately.
- A BGE failure prevents Gemini and Elasticsearch work.
- A Gemini failure preserves its resumable state and prevents Elasticsearch
  writes.
- Missing Elasticsearch credentials, mapping mismatch, bulk item failure, or
  incomplete Gemini coverage is reported and returns a nonzero CLI result.
- Clients are always closed. No credential value is logged or written to the
  ETL report.
- An Elasticsearch failure does not invalidate completed canonical artifacts;
  rerunning with the same flags skips/resumes embedding work and retries the
  idempotent GSE-keyed load.

## Verification

Offline tests use fake embedding builders and fake Elasticsearch clients. They
prove:

1. default ETL remains BGE-only and performs no Elasticsearch call;
2. paid authorization and concurrency validation happen before discovery;
3. BGE then Gemini execute in order with identical replacement GSEs;
4. a failed embedding prevents later paid or Elasticsearch work;
5. the Elasticsearch stage receives every registered model key;
6. full Gemini vector coverage is required for success;
7. settings/client/load failures are reported and clients close;
8. CLI flags propagate into the configured Prefect flow;
9. report JSON contains per-model and Elasticsearch evidence.

Live completion requires all of the following evidence:

- the Gemini coordinator exits zero and publishes the final artifact;
- canonical validation reports 249,736 rows, 3,072 dimensions, and the Gemini
  model key;
- a repeated Gemini build returns `status=skipped` without provider work;
- the Elasticsearch loader exits zero with no bulk failures;
- Elasticsearch document count equals canonical record count;
- `embedding_gemini_3072` coverage equals document count;
- a second identical Elasticsearch load leaves document count unchanged;
- the patched Prefect offline tests and the full repository suite pass.

## Operational credentials

The healthy local Elasticsearch container is already running. The main
worktree has no `.env.elasticsearch`, but the ignored credential file used to
start the container exists at
`.worktrees/elasticsearch-foundation/.env.elasticsearch`. The one-time load can
source that file without printing or copying any secret. Future Prefect runs
must export the same Elasticsearch variables before using
`--load-elasticsearch`.
