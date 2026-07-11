# Local Elasticsearch Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reviewable local Elasticsearch 9.4.2 service, canonical NumPy-artifact loader, and backend-neutral search adapter that can be verified with synthetic data now and used for the real corpus after ETL completes.

**Architecture:** Keep Docker/runtime configuration, index schema, artifact reading, bulk loading, and search behavior in focused modules. The loader reads canonical JSON plus aligned `vectors.npy`/`ids.json`/`metadata.json` artifacts without mutating them, while the search adapter reuses the existing filter/facet response contracts and injects one deployment-selected query encoder.

**Tech Stack:** Python 3.11+, `elasticsearch>=9.4,<10`, NumPy float32 memory maps, Docker Compose, Elasticsearch 9.4.2, pytest fake clients, opt-in live-container tests.

## Global Constraints

- Work only on branch `elasticsearch-foundation`; do not modify the Prefect worktree.
- Run tests before implementation for every behavior change.
- Use exactly one index named `geo-series` and GSE as Elasticsearch `_id`.
- Bulk writes use `index`, never `create`, so repeat loads are safe replacements.
- Bind Docker port 9200 only to `127.0.0.1` and persist data in a named volume.
- Read credentials only from `ELASTICSEARCH_URL` plus basic credentials or an API key.
- Never parse SOFT, import Prefect, download a model, call Gemini, or mutate canonical records/artifacts.
- Keep PostgreSQL modules and tests unchanged except for shared response-model extensions with defaults.
- Support only the fixed BGE 384, MedCPT 768, Qwen 1,024, and Gemini 3,072 variants.
- Use OR within one facet and AND across facets; omit a facet's own filter for its counts.
- Blank-query facet counts cover all matches; nonblank counts use a bounded retrieval pool.
- Search callers never select a model or vector field; deployment configuration selects one active model.
- Put live Elasticsearch tests behind `GEO_TEST_ELASTIC=1`.
- Do not add Elastic Cloud, Terraform, networking, DNS, MCP deployment, snapshots, dated indices, aliases, rollback releases, or vector generation.

---

## File Structure

| Path | Responsibility |
|---|---|
| `docker-compose.elasticsearch.yml` | Pinned local single-node Elasticsearch service, volume, localhost binding, health check |
| `.env.elasticsearch.example` | Safe configuration template with no credential value |
| `src/geo_index/elasticsearch_config.py` | Environment validation, fixed model-to-field registry, client construction |
| `src/geo_index/elasticsearch_index.py` | Fixed index settings/mappings and explicit lifecycle operations |
| `src/geo_index/elasticsearch_sources.py` | Read-only canonical record and NumPy artifact validation/joining |
| `src/geo_index/elasticsearch_loader.py` | Batched bulk upserts, bounded item retries, reports, loader CLI |
| `src/geo_index/elasticsearch_search.py` | Exact, BM25, dense, native RRF, filters, facets, provenance |
| `tests/test_elasticsearch_config.py` | Offline Compose/config/client tests |
| `tests/test_elasticsearch_index.py` | Offline mapping/lifecycle tests |
| `tests/test_elasticsearch_sources.py` | Synthetic JSON/NumPy validation and joining tests |
| `tests/test_elasticsearch_loader.py` | Fake-client retry, error, `_id`, and second-load tests |
| `tests/test_elasticsearch_search.py` | Fake-client retrieval, filter, facet, order, and provenance tests |
| `tests/test_elasticsearch_live.py` | Opt-in real-container synthetic ingestion and search smoke tests |
| `README.md` | Local startup, synthetic verification, deferred real-load commands |
| `pyproject.toml`, `uv.lock` | Elasticsearch client, scripts, and live-test marker |

---

### Task 1: Local Service, Configuration, and Fixed Vector Fields

**Files:**
- Create: `docker-compose.elasticsearch.yml`
- Create: `.env.elasticsearch.example`
- Modify: `.gitignore`
- Create: `src/geo_index/elasticsearch_config.py`
- Create: `tests/test_elasticsearch_config.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Produces: `INDEX_NAME`, `VectorFieldSpec`, `VECTOR_FIELDS`, `ElasticsearchSettings.from_env()`, and `create_client(settings)`.
- Consumers: index lifecycle, loader, search adapter, and live tests.

- [ ] **Step 1: Write failing configuration and Compose tests**

Create tests that parse the Compose YAML as text, validate fixed field metadata,
and exercise environment validation without network access:

```python
from pathlib import Path

