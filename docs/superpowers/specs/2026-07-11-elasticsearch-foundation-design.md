# Local Elasticsearch Foundation Design

## Goal

Provide a reviewable local Elasticsearch 9.4.2 foundation now, independent of
the unfinished SOFT ETL and embedding builds. The implementation will include a
single-node Docker service, a read-only canonical-artifact loader, a
backend-neutral Elasticsearch search service, offline fake-client tests, and
opt-in live-container tests. Loading the complete real corpus is deferred until
the canonical JSON record tree and model artifacts are available.

## Scope

This branch owns:

- `docker-compose.elasticsearch.yml` for one Elasticsearch 9.4.2 node;
- an ignored local environment-file contract and a checked-in example;
- client construction from `ELASTICSEARCH_URL` plus either basic credentials or
  an API key;
- the fixed `geo-series` index settings and mappings;
- read-only consumption of canonical JSON records and aligned NumPy embedding
  artifacts;
- idempotent GSE-keyed bulk `index` operations with bounded retries and an
  auditable load report;
- exact lookup, BM25, filtered dense retrieval, native RRF hybrid retrieval,
  disjunctive facets, stable ordering, and retrieval provenance;
- synthetic end-to-end fixtures and tests that run without the ETL outputs;
- real-container tests gated by `GEO_TEST_ELASTIC=1`.

This branch does not own SOFT parsing, Prefect, model loading, document or query
embedding generation, Gemini calls, artifact mutation, Elasticsearch cloud
resources, MCP deployment, snapshots, aliases, or versioned releases. It keeps
the PostgreSQL implementation unchanged as the parity baseline.

## Dependency Boundary

The loader consumes only:

```text
data/processed/series_records/<bucket>/<GSE>.json
data/processed/embedding_artifacts/<model_key>/vectors.npy
data/processed/embedding_artifacts/<model_key>/ids.json
data/processed/embedding_artifacts/<model_key>/metadata.json
```

Each model artifact is validated against the fixed embedding registry. Its
matrix is a finite, C-contiguous float32 array, and `ids.json` contains the
numeric-GSE-ordered row identity. The loader memory-maps the matrix and joins
rows by GSE without changing any artifact. An artifact must be internally
complete, but different model artifacts may cover different subsets of the
record tree. The load report makes that cross-model coverage explicit.

The Elasticsearch branch will be developed independently from `main` while
matching the public `EmbeddingVariant`, `get_variant()`, and artifact metadata
contracts already committed on `prefect-soft-canonical-embeddings`. It will not
change the Prefect workflow or duplicate embedding execution.

## Local Service

Docker Compose runs exactly one pinned Elasticsearch 9.4.2 container in
single-node mode. Port 9200 binds to `127.0.0.1`, data persists in a named Docker
volume, and a container health check authenticates against the cluster health
endpoint. Credentials come from an ignored `.env.elasticsearch`; a checked-in
`.env.elasticsearch.example` contains names and safe placeholders only.

The local configuration uses security with basic authentication. Application
code never assumes localhost and never reads Docker-specific settings. It
constructs the client only from:

- `ELASTICSEARCH_URL`;
- `ELASTICSEARCH_USERNAME` and `ELASTICSEARCH_PASSWORD`, or
- `ELASTICSEARCH_API_KEY`.

The same code can therefore target a later managed deployment by changing
environment values. Client construction uses bounded request timeouts and
retries for transient transport statuses.

## Index and Loader

`geo-series` is the only index name. Its explicit settings use one primary shard
and zero replicas for the local single-node prototype. Dynamic mappings are
disabled so unknown document fields cannot silently change the schema.

The mapping includes:

- keyword `gse` for exact lookup and stable ordering;
- text `title`, `summary`, `overall_design`, and `embed_text` for BM25;
- a keyword subfield on `title`;
- keyword arrays and status fields for organism, sex, and assay filters/facets;
- integer `n_samples`;
- `submission_date` and `last_update_date` dates;
- explicit cosine `int8_hnsw` dense vectors at 384, 768, 1,024, and 3,072
  dimensions.

