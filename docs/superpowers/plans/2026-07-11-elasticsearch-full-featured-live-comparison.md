# Elasticsearch Full-Featured Live Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a repeatable live comparison that uses real BGE, MedCPT, and Qwen query embeddings with Elasticsearch-native BM25+dense RRF, filters, disjunctive facets, stable ordering, and provenance, then writes deterministic Markdown result tables.

**Architecture:** Keep corpus loading unchanged. Add a small query-encoder adapter beside the existing document encoders, a fixed JSONL researcher-query fixture, and an internal comparison runner that constructs one fixed-model `ElasticsearchSearchService` at a time. The runner validates the live index, records standalone BM25/dense diagnostics, treats full hybrid responses as the acceptance path, and atomically renders a versioned Markdown report.

**Tech Stack:** Python 3.11+, NumPy, sentence-transformers, transformers/PyTorch, official Elasticsearch 9 client, pytest, JSONL, Markdown.

## Global Constraints

- Work only on branch `elasticsearch-foundation` in its existing linked worktree.
- Connect only through `ELASTICSEARCH_URL` plus the existing basic-auth or API-key environment variables.
- Use canonical index `geo-series`; never reset, delete, or mutate it from this comparison.
- Compare exactly `bge_small_v15`, `medcpt_v1`, and `qwen3_06b_1024_v1`; Gemini remains contextual zero coverage.
- The primary path is native RRF containing BM25 and dense kNN, with normalized filters and disjunctive facets in the returned `SearchResponse`.
- Standalone BM25 and dense runs are diagnostics, not substitutes for hybrid.
- Do not add a public model selector or change `ELASTICSEARCH_ACTIVE_MODEL` semantics.
- Guard live/model-loading execution behind `GEO_TEST_ELASTIC=1`.
- Write tests before production code and make small reviewable commits.
- The committed report must omit credentials, absolute paths, timestamps, latency, and machine-specific details.

---

### Task 1: Version and validate researcher query cases

**Files:**
- Create: `eval/elasticsearch_live_queries.jsonl`
- Create: `src/geo_index/elasticsearch_live_compare.py`
- Create: `tests/test_elasticsearch_live_compare.py`

**Interfaces:**
- Produces `LiveQueryCase(query_id: str, query: str, intent: str, filters: SearchFilters)`.
- Produces `load_query_cases(path: Path) -> tuple[LiveQueryCase, ...]`.
- Consumes `SearchFilters.from_mapping()` and the closed normalized facet names.

- [ ] **Step 1: Write failing query-loader tests**

Add tests that write temporary JSONL and assert ordered loading, filter normalization,
duplicate-ID rejection, blank query/intent rejection, malformed JSON rejection, and
unknown-filter rejection. The happy-path assertion must be:

```python
cases = load_query_cases(path)
assert [case.query_id for case in cases] == ["human_scrna", "mouse_spatial"]
assert cases[0].filters == SearchFilters(
    organism_ids=("NCBITaxon:9606",),
    assay_labels=("scRNA-seq",),
)
```

- [ ] **Step 2: Run the tests and verify RED**

```bash
uv run pytest -q tests/test_elasticsearch_live_compare.py -k query_case
```

Expected: collection fails because `geo_index.elasticsearch_live_compare` does not exist.

- [ ] **Step 3: Implement the minimal loader**

Define the frozen dataclass and line-oriented loader. Require query IDs to match
`[a-z0-9_]+`, preserve file order, reject duplicates, and wrap JSON/shape errors with
the one-based line number. Call `SearchFilters.from_mapping(raw.get("filters"))`.

- [ ] **Step 4: Add the seven approved JSONL rows**

Use the exact IDs, text, intents, and filters from the approved design:
`control_childhood_malaria`, `human_tumor_exhausted_t_cells`,
`mouse_brain_spatial_injury`, `crispr_interferon_t_cells`,
`rare_disease_fibroblasts`, `ribosome_er_stress`, and `airway_viral_infection`.

- [ ] **Step 5: Run tests and commit**