import pytest

from geo_index.elasticsearch_config import (
    INDEX_NAME,
    VECTOR_FIELDS,
    ElasticsearchSettings,
)


def test_compose_pins_local_single_node_with_volume_and_healthcheck() -> None:
    text = Path("docker-compose.elasticsearch.yml").read_text()
    assert "docker.elastic.co/elasticsearch/elasticsearch:9.4.2" in text
    assert '"127.0.0.1:9200:9200"' in text
    assert "discovery.type=single-node" in text
    assert "xpack.security.http.ssl.enabled=false" in text
    assert "geo_elasticsearch_data:/usr/share/elasticsearch/data" in text
    assert "healthcheck:" in text
    assert "ELASTIC_PASSWORD" in text


def test_fixed_index_and_vector_fields() -> None:
    assert INDEX_NAME == "geo-series"
    assert {key: (spec.field, spec.dimensions) for key, spec in VECTOR_FIELDS.items()} == {
        "bge_small_v15": ("embedding_bge_384", 384),
        "medcpt_v1": ("embedding_medcpt_768", 768),
        "qwen3_06b_1024_v1": ("embedding_qwen3_06b_1024", 1024),
        "gemini_embedding_2_3072_v1": ("embedding_gemini_3072", 3072),
    }


def test_settings_require_exactly_one_credential_mode() -> None:
    with pytest.raises(ValueError, match="credentials"):
        ElasticsearchSettings.from_env({"ELASTICSEARCH_URL": "http://localhost:9200"})
    settings = ElasticsearchSettings.from_env({
        "ELASTICSEARCH_URL": "http://localhost:9200",
        "ELASTICSEARCH_USERNAME": "elastic",
        "ELASTICSEARCH_PASSWORD": "secret",
        "ELASTICSEARCH_ACTIVE_MODEL": "bge_small_v15",
    })
    assert settings.active_model_key == "bge_small_v15"
```

- [ ] **Step 2: Run the focused tests and confirm collection fails**

Run: `uv run pytest tests/test_elasticsearch_config.py -v`

Expected: FAIL because `geo_index.elasticsearch_config` and the Compose file do not exist.

- [ ] **Step 3: Add the dependency and safe environment contract**

Add `"elasticsearch>=9.4,<10"` to project dependencies and regenerate the lock
with `uv lock`. Add these script-safe values to `.env.elasticsearch.example`:

```dotenv
ELASTICSEARCH_URL=http://localhost:9200
ELASTICSEARCH_USERNAME=elastic
ELASTICSEARCH_PASSWORD=replace-with-a-local-password
ELASTICSEARCH_ACTIVE_MODEL=bge_small_v15
ELASTICSEARCH_JAVA_OPTS=-Xms1g -Xmx1g
```

Add `.env.elasticsearch` to `.gitignore`, not the example file.

- [ ] **Step 4: Add the pinned Docker Compose service**

Use this service shape:

```yaml
services:
  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:9.4.2
    container_name: geo-elasticsearch
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=true
      - xpack.security.http.ssl.enabled=false
      - ELASTIC_PASSWORD=${ELASTICSEARCH_PASSWORD:?set ELASTICSEARCH_PASSWORD}
      - ES_JAVA_OPTS=${ELASTICSEARCH_JAVA_OPTS:--Xms1g -Xmx1g}
    ports:
      - "127.0.0.1:9200:9200"
    volumes:
      - geo_elasticsearch_data:/usr/share/elasticsearch/data
    healthcheck:
      test: ["CMD-SHELL", "curl --silent --fail --user elastic:$${ELASTIC_PASSWORD} http://localhost:9200/_cluster/health?wait_for_status=yellow&timeout=5s >/dev/null"]
      interval: 10s
      timeout: 6s
      retries: 12
      start_period: 30s
    restart: unless-stopped

volumes:
  geo_elasticsearch_data:
```

- [ ] **Step 5: Implement settings, fixed fields, and client construction**

Implement immutable settings and reject unknown active variants before importing
the Elasticsearch client:

```python
@dataclass(frozen=True)
class VectorFieldSpec:
    model_key: str
    field: str
    dimensions: int


