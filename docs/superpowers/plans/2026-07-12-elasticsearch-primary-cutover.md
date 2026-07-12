# Elasticsearch Primary Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Gemini 3,072-dimensional embeddings and Elasticsearch the required primary ingestion, indexing, search CLI, and web path while retaining PostgreSQL only as historical comparison code.

**Architecture:** Extend the existing canonical Prefect flow with the existing Elasticsearch loader boundary, default all active-model configuration to `gemini_embedding_2_3072_v1`, and introduce one reusable Elasticsearch runtime adapter for CLI/web resource ownership. Preserve durable canonical and embedding artifacts so failed Elasticsearch loads retry safely.

**Tech Stack:** Python 3.11+, Prefect 3, Google Gen AI SDK, official Elasticsearch 9 client, NumPy, pytest, Markdown/Obsidian wiki.

## Global Constraints

- Elasticsearch is the only primary online datastore and search backend.
- The primary embedding model is `gemini_embedding_2_3072_v1` with 3,072 dimensions in `embedding_gemini_3072`.
- Paid Gemini corpus work requires explicit `--allow-paid-gemini`; credentials remain environment-only.
- PostgreSQL source and tests remain present but no primary command imports or connects to them.
- Offline tests require no Elasticsearch, PostgreSQL, model download, or provider request.
- Preserve unrelated `.gitignore`, `.obsidian`, `AGENTS.md`, and existing untracked-plan changes.

---

### Task 1: Default Elasticsearch and query encoding to Gemini

**Files:**
- Modify: `src/geo_index/elasticsearch_config.py`
- Modify: `src/geo_index/elasticsearch_query_embeddings.py`
- Modify: `tests/test_elasticsearch_config.py`
- Modify: `tests/test_elasticsearch_query_embeddings.py`

**Interfaces:**
- Produces `DEFAULT_ACTIVE_MODEL_KEY = "gemini_embedding_2_3072_v1"`.
- Produces `create_query_encoder("gemini_embedding_2_3072_v1")` returning a closable encoder whose `encode(query)` calls Gemini `models.embed_content` with `output_dimensionality=3072` and returns a normalized finite `float32` vector.

- [ ] **Step 1: Write failing default and Gemini encoder tests**

Add assertions equivalent to:

```python
settings = ElasticsearchSettings.from_env({
    "ELASTICSEARCH_URL": "http://localhost:9200",
    "ELASTICSEARCH_API_KEY": "elastic-key",
})
assert settings.active_model_key == "gemini_embedding_2_3072_v1"

encoder = _GeminiQueryEncoder(variant, api_key="gemini-key", client=fake)
vector = encoder.encode("immune cells")
assert fake.models.calls[0].config == {
    "task_type": "RETRIEVAL_QUERY",
    "output_dimensionality": 3072,
}
assert vector.shape == (3072,)
encoder.close()
assert fake.closed
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `uv run pytest -q tests/test_elasticsearch_config.py tests/test_elasticsearch_query_embeddings.py`

Expected: failures because the default is BGE and no Gemini query encoder exists.

- [ ] **Step 3: Implement the fixed default and Gemini encoder**

Use `GEMINI_API_KEY`, `google.genai.Client`, `types.EmbedContentConfig(task_type="RETRIEVAL_QUERY", output_dimensionality=3072)`, validate exactly one returned embedding, and reuse `validate_query_vector()`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `uv run pytest -q tests/test_elasticsearch_config.py tests/test_elasticsearch_query_embeddings.py`

Expected: all focused tests pass offline.

### Task 2: Make Elasticsearch loading a required Prefect stage

**Files:**
- Modify: `src/geo_index/prefect_etl.py`
- Modify: `tests/test_prefect_etl.py`

**Interfaces:**
- `geo_soft_etl(..., artifacts_root, allow_paid_gemini, elasticsearch_batch_size, elasticsearch_max_item_retries) -> EtlReport`.
- `EtlReport` adds Elasticsearch status/error and load/audit counters; `succeeded` requires records, embedding, and Elasticsearch success.

- [ ] **Step 1: Write failing pipeline tests**

Cover the exact model key, paid authorization propagation, load-after-embedding ordering, `load_index()` arguments, client closure, load metrics, skip-on-embedding-error, and Elasticsearch-error failure. Use fake `LoadReport` values and no network.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `uv run pytest -q tests/test_prefect_etl.py`

Expected: failures for missing report fields, arguments, and Elasticsearch stage.

- [ ] **Step 3: Implement the required indexing stage**

Set `DEFAULT_EMBEDDING_MODEL_KEY` to Gemini, pass `allow_paid_gemini`, call `ElasticsearchSettings.from_env()` and `create_client()` only after embedding succeeds, then:

```python
load_report = load_index(
    client,
    records_root=records_root,
    artifacts_root=artifacts_root,
    model_keys=(DEFAULT_EMBEDDING_MODEL_KEY,),
    batch_size=elasticsearch_batch_size,
    max_item_retries=elasticsearch_max_item_retries,
)
```

Always close the client. Serialize safe counters/errors only. Add CLI flags `--artifacts-root`, `--allow-paid-gemini`, `--elasticsearch-batch-size`, and `--elasticsearch-max-item-retries`.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `uv run pytest -q tests/test_prefect_etl.py tests/test_elasticsearch_loader.py`

Expected: all tests pass offline.

### Task 3: Cut the primary search CLI and web server over to Elasticsearch

**Files:**
- Create: `src/geo_index/elasticsearch_runtime.py`
- Create: `src/geo_index/elasticsearch_cli.py`
- Create: `tests/test_elasticsearch_runtime.py`
- Create: `tests/test_elasticsearch_cli.py`
- Modify: `src/geo_index/web.py`
- Modify: `src/geo_index/web_ui.html`
- Modify: `tests/test_web.py`
- Modify: `pyproject.toml`

**Interfaces:**
- `ElasticsearchRuntime.search(...)` and `.close()` own settings, client, and lazy query encoder.
- `geo-search QUERY [--mode ... --topk ... --filters ...]` prints the backend-neutral `SearchResponse` as JSON.
- `web._our_search()` delegates to a process-wide runtime and never imports `pg_hybrid`.

- [ ] **Step 1: Write failing runtime, CLI, and web delegation tests**

Prove BM25 avoids encoder creation, dense/hybrid initialize it once, settings use Gemini by default, resources close exactly once, CLI JSON includes Elasticsearch provenance, and web passes filters/mode/top-k to the runtime.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run pytest -q tests/test_elasticsearch_runtime.py tests/test_elasticsearch_cli.py tests/test_web.py`