```bash
uv run pytest -q tests/test_elasticsearch_live_compare.py -k query_case
git add eval/elasticsearch_live_queries.jsonl src/geo_index/elasticsearch_live_compare.py tests/test_elasticsearch_live_compare.py
git commit -m "test: define Elasticsearch live comparison queries"
```

Expected: query-case tests pass.

### Task 2: Encode real researcher queries for all three models

**Files:**
- Create: `src/geo_index/elasticsearch_query_embeddings.py`
- Create: `tests/test_elasticsearch_query_embeddings.py`

**Interfaces:**
- Produces `QueryEncoderInfo(model_key: str, model_id: str, revision: str, dimensions: int)`.
- Produces protocol `QueryEncoder` with `.info`, `.encode(query: str) -> np.ndarray`, and `.close() -> None`.
- Produces `format_query(model_key: str, query: str) -> str`.
- Produces `validate_query_vector(model_key: str, value: object) -> np.ndarray`.
- Produces `create_query_encoder(model_key: str) -> QueryEncoder`.
- Consumes `embedding_registry.get_variant()` and the fixed Elasticsearch vector registry.

- [ ] **Step 1: Write failing pure validation tests**

Assert exact BGE, MedCPT, and Qwen query formatting; reject blank queries; return a
contiguous `float32` one-dimensional array; reject wrong dimensions, NaN, infinity,
and zero-norm vectors; and normalize a non-unit vector. Include:

```python
vector = validate_query_vector("bge_small_v15", np.ones(384))
assert vector.dtype == np.float32
assert np.linalg.norm(vector) == pytest.approx(1.0)
```

- [ ] **Step 2: Run validation tests and verify RED**

```bash
uv run pytest -q tests/test_elasticsearch_query_embeddings.py -k 'format or validate'
```

Expected: import fails because the adapter module does not exist.

- [ ] **Step 3: Implement formatting and validation**

Resolve only the three comparison model keys. Apply `variant.query_format.format(query=query)`
exactly once. Convert to `float32`, require `(variant.dimensions,)`, require finite
values and positive norm, then return the L2-normalized contiguous array.

- [ ] **Step 4: Write failing fake-model adapter tests**

Inject fake sentence-transformer and MedCPT tokenizer/model objects. Assert BGE and
Qwen receive the formatted string, sentence-transformers requests normalized NumPy
output, Qwen enables trusted remote code and left padding, MedCPT uses the query model
ID and CLS representation, revisions are recorded, and `.close()` releases model
references without contacting Elasticsearch.

- [ ] **Step 5: Run adapter tests and verify RED**

```bash
uv run pytest -q tests/test_elasticsearch_query_embeddings.py
```

Expected: adapter-construction tests fail because `create_query_encoder` is absent.

- [ ] **Step 6: Implement lazy BGE/Qwen and MedCPT adapters**

Resolve the query-model SHA through `huggingface_hub.HfApi().model_info()`. For BGE
and Qwen use `SentenceTransformer`; for MedCPT use `AutoTokenizer` and `AutoModel`,
CLS pooling, and `torch.nn.functional.normalize`. Reuse the existing local device
selection policy. Encode one query at a time, pass every output through
`validate_query_vector`, and expose immutable `QueryEncoderInfo`.

- [ ] **Step 7: Run tests and commit**

```bash
uv run pytest -q tests/test_elasticsearch_query_embeddings.py
git add src/geo_index/elasticsearch_query_embeddings.py tests/test_elasticsearch_query_embeddings.py
git commit -m "feat: encode Elasticsearch comparison queries"
```

Expected: all query-embedding tests pass without downloading models.

### Task 3: Orchestrate and validate the full Elasticsearch feature path

**Files:**
- Modify: `src/geo_index/elasticsearch_live_compare.py`
- Modify: `tests/test_elasticsearch_live_compare.py`