VECTOR_FIELDS = {
    "bge_small_v15": VectorFieldSpec("bge_small_v15", "embedding_bge_384", 384),
    "medcpt_v1": VectorFieldSpec("medcpt_v1", "embedding_medcpt_768", 768),
    "qwen3_06b_1024_v1": VectorFieldSpec("qwen3_06b_1024_v1", "embedding_qwen3_06b_1024", 1024),
    "gemini_embedding_2_3072_v1": VectorFieldSpec("gemini_embedding_2_3072_v1", "embedding_gemini_3072", 3072),
}


@dataclass(frozen=True)
class ElasticsearchSettings:
    url: str
    active_model_key: str
    username: str | None = None
    password: str | None = None
    api_key: str | None = None
    request_timeout: float = 30.0
    max_retries: int = 3

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ElasticsearchSettings": ...


def create_client(settings: ElasticsearchSettings):
    from elasticsearch import Elasticsearch
    auth = {"api_key": settings.api_key} if settings.api_key else {
        "basic_auth": (settings.username, settings.password)
    }
    return Elasticsearch(
        settings.url,
        request_timeout=settings.request_timeout,
        max_retries=settings.max_retries,
        retry_on_timeout=True,
        retry_on_status=(429, 502, 503, 504),
        **auth,
    )
```

Reject blank URLs, simultaneous API-key/basic auth, incomplete basic auth,
unknown active model keys, nonpositive timeouts, and negative retries.

- [ ] **Step 6: Run focused and full offline tests**

Run: `uv run pytest tests/test_elasticsearch_config.py -v`

Expected: all configuration tests PASS.

Run: `uv run pytest -m "not elastic_integration" -q`

Expected: the existing 69 tests plus new configuration tests pass; PostgreSQL integration tests remain skipped.

- [ ] **Step 7: Commit the local service boundary**

```bash
git add .gitignore .env.elasticsearch.example docker-compose.elasticsearch.yml pyproject.toml uv.lock src/geo_index/elasticsearch_config.py tests/test_elasticsearch_config.py
git commit -m "feat: configure local Elasticsearch service"
```

---

### Task 2: Explicit Index Schema and Lifecycle

**Files:**
- Create: `src/geo_index/elasticsearch_index.py`
- Create: `tests/test_elasticsearch_index.py`

**Interfaces:**
- Consumes: `INDEX_NAME`, `VECTOR_FIELDS`.
- Produces: `MAPPING_REVISION`, `index_definition()`, `ensure_index(client)`, `reset_index(client, confirm=False)`, and `index_readiness(client, active_model_key)`.

- [ ] **Step 1: Write failing mapping and lifecycle tests**

Cover exact mappings, strict dynamics, vector dimensions/options, idempotent
creation, revision mismatch, and reset confirmation:

```python
def test_index_definition_has_explicit_settings_and_all_vector_dimensions() -> None:
    definition = index_definition()
    assert definition["settings"]["number_of_shards"] == 1
    assert definition["settings"]["number_of_replicas"] == 0
    mappings = definition["mappings"]
    assert mappings["dynamic"] == "strict"
    assert mappings["properties"]["gse"] == {"type": "keyword"}
    assert mappings["properties"]["n_samples"] == {"type": "integer"}
    assert mappings["properties"]["submission_date"] == {"type": "date", "ignore_malformed": False}
    assert mappings["properties"]["embedding_bge_384"]["dims"] == 384
    assert mappings["properties"]["embedding_medcpt_768"]["dims"] == 768
    assert mappings["properties"]["embedding_qwen3_06b_1024"]["dims"] == 1024
    assert mappings["properties"]["embedding_gemini_3072"]["dims"] == 3072
    assert all(
        mappings["properties"][field]["index_options"] == {"type": "int8_hnsw"}
        for field in (
            "embedding_bge_384",
            "embedding_medcpt_768",
            "embedding_qwen3_06b_1024",
            "embedding_gemini_3072",
        )
    )