Record parsing whitelists mapped fields from the canonical schema. It rejects
malformed JSON, missing or mismatched GSE accessions, unknown model keys,
metadata/registry mismatches, wrong vector dimensions, nonfinite vectors, and
duplicate artifact IDs before sending invalid actions.

Bulk actions use `_op_type: index`, `_index: geo-series`, and `_id: <GSE>`.
Successful repeated actions replace the same document. Retryable item failures
are retried in bounded batches; permanent failures are counted and reported by
GSE without logging document text. The index refreshes once after the batch.
The report records server version, discovered records, attempted/succeeded/
failed actions, retry counts, final document count, and coverage for each vector
field.

Index creation is idempotent when the existing mapping revision matches. A
separate explicit local reset command may delete and recreate `geo-series` for
mapping changes or test recovery; normal loads never delete the index or
unrelated documents.

## Search Service

The Elasticsearch adapter implements the existing `SearchFilters`,
`SearchResponse`, `FacetResult`, and `FacetBucket` contracts. Callers choose
`bm25`, `dense`, or `hybrid`; they never choose a vector field. Deployment
configuration selects exactly one registry model key and its fixed field.

- Exact lookup uses GSE as the document ID, with a term-query fallback only
  where the shared service interface requires a search response.
- BM25 uses a frozen multi-field query over the mapped narrative fields.
- Dense retrieval uses filtered kNN on the active vector field.
- Hybrid retrieval uses Elasticsearch's native RRF retriever over the same BM25
  and filtered-kNN branches.
- All filter clauses use `terms`: values within one facet are ORed, while
  clauses for different facets are ANDed.

Every ranked response applies ascending GSE as the deterministic secondary
order after the primary score/rank. Response provenance includes a fixed mapping
revision, active model key, vector field, dimensions, mode, and retrieval
settings.

Facet counts preserve the current semantics. For each requested facet, the
service omits that facet's own filter and retains all other filters. Blank-query
counts aggregate across every matching document. Nonblank-query counts issue a
separate bounded retrieval for each facet, collect up to `facet_pool` documents,
and count distinct keyword values from that pool. Buckets sort by count
descending and value ascending. Counts never silently describe only the final
top-k hits.

## Error Handling and Readiness

Configuration validation fails before network access when the URL,
credentials, index name, or active model key is invalid. Readiness verifies
cluster availability, Elasticsearch version, `geo-series`, the mapping revision,
and the active vector field. Search methods enforce bounded top-k, candidate,
facet-pool, and bucket sizes. They do not accept raw Elasticsearch request
bodies or dynamic field names.

The loader continues past item-level bulk failures so the report captures all
errors in a batch, but exits unsuccessfully when any permanent failure remains.
Source records and embeddings remain untouched on every failure path.

## Testing and Delivery Sequence

Tests are written before implementation and committed in small slices:

1. Docker, configuration, fixed registry-to-field mapping, and index mapping.
2. Canonical record/artifact joins and validation.
3. Bulk action identity, retry/error accounting, and second-load idempotence.
4. Search request construction, filters, ordering, facets, and provenance.
5. Opt-in live-container creation, synthetic load/reload, and retrieval smoke
   tests.

Offline tests use fake clients and tiny NumPy/JSON fixtures. Live tests run only
when `GEO_TEST_ELASTIC=1` and operate on the local `geo-series` index using an
explicit reset fixture. The first live ingestion is synthetic, proving the
whole foundation before the real ETL completes.

After review and merge, the later operational run will start the container,
load the completed canonical record tree and available model artifacts, run the
loader twice, and report real document/vector coverage plus BM25, dense, hybrid,
filter, facet, and PostgreSQL-parity smoke results.

## Deferred Work

The following remain explicitly deferred: running the unfinished ETL, building
or downloading embeddings, Elastic Cloud, Terraform, networking, DNS,
production credentials, remote MCP deployment, content hashes, incremental
vector deltas, snapshots, dated indices, aliases, release rollback, and removal
of PostgreSQL.
