# Prefect Gemini and Elasticsearch Completion Safety Design

**Status:** Approved as a refinement of the approved Elasticsearch Primary
Cutover Design (`998d686`)

## Goal

Finish the already-running Gemini corpus build, validate and upload the complete
artifact to Elasticsearch, and harden the primary Prefect pipeline so every
future successful run builds/resumes Gemini and proves it is fully represented
in Elasticsearch.

## Relationship to the primary cutover

The approved primary-cutover design is authoritative: Gemini and Elasticsearch
are required stages of `geo-soft-etl`, not optional extensions. This refinement
adds missing production guarantees without reversing that decision:

- paid Gemini still requires `--allow-paid-gemini`;
- `--gemini-concurrency` defaults to `1` and production uses `4`;
- Elasticsearch replacement writes preserve every available registered vector
  artifact, not only Gemini;
- a successful flow requires Gemini vector coverage equal to document count.

## Prefect contract

The primary flow remains:

```text
SOFT -> canonical records -> Gemini artifact -> Elasticsearch -> audited report
```

`geo_soft_etl(...)` accepts the existing roots and Elasticsearch retry bounds,
plus `gemini_concurrency: int = 1`. The CLI exposes
`--gemini-concurrency`. Concurrency below one or missing paid authorization
fails before record discovery.

The flow calls `build_missing_embeddings(...)` once for
`gemini_embedding_2_3072_v1`, forwarding:

```python
replace_gses=frozenset(created_gses)
allow_paid_gemini=allow_paid_gemini
gemini_concurrency=gemini_concurrency
```

It never creates a second writer for the same Gemini temporary state.

## Elasticsearch preservation

The loader uses bulk `index`, which replaces the complete document source.
Passing only the Gemini model key would remove BGE, MedCPT, and Qwen vector
fields already present on each document. The primary Prefect stage therefore
passes `model_keys=tuple(VECTOR_FIELDS)` so every available canonical artifact
is joined into each replacement source.

The current artifact root contains complete BGE, MedCPT, and Qwen artifacts.
After the active build finishes it will also contain Gemini. The Prefect stage
validates the Gemini artifact before creating the Elasticsearch client. Other
registered artifacts retain the loader's existing availability behavior, but
the primary Gemini artifact is mandatory.

## Coverage and reporting

After `load_index(...)` refreshes the index, the flow reads:

```text
document_count
vector_coverage["embedding_gemini_3072"]
```

The two values must be equal. Otherwise the Elasticsearch stage is reported as
failed with an incomplete-coverage error and `EtlReport.succeeded` is false.
Existing load counters and error fields remain the public report shape; no
second report abstraction is introduced.

The report never includes API keys, passwords, or raw client configuration.
The Elasticsearch client always closes in `finally`.

## Failure behavior

- Invalid concurrency or missing paid authorization fails before discovery.
- Record or Gemini failure prevents Elasticsearch client construction.
- Missing/invalid Gemini artifact prevents Elasticsearch writes.
- Settings, connection, mapping, bulk, or coverage failure is captured in the
  terminal ETL report and returns nonzero.
- Completed records and artifacts remain durable for retry.
- Re-running the GSE-keyed loader is idempotent.

## Testing

Offline fake-only tests prove:

1. paid authorization and concurrency validate before discovery;
2. Gemini concurrency reaches `build_missing_embeddings`;
3. Elasticsearch starts only after Gemini succeeds;
4. the loader receives every registered model key;
5. the Gemini artifact is mandatory;
6. incomplete Gemini coverage makes the flow unsuccessful;
7. client closure and report counters/errors are deterministic;
8. CLI flags propagate into the configured Prefect flow.

Full offline verification must not source credentials or contact Google or
Elasticsearch.

## Live completion evidence

The goal is complete only after all evidence exists:

- the existing Gemini coordinator exits zero;
- the final artifact validates as 249,736 rows by 3,072 dimensions for
  `gemini_embedding_2_3072_v1`;
- a repeated builder invocation returns `status=skipped` without provider work;
- the all-model Elasticsearch loader reports no failures;
- Elasticsearch document count is 249,736;
- `embedding_gemini_3072` coverage is 249,736;
- a second identical load leaves both counts unchanged;
- an end-to-end `geo-soft-etl --allow-paid-gemini --gemini-concurrency 4` run
  skips the complete artifact, reloads Elasticsearch, reports full coverage,
  and exits zero;
- focused and full offline tests pass.

The local Elasticsearch container is healthy. Its ignored credential file is
available at `.worktrees/elasticsearch-foundation/.env.elasticsearch` and can
be sourced for the live load without printing or copying secrets.