```

Use a fake `indices` object to assert `ensure_index()` creates only when absent,
accepts only the matching `_meta.mapping_revision`, and raises on a mismatch.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `uv run pytest tests/test_elasticsearch_index.py -v`

Expected: FAIL because `geo_index.elasticsearch_index` does not exist.

- [ ] **Step 3: Implement the fixed definition**

Define `MAPPING_REVISION = "geo-series-v1"`, `_meta` containing the revision and
model-key/field/dimension table, `dynamic: strict`, and these properties:

```python
TEXT_FIELDS = {
    "title": {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 1024}}},
    "summary": {"type": "text"},
    "overall_design": {"type": "text"},
    "embed_text": {"type": "text"},
}
KEYWORD_FIELDS = (
    "gse", "type", "pubmed_ids", "platform_ids", "organism_ids",
    "organism_status", "sex_ids", "sex_status", "assay_categories",
    "assay_labels", "assay_status", "organisms", "molecules",
    "source_names", "library_strategies", "library_sources",
    "library_selections",
)
```

Map the two dates, `n_samples`, and four dense vectors with `index: true`,
`element_type: float`, `similarity: cosine`, and explicit `int8_hnsw`.

- [ ] **Step 4: Implement safe lifecycle functions**

`ensure_index()` calls `indices.exists`, `indices.create`, and `indices.get_mapping`.
`reset_index()` raises unless `confirm is True`, deletes only `geo-series` with
`ignore_status=404`, then creates the fixed definition. `index_readiness()`
checks cluster info, index existence, revision, active field, and returns:

```python
@dataclass(frozen=True)
class IndexReadiness:
    ready: bool
    server_version: str
    index_name: str
    mapping_revision: str
    active_model_key: str
    active_vector_field: str
```

- [ ] **Step 5: Run focused tests**

Run: `uv run pytest tests/test_elasticsearch_index.py -v`

Expected: all mapping and lifecycle tests PASS.

- [ ] **Step 6: Commit the explicit index contract**

```bash
git add src/geo_index/elasticsearch_index.py tests/test_elasticsearch_index.py
git commit -m "feat: define canonical Elasticsearch index"
```

---

### Task 3: Canonical Record and NumPy Artifact Joining

**Files:**
- Create: `src/geo_index/elasticsearch_sources.py`
- Create: `tests/test_elasticsearch_sources.py`

**Interfaces:**
- Consumes: canonical JSON schema and `VECTOR_FIELDS`.
- Produces: `CanonicalRecord`, `EmbeddingArtifact`, `load_records(root)`, `load_artifact(path, spec)`, and `iter_index_documents(records_root, artifacts_root, model_keys)`.

- [ ] **Step 1: Write synthetic fixture helpers and failing source tests**

Use `tmp_path` to create two records (`GSE2`, `GSE10`) and artifact rows in
numeric order. Assert stable record order, path/payload GSE equality, whitelist
projection, row-ID join, partial model coverage, wrong dimensions, nonfinite
vectors, duplicate IDs, unknown keys, and metadata mismatches:

```python
def test_documents_join_vector_rows_by_gse(tmp_path: Path) -> None:
    records_root, artifacts_root = write_valid_sources(tmp_path)
    documents = list(iter_index_documents(
        records_root,
        artifacts_root,
        model_keys=("bge_small_v15",),
    ))
    assert [document.gse for document in documents] == ["GSE2", "GSE10"]
    assert documents[0].source["embedding_bge_384"] == pytest.approx([0.0] * 383 + [1.0])
    assert "samples" not in documents[0].source


def test_wrong_dimension_and_nonfinite_vectors_are_rejected(tmp_path: Path) -> None:
    artifact = write_artifact(tmp_path, dimensions=383)
    with pytest.raises(ValueError, match="384 dimensions"):
        load_artifact(artifact, VECTOR_FIELDS["bge_small_v15"])
    artifact = write_artifact(tmp_path, dimensions=384, value=float("nan"))
    with pytest.raises(ValueError, match="nonfinite"):
        load_artifact(artifact, VECTOR_FIELDS["bge_small_v15"])
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `uv run pytest tests/test_elasticsearch_sources.py -v`

Expected: FAIL because `geo_index.elasticsearch_sources` does not exist.

- [ ] **Step 3: Implement strict record projection**

Validate `^GSE[1-9][0-9]*$`, require filename stem to equal payload `gse`, reject
duplicate GSEs, and sort numerically. Project only the mapped record fields:

```python
INDEXED_RECORD_FIELDS = (
    "gse", "title", "summary", "overall_design", "embed_text", "type",
    "pubmed_ids", "submission_date", "last_update_date", "platform_ids",
    "n_samples", "organisms", "molecules", "source_names",
    "library_strategies", "library_sources", "library_selections",
    "organism_ids", "organism_status", "sex_ids", "sex_status",
    "assay_categories", "assay_labels", "assay_status",
)
```