Expected: collection/delegation failures because the runtime and CLI do not exist and web imports PostgreSQL.

- [ ] **Step 3: Implement the runtime, CLI, and web cutover**

Keep the public search contract closed; use existing `SearchFilters.from_mapping`. Update `geo-search` to `geo_index.elasticsearch_cli:main`, add `geo-web = "geo_index.web:main"`, and label `search_test.py` as an explicit legacy brute-force harness only. Change UI copy to “Elasticsearch BM25 + Gemini dense RRF”.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `uv run pytest -q tests/test_elasticsearch_runtime.py tests/test_elasticsearch_cli.py tests/test_web.py tests/test_elasticsearch_search.py`

Expected: all focused tests pass offline.

### Task 4: Rewrite current documentation around the Elasticsearch primary path

**Files:**
- Modify: `README.md`
- Modify: `wiki/Home.md`
- Modify: `wiki/00-Overview.md`
- Modify: `wiki/20-Architecture-Overview.md`
- Modify: `wiki/21-Ingestion-Pipeline.md`
- Modify: `wiki/23-Search-and-Retrieval.md`
- Modify: `wiki/24-Faceted-Search.md`
- Modify: `wiki/26-Datastore-Postgres.md`
- Modify: `wiki/40-Roadmap.md`
- Modify: `wiki/42-Build-Log.md`
- Modify: `wiki/51-Search-Database-Bakeoff-and-Elasticsearch-Plan.md`
- Modify: `wiki/53-Prefect-SOFT-ETL-and-Embedding-Prototype-Plan.md`
- Modify: `wiki/55-Prefect-and-Local-Elasticsearch-Coworker-Prompts.md`
- Modify: `wiki/90-Glossary.md`
- Create: `tests/test_primary_path_docs.py`

**Interfaces:**
- README is the executable primary runbook.
- Current-state wiki pages consistently name Elasticsearch and Gemini 3,072 dimensions; PostgreSQL pages are explicitly historical.

- [ ] **Step 1: Add a failing documentation consistency test**

Assert required current-state phrases and commands exist, README places the Elasticsearch runbook before a “Historical PostgreSQL baseline” heading, and architecture/current-path pages contain no claims such as “One Postgres for everything”, “Postgres-first”, or BGE as the current default.

- [ ] **Step 2: Run the docs test and verify RED**

Run: `uv run pytest -q tests/test_primary_path_docs.py`

Expected: failures identifying stale PostgreSQL-primary content.

- [ ] **Step 3: Rewrite docs and add historical markers**

Document environment variables, `uv run geo-soft-etl --allow-paid-gemini`, `uv run geo-search`, and `uv run geo-web`. Update Mermaid data flow to canonical records → Gemini artifacts → Elasticsearch → search/facets. Preserve historical measurements and plans but mark their deployment choice superseded.

- [ ] **Step 4: Run docs and full offline verification**

Run: `uv run pytest -q tests/test_primary_path_docs.py`

Run: `uv run pytest -q`

Run: `git diff --check`

Expected: all offline tests pass, opt-in integrations skip when not enabled, and diff whitespace checks pass.

### Task 5: Completion audit and commit

**Files:**
- Review all files changed by Tasks 1–4.

**Interfaces:**
- Produces evidence that every approved design requirement is implemented without deleting PostgreSQL code.

- [ ] **Step 1: Audit primary-path imports and documentation claims**

Run:

```bash
rg -n "pg_hybrid|Postgres-first|One Postgres for everything|DEFAULT_EMBEDDING_MODEL_KEY" \
  src/geo_index/prefect_etl.py src/geo_index/web.py pyproject.toml \
  README.md wiki/{Home,00-Overview,20-Architecture-Overview,21-Ingestion-Pipeline,23-Search-and-Retrieval,24-Faceted-Search,40-Roadmap}.md
```

Expected: no primary runtime PostgreSQL import; any documentation Postgres occurrence is explicitly historical; Prefect default is Gemini.

- [ ] **Step 2: Confirm PostgreSQL remains available**

Run: `test -f src/geo_index/pg_hybrid.py && uv run pytest -q tests/test_pg_hybrid.py -m 'not integration'`

Expected: file exists and offline historical tests pass.

- [ ] **Step 3: Commit the verified implementation**

Stage only the cutover files listed in this plan and commit with `feat: cut primary path over to Elasticsearch`.