**Interfaces:**
- Produces `IndexSnapshot(server_version, mapping_revision, document_count, vector_coverage)`.
- Produces `FeatureCheck(name: str, passed: bool, note: str)`.
- Produces `ModelComparison(info, dense_by_query, hybrid_by_query)`.
- Produces `ComparisonRun(snapshot, cases, checks, bm25_by_query, models)`.
- Produces `inspect_index(client) -> IndexSnapshot`.
- Produces `run_comparison(client, cases, encoder_factory, *, topk=5) -> ComparisonRun`.

- [ ] **Step 1: Write failing preflight tests with a fake client**

Cover Elasticsearch version `9.4.2`, green/yellow health, mapping revision, vector
dimensions, exact document count, all three full-coverage counts, and Gemini zero
coverage. Reject red health, wrong version, wrong mapping, wrong dimensions, empty
corpus, and incomplete comparison-model coverage.

- [ ] **Step 2: Run preflight tests and verify RED**

```bash
uv run pytest -q tests/test_elasticsearch_live_compare.py -k preflight
```

Expected: failure because `inspect_index` is not defined.

- [ ] **Step 3: Implement read-only index inspection**

Use `client.info()`, `client.cluster.health(index="geo-series")`,
`client.indices.get_mapping(index="geo-series")`, and `client.count()` with exists
queries. Unwrap official response objects through `response_body`. Never call create,
delete, refresh, bulk, or update APIs.

- [ ] **Step 4: Write failing runner/feature-validation tests**

Use a recording fake service factory and encoders. Assert:

- exact and blank contract preflights run once;
- BM25 runs once per query total;
- dense and hybrid run once per model/query;
- hybrid uses `deep=100`, `num_candidates=500`, `k0=60`, `facet_pool=100`, and
  `bucket_limit=10`;
- fixture filters reach every mode unchanged;
- every hybrid response is validated for five hits, filters, score/GSE ordering,
  candidate-pool facets, bounded candidate counts, and exact provenance;
- blank facets use all-match scope and own-filter omission shows alternatives;
- encoder `.close()` executes even if a later query fails.

- [ ] **Step 5: Run runner tests and verify RED**

```bash
uv run pytest -q tests/test_elasticsearch_live_compare.py -k 'runner or feature'
```

Expected: failure because comparison orchestration is not defined.

- [ ] **Step 6: Implement the comparison runner**

Inject service and encoder factories for offline tests. Run contract preflights,
cache one BM25 result per query, then process models in fixed order. Store only
serializable response contracts and immutable encoder provenance. Close each encoder
in `finally`. Raise `ValueError` naming the feature, model, and query on the first
validation failure.

- [ ] **Step 7: Run tests and commit**

```bash
uv run pytest -q tests/test_elasticsearch_live_compare.py
git add src/geo_index/elasticsearch_live_compare.py tests/test_elasticsearch_live_compare.py
git commit -m "feat: compare full Elasticsearch search paths"
```

Expected: all offline comparison orchestration tests pass.

### Task 4: Render deterministic Markdown and expose the guarded CLI

**Files:**
- Modify: `src/geo_index/elasticsearch_live_compare.py`
- Modify: `tests/test_elasticsearch_live_compare.py`
- Modify: `pyproject.toml`
- Modify: `README.md`

**Interfaces:**
- Produces `overlap_at_five(left: SearchResponse, right: SearchResponse) -> int`.
- Produces `render_markdown(run: ComparisonRun, *, source_revision: str, query_digest: str) -> str`.
- Produces `write_report_atomic(path: Path, content: str) -> None`.
- Produces CLI `geo-elasticsearch-compare` mapped to `geo_index.elasticsearch_live_compare:main`.

- [ ] **Step 1: Write failing renderer tests**

Build a small `ComparisonRun` fixture and assert exact stable Markdown containing:
run provenance, model readiness, a PASS feature matrix, hybrid side-by-side rankings,
BM25/dense diagnostics, facet evidence, and pairwise dense/hybrid overlap. Assert
Markdown pipes/newlines in titles are escaped/collapsed and output contains no temp
path, credential, timestamp, or latency.