Keep empty arrays, omit `None` optional scalars/dates, and reject non-object JSON
or a missing/blank GSE.

- [ ] **Step 4: Implement read-only artifact validation**

Memory-map `vectors.npy` with `allow_pickle=False`, require two dimensions,
float32, C-contiguity, exact registry dimension, and finite values. Require
`ids.json` to be a JSON string list with unique numeric-GSE-sorted entries and a
length equal to matrix rows. Require `metadata.json` to match `model_key`,
`dimensions`, and `record_count`. Store the ID-to-row lookup without copying the
whole matrix.

- [ ] **Step 5: Implement the record/vector join**

Define:

```python
@dataclass(frozen=True)
class IndexDocument:
    gse: str
    source: dict[str, object]


def iter_index_documents(
    records_root: Path,
    artifacts_root: Path,
    model_keys: Sequence[str],
) -> Iterator[IndexDocument]: ...
```

Reject unknown model keys. Load each requested existing artifact, permit a
missing artifact directory by reporting zero coverage later, and attach only
vectors whose IDs match a canonical record. Convert one row at a time with
`astype(float, copy=False).tolist()`.

- [ ] **Step 6: Run focused and full offline tests**

Run: `uv run pytest tests/test_elasticsearch_sources.py -v`

Expected: all source validation/join tests PASS.

Run: `uv run pytest -m "not elastic_integration" -q`

Expected: all offline tests PASS.

- [ ] **Step 7: Commit the read-only source boundary**

```bash
git add src/geo_index/elasticsearch_sources.py tests/test_elasticsearch_sources.py
git commit -m "feat: join canonical records and embedding matrices"
```

---

### Task 4: Idempotent Bulk Loader and Audit Report

**Files:**
- Create: `src/geo_index/elasticsearch_loader.py`
- Create: `tests/test_elasticsearch_loader.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `ensure_index()`, `iter_index_documents()`, `ElasticsearchSettings`.
- Produces: `BulkFailure`, `LoadReport`, `bulk_upsert(client, documents, batch_size=500, max_item_retries=3)`, `load_index(...)`, and `geo-elasticsearch-load` CLI.

- [ ] **Step 1: Write failing action, retry, accounting, and idempotence tests**

Implement a fake in-memory bulk client whose `documents` dictionary is keyed by
`_id`. Test `_id`, `index` action, retryable 429, permanent 400, mixed batches,
one refresh, second-load count stability, and coverage reporting:

```python
def test_bulk_actions_use_gse_id_and_index_operation() -> None:
    client = FakeBulkClient()
    report = bulk_upsert(client, [IndexDocument("GSE2", {"gse": "GSE2", "title": "A"})])
    assert client.operations[0] == {"index": {"_index": "geo-series", "_id": "GSE2"}}
    assert client.documents == {"GSE2": {"gse": "GSE2", "title": "A"}}
    assert report.succeeded == 1


def test_second_load_replaces_without_duplicate_documents() -> None:
    client = FakeBulkClient()
    first = [IndexDocument("GSE2", {"gse": "GSE2", "title": "first"})]
    second = [IndexDocument("GSE2", {"gse": "GSE2", "title": "second"})]
    bulk_upsert(client, first)
    bulk_upsert(client, second)
    assert len(client.documents) == 1
    assert client.documents["GSE2"]["title"] == "second"


def test_only_retryable_item_failures_are_retried() -> None:
    client = ScriptedBulkClient(statuses={"GSE2": [429, 201], "GSE10": [400]})
    report = bulk_upsert(client, documents(), max_item_retries=2)
    assert report.succeeded == 1
    assert report.retried == 1
    assert [(failure.gse, failure.status) for failure in report.failures] == [("GSE10", 400)]
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `uv run pytest tests/test_elasticsearch_loader.py -v`

Expected: FAIL because `geo_index.elasticsearch_loader` does not exist.

- [ ] **Step 3: Implement bounded raw bulk operations**

For each batch, send alternating action/source objects:

```python
operations.extend((
    {"index": {"_index": INDEX_NAME, "_id": document.gse}},
    document.source,
))
response = client.bulk(operations=operations, refresh=False)
```

Pair response items with documents in order. Count status 200/201 as success.
Retry only 429, 502, 503, and 504 items for at most `max_item_retries` rounds.
Capture permanent errors as `BulkFailure(gse, status, error_type, reason)` while
truncating the reason to 500 characters and never storing/logging `_source`.

- [ ] **Step 4: Implement the end-to-end load report**

Define:

```python
@dataclass(frozen=True)
class LoadReport:
    server_version: str
    index_name: str
    mapping_revision: str
    discovered_records: int
    attempted: int
    succeeded: int
    retried: int
    failures: tuple[BulkFailure, ...]
    document_count: int
    vector_coverage: dict[str, int]
```

`load_index()` validates settings, ensures the index, joins sources, performs
bulk writes, calls `indices.refresh(index=INDEX_NAME)` exactly once, then uses
`count` and one `exists` query per fixed vector field to populate final coverage.
It raises `LoadFailedError(report)` after writing the report when permanent
failures remain.

- [ ] **Step 5: Add the CLI without import-time network access**

Register:

```toml
geo-elasticsearch-load = "geo_index.elasticsearch_loader:main"
```

The CLI accepts `--records-root`, `--artifacts-root`, repeatable `--model-key`,
`--batch-size`, `--max-item-retries`, and `--report`. Defaults point to the
canonical paths. It creates the client only inside `main()`, writes pretty JSON
to the report path, prints counts without document text, and returns nonzero for
validation or permanent bulk failures.

- [ ] **Step 6: Run focused and full tests**

Run: `uv run pytest tests/test_elasticsearch_loader.py -v`

Expected: all loader tests PASS, including second-load idempotence.

Run: `uv run pytest -m "not elastic_integration" -q`

Expected: all offline tests PASS.

- [ ] **Step 7: Commit the loader**

```bash
git add pyproject.toml src/geo_index/elasticsearch_loader.py tests/test_elasticsearch_loader.py
git commit -m "feat: bulk upsert canonical records into Elasticsearch"
```

---

### Task 5: Backend-Neutral Search, Filters, Facets, and Provenance

**Files:**
- Create: `src/geo_index/elasticsearch_search.py`
- Create: `tests/test_elasticsearch_search.py`
- Modify: `src/geo_index/search_models.py`
- Modify: `tests/test_search_models.py`

**Interfaces:**
- Consumes: `SearchFilters`, `FACET_FIELDS`, `facet_label`, `VECTOR_FIELDS`, `INDEX_NAME`.
- Produces: `SearchProvenance`, extended `SearchResponse`, `build_filter_query(filters)`, and `ElasticsearchSearchService`.

- [ ] **Step 1: Write failing shared provenance tests**

Extend the shared response without breaking PostgreSQL callers:

```python
def test_search_response_defaults_to_no_provenance() -> None:
    assert SearchResponse(hits=()).provenance is None


def test_search_provenance_is_immutable() -> None:
    provenance = SearchProvenance(
        backend="elasticsearch",
        mapping_revision="geo-series-v1",
        active_model_key="bge_small_v15",
        vector_field="embedding_bge_384",
        dimensions=384,
        mode="hybrid",
        settings={"rank_window_size": 200},
    )
    with pytest.raises(FrozenInstanceError):
        provenance.mode = "dense"
```

Add `provenance: SearchProvenance | None = None` after `facets` in
`SearchResponse` so existing construction remains valid.

- [ ] **Step 2: Write failing search request and result tests**

Use a fake client that queues search/get responses and records kwargs. Cover:

- exact GSE lookup uses `_id` and returns `None` on 404;
- filter builder emits one `terms` clause per nonempty facet;
- multiple values remain in one clause (OR within);
- different fields become separate clauses (AND across);
- BM25 uses the frozen fields;
- dense uses only the active vector field and validates query dimension/finite values;
- hybrid uses native `rrf` with `standard` and `knn` children;
- no public `model_key` argument exists on `search()`;
- stable score-desc/GSE-asc ordering;
- blank facets use all matching documents;
- queried facets call bounded retrieval once per field with that field omitted;
- equal-count facet buckets sort alphabetically;
- provenance exposes mapping revision and active model.