- [ ] **Step 2: Write failing atomic-output and CLI guard tests**

Assert an existing report is unchanged when generation raises, successful writes
replace it, `main()` refuses to run without `GEO_TEST_ELASTIC=1`, and the parser has
no `--model` option.

- [ ] **Step 3: Run renderer/CLI tests and verify RED**

```bash
uv run pytest -q tests/test_elasticsearch_live_compare.py -k 'markdown or overlap or atomic or cli'
```

Expected: failures because renderer, atomic writer, and CLI are not implemented.

- [ ] **Step 4: Implement deterministic rendering and CLI**

Render fixture order and fixed model order only. Format result cells as
`GSE — title`, collapse whitespace, escape `|`, and truncate titles at 100 characters.
Use GSE-set intersections for overlap. Write via `tempfile.NamedTemporaryFile` in
the destination directory and `os.replace`; unlink the temporary file on error.
The CLI loads environment settings, cases, and Git HEAD, runs the comparison, writes
only on success, closes the client, and returns nonzero with a sanitized stderr line
on validation errors.

- [ ] **Step 5: Register and document the command**

Add:

```toml
geo-elasticsearch-compare = "geo_index.elasticsearch_live_compare:main"
```

Document the explicit `GEO_TEST_ELASTIC=1` invocation, the three fixed models, the
read-only index contract, and the versioned report path in `README.md`.

- [ ] **Step 6: Run tests and commit**

```bash
uv lock
uv run pytest -q tests/test_elasticsearch_query_embeddings.py tests/test_elasticsearch_live_compare.py
git add pyproject.toml uv.lock README.md src/geo_index/elasticsearch_live_compare.py tests/test_elasticsearch_live_compare.py
git commit -m "feat: report Elasticsearch live comparisons"
```

Expected: focused tests pass and the lockfile is current.

### Task 5: Verify offline, run live, and commit the comparison report

**Files:**
- Create: `eval/elasticsearch-live-comparison.md`
- Modify only if a test-first live defect is found: implementation and matching test files.

**Interfaces:**
- Consumes CLI and fixture from Tasks 1–4.
- Produces the reviewable live result report.

- [ ] **Step 1: Run focused and full offline verification**

```bash
uv sync --frozen
uv run pytest -q tests/test_elasticsearch_query_embeddings.py tests/test_elasticsearch_live_compare.py tests/test_elasticsearch_search.py
uv run pytest -q
```

Expected: focused tests pass; full suite passes with only the eight existing opt-in
Elasticsearch/PostgreSQL skips.

- [ ] **Step 2: Verify the live container without mutating it**

```bash
docker inspect --format 'status={{.State.Status}} health={{.State.Health.Status}} restarts={{.RestartCount}}' geo-elasticsearch
```

Expected: `status=running health=healthy restarts=0`.

- [ ] **Step 3: Run the real three-model comparison**

```bash
set -a
source .env.elasticsearch
set +a
GEO_TEST_ELASTIC=1 uv run geo-elasticsearch-compare \
  --queries eval/elasticsearch_live_queries.jsonl \
  --topk 5 \
  --output eval/elasticsearch-live-comparison.md
```

Expected: exit zero after loading BGE, MedCPT, and Qwen sequentially and writing the
Markdown report. The command must not print credentials.

- [ ] **Step 4: Inspect the report and verify corpus immutability**

```bash
sed -n '1,320p' eval/elasticsearch-live-comparison.md
docker exec geo-elasticsearch sh -c 'curl --silent --user "elastic:$ELASTIC_PASSWORD" http://localhost:9200/geo-series/_count'
```

Expected: the report contains seven query sections, all three models, PASS feature
evidence, and side-by-side tables; document count remains `249736`.

- [ ] **Step 5: Run final verification and commit results**

```bash
uv run pytest -q
git diff --check
git status --short
git add eval/elasticsearch-live-comparison.md
git commit -m "docs: record Elasticsearch live comparison"
```

Expected: tests pass, no whitespace errors, only intended files are committed, and
the branch remains ready for review.