```python
def test_filter_query_ors_within_and_ands_across() -> None:
    query = build_filter_query(SearchFilters(
        organism_ids=("NCBITaxon:9606", "NCBITaxon:10090"),
        sex_ids=("PATO:0000383",),
    ))
    assert query == [
        {"terms": {"organism_ids": ["NCBITaxon:9606", "NCBITaxon:10090"]}},
        {"terms": {"sex_ids": ["PATO:0000383"]}},
    ]


def test_dense_request_uses_deployment_selected_field() -> None:
    client = FakeSearchClient(search_responses=[hits("GSE2")])
    service = ElasticsearchSearchService(
        client,
        active_model_key="bge_small_v15",
        encode_query=lambda _: [0.0] * 383 + [1.0],
    )
    service.search("immune", mode="dense")
    assert client.search_calls[0]["knn"]["field"] == "embedding_bge_384"
```

- [ ] **Step 3: Run focused tests and confirm failure**

Run: `uv run pytest tests/test_search_models.py tests/test_elasticsearch_search.py -v`

Expected: FAIL because provenance and the Elasticsearch search module do not exist.

- [ ] **Step 4: Implement filters and query-vector validation**

`build_filter_query()` iterates only `FACET_FIELDS`. Empty filters return `[]`.
Validate vectors with `np.asarray(value, dtype=np.float32)`, require exact active
dimension and all finite values, and never expose field/model arguments to
callers.

- [ ] **Step 5: Implement exact, BM25, dense, and native RRF retrieval**

Create:

```python
class ElasticsearchSearchService:
    def __init__(self, client, *, active_model_key: str, encode_query: Callable[[str], Sequence[float]]): ...
    def get_dataset(self, gse: str) -> dict[str, object] | None: ...
    def search(
        self,
        query: str,
        *,
        mode: Literal["bm25", "dense", "hybrid"] = "hybrid",
        filters: SearchFilters | None = None,
        topk: int = 15,
        deep: int = 200,
        num_candidates: int = 500,
        k0: int = 60,
        facet_pool: int = 1000,
        bucket_limit: int = 50,
    ) -> SearchResponse: ...
```

BM25 uses a `bool` query with `multi_match` fields
`title^3`, `summary^2`, `overall_design`, and `embed_text`, plus filter clauses.
Dense uses `knn={field, query_vector, k, num_candidates, filter}`. Hybrid sends:

```python
{"retriever": {"rrf": {
    "retrievers": [
        {"standard": {"query": bm25_query}},
        {"knn": {"field": spec.field, "query_vector": vector, "k": deep, "num_candidates": num_candidates}},
    ],
    "filter": filter_clauses,
    "rank_constant": k0,
    "rank_window_size": deep,
}}}
```

Embed once for dense/hybrid and reuse the vector for results and facet pools.
Convert hits to the existing dict schema and sort by `(-float(_score), gse)`.

- [ ] **Step 6: Implement disjunctive facets**

For blank queries, issue one `size=0` terms-aggregation request per facet using
all filters except that facet. For nonblank queries, call the internal retrieval
method once per facet at `topk=facet_pool`, with `filters.without(field)` and
`_source` limited to the facet field. Count each value once per GSE, build labels
with `facet_label`, sort `(-count, value)`, and return `scope="all_matches"` or
`scope="candidate_pool"` with the actual candidate count.

- [ ] **Step 7: Run focused and full tests**

Run: `uv run pytest tests/test_search_models.py tests/test_elasticsearch_search.py -v`

Expected: all search, filter, facet, order, and provenance tests PASS.

Run: `uv run pytest -m "not elastic_integration" -q`

Expected: all offline tests PASS and existing PostgreSQL behavior remains green.

- [ ] **Step 8: Commit search behavior**

```bash
git add src/geo_index/search_models.py src/geo_index/elasticsearch_search.py tests/test_search_models.py tests/test_elasticsearch_search.py
git commit -m "feat: search Elasticsearch with filters and facets"
```

---

### Task 6: Opt-In Live Container Verification and Operator Documentation

**Files:**
- Create: `tests/test_elasticsearch_live.py`
- Modify: `pyproject.toml`
- Modify: `README.md`

**Interfaces:**
- Consumes: Docker service, fixed index, source joiner, loader, search service.
- Produces: `elastic_integration` marker and reproducible local operator commands.

- [ ] **Step 1: Register and write skipped-by-default live tests**

Register:

```toml
markers = [
  "integration: requires the local GEO Postgres database",
  "elastic_integration: requires local Elasticsearch and GEO_TEST_ELASTIC=1",
]
```

Create a module-level skip and a fixture that resets only `geo-series`:

```python
pytestmark = [
    pytest.mark.elastic_integration,
    pytest.mark.skipif(os.environ.get("GEO_TEST_ELASTIC") != "1", reason="set GEO_TEST_ELASTIC=1"),
]
```

Use two synthetic canonical records and deterministic 384-dimensional vectors.
Test server version `9.4.2`, green/yellow health, mapping dimensions, exact
lookup, BM25, dense, native RRF hybrid, human/mouse filters, own-filter-omitting
facets, first-load count 2, second-load count 2, and BGE vector coverage 2.

- [ ] **Step 2: Run the default suite and confirm live tests skip**

Run: `uv run pytest -q`

Expected: all offline tests pass; PostgreSQL and Elasticsearch live tests skip.

- [ ] **Step 3: Document startup and deferred real ingestion**

Add exact commands:

```bash
cp .env.elasticsearch.example .env.elasticsearch
# Edit ELASTICSEARCH_PASSWORD before starting.
docker compose --env-file .env.elasticsearch -f docker-compose.elasticsearch.yml up -d
docker compose --env-file .env.elasticsearch -f docker-compose.elasticsearch.yml ps
set -a; source .env.elasticsearch; set +a
GEO_TEST_ELASTIC=1 uv run pytest tests/test_elasticsearch_live.py -v
```

Document the later real load without executing it now:

```bash
uv run geo-elasticsearch-load \
  --records-root data/processed/series_records \
  --artifacts-root data/processed/embedding_artifacts \
  --report data/processed/elasticsearch_load_report.json
```

State that the ETL and per-model artifact builds must complete first and that a
second identical load is the production idempotence proof.

- [ ] **Step 4: Start the pinned container**

Run the Compose command, wait for health, and record:

```bash
docker version --format '{{.Server.Version}}'
docker inspect --format '{{.State.Health.Status}}' geo-elasticsearch
curl --silent --user "$ELASTICSEARCH_USERNAME:$ELASTICSEARCH_PASSWORD" "$ELASTICSEARCH_URL"
```

Expected: Docker reports its server version, container health is `healthy`, and
Elasticsearch reports `version.number` equal to `9.4.2`.

- [ ] **Step 5: Run focused live tests**

Run: `GEO_TEST_ELASTIC=1 uv run pytest tests/test_elasticsearch_live.py -v`

Expected: all synthetic container tests PASS, including second-load count 2 and
BM25/dense/hybrid/filter/facet smoke assertions.

- [ ] **Step 6: Run the exact focused and full verification sets**

Run:

```bash
uv run pytest tests/test_elasticsearch_config.py tests/test_elasticsearch_index.py tests/test_elasticsearch_sources.py tests/test_elasticsearch_loader.py tests/test_elasticsearch_search.py -v
uv run pytest -q
GEO_TEST_ELASTIC=1 uv run pytest tests/test_elasticsearch_live.py -v
```

Expected: focused and full offline suites PASS; live suite PASS when the local
container is healthy.

- [ ] **Step 7: Commit live verification and documentation**

```bash
git add README.md pyproject.toml tests/test_elasticsearch_live.py
git commit -m "test: verify local Elasticsearch foundation"
```

---

## Final Verification and Handoff Evidence

- [ ] Run `git status --short` and confirm only intentional files are present.
- [ ] Run `git log --oneline main..HEAD` and list every small commit.
- [ ] Record exact focused, full, and live test commands and outcomes.
- [ ] Record `docker version`, Elasticsearch `version.number`, and container health.
- [ ] Print `GET geo-series/_mapping` and `GET geo-series/_settings` summaries.
- [ ] Record synthetic document/vector coverage and the second-load unchanged count.
- [ ] Record exact/BM25/dense/hybrid/filter/facet synthetic smoke outputs.
- [ ] Compare shared filters/facets/order/provenance behavior with PostgreSQL tests; defer real relevance/latency parity until the canonical corpus exists.
- [ ] State the managed-deployment contract: `ELASTICSEARCH_URL` plus basic credentials or `ELASTICSEARCH_API_KEY`; no code changes.
- [ ] List real-corpus ETL/artifact availability as the only expected blocker to full ingestion metrics.
