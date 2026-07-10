---
title: Alternate Embedding Bake-off Implementation Plan
tags: [embeddings, pgvector, evaluation, postgres, plan, v1]
status: implementation-plan
created: 2026-07-10
---

# 49 · Alternate Embedding Bake-off Implementation Plan

← [[Home]] · implements [[48-Alternate-Embedding-Bakeoff]] · extends
[[46-Retrieval-Evaluation-Plan]] · integrates with [[47-MCP-Server-Plan]]

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, store, search, and fairly evaluate MedCPT and Qwen embedding
variants beside the existing BGE baseline without overwriting the production
column or changing the public search/MCP contract.

**Scope:** This is a **(v1)** model-selection track. It compares alternative
single whole-document vectors and does not add multi-vector retrieval.

**Architecture:** A frozen code registry owns every model, dimension, artifact,
column, index, and query-policy choice. Model-specific adapters produce
normalized `float32` document/query vectors; a resumable artifact builder records
complete provenance; a restartable database loader populates typed columns and
builds HNSW only after full coverage. Deployment/CLI/evaluation boundaries
resolve a whitelisted key once; internal retrieval receives the validated ready
object. Track 3 compares seven fixed retrieval systems and Track 4 exposes only
one deployment-selected active variant.

**Tech Stack:** Python 3.11+, NumPy memmaps, sentence-transformers,
Transformers 4.51+, Hugging Face Hub, PyTorch, psutil RSS sampling, psycopg 3,
PostgreSQL/pgvector HNSW, pytest.

## Global Constraints

- Preserve `series.embedding vector(384)` and its `series_hnsw` index.
- Add exactly `embedding_medcpt_768 vector(768)` and
  `embedding_qwen3_06b_1024 vector(1024)` for the first bake-off.
- Use exactly the variant keys `bge_small_v15`, `medcpt_v1`, and
  `qwen3_06b_1024_v1`.
- Compare the same `geo_series.jsonl` rows, GSE order, and document composition.
- Before pooling/scoring, require identical input SHA, ordered-ID SHA, count,
  and document-template version across all three manifests.
- Produce one whole-document vector per GSE per model; do not add field vectors.
- Resolve a floating Hugging Face revision to a commit before encoding and record
  both values.
- Normalize all stored/query vectors and reject nonfinite or wrong-dimensional
  arrays.
- Never accept a caller-provided SQL identifier, model ID, artifact path, or
  prompt. Resolve only registry keys.
- Do not build or query a candidate HNSW index until its load state is complete.
- BM25 remains embedding-independent and runs only once in multi-model eval.
- Apply the frozen GSE/value secondary order to lexical cutoffs, final results,
  facet buckets, and the materialized ANN candidate set. HNSW membership remains
  approximate; do not claim exact deterministic membership at its inner cutoff.
- Re-pool and review newly surfaced candidates; never score unjudged results as
  irrelevant.
- Promote a candidate only if aggregate NDCG@10 improves over BGE, at least one
  conceptual slice improves, no protected `conceptual`, `filtered`, or `exact`
  slice loses more than 0.05 NDCG@10, and the measured deployment
  latency/memory budget is met.
- Do not train a selector, regression/ranking model, learned fusion, or reranker.
- Do not expose a model selector through MCP.

---

## Locked file structure

| Path | Responsibility |
|---|---|
| `src/geo_index/embedding_variants.py` | Frozen lightweight registry; no model imports |
| `src/geo_index/embedding_encoders.py` | BGE/Qwen sentence-transformer and MedCPT adapters |
| `src/geo_index/embedding_artifacts.py` | Manifest/state types, hashes, atomic persistence |
| `src/geo_index/build_embeddings.py` | Resumable build/adopt CLI orchestration |
| `src/geo_index/embedding_store.py` | Schema migration, restartable load, readiness, HNSW |
| `src/geo_index/retrieval_profile.py` | Frozen algorithm/tuning and stable tie policy |
| `src/geo_index/pg_hybrid.py` | Variant-aware internal dense/hybrid SQL |
| `src/geo_index/web.py` | Deployment-selected local demo variant |
| `src/geo_index/retrieval_eval.py` | Seven-system pooling, scoring, and reports after Track 3 |
| `src/geo_index/search_service.py` | One active query encoder after Track 4 lands |
| `src/geo_index/mcp_models.py` | Output-only retrieval provenance |
| `tests/test_embedding_variants.py` | Registry invariants |
| `tests/test_embedding_encoders.py` | Adapter behavior with fake models |
| `tests/test_embedding_artifacts.py` | Hash/state/finalization behavior |
| `tests/test_build_embeddings.py` | Resume and CLI orchestration |
| `tests/test_embedding_store.py` | Safe SQL, state transitions, failure recovery |
| `tests/test_retrieval_profile.py` | Profile immutability, serialization, and exact values |
| `tests/test_pg_hybrid_variants.py` | Column routing and vector validation |
| `tests/test_retrieval_eval_variants.py` | Pool/run reuse and report separation |
| `eval/embedding_manifests/` | Committed lightweight manifests used by official runs |

Files are split by responsibility so importing the registry never imports
PyTorch or downloads a model, and database migrations never need encoder code.

## Dependency and ownership graph

The embedding coworker should own this plan end to end. If its tasks are split
further, use these merge boundaries:

```text
Task 1 registry/adapters ──→ Task 2 artifacts
          │
          └───────────────→ Task 3 store core

Track 2 filters + Task 3 ──→ Task 4 retrieval integration
Track 3 eval + Task 4 ─────→ Task 5 seven-system comparison
Track 4 MCP + Task 3 ready/exported manifests + Task 4 ──→ Task 6 integration
Task 5 promotion decision ───────────────────────────────→ candidate image
```

One integration owner handles shared files:

- `pyproject.toml`/`uv.lock` after Tasks 1 and 3;
- `pg_hybrid.py` after Tasks 3 and 4;
- `retrieval_eval.py` only after the Track 3 owner has merged;
- `mcp_settings.py`/`search_service.py`/`mcp_models.py`/`mcp_server.py` only
  after the Track 4 owner has merged.

Track 4 Task 1 also edits `pyproject.toml`/`uv.lock`. Before embedding Task 1
regenerates the lockfile, coordinate ownership with the MCP owner or merge one
dependency commit first; never overwrite two independently generated locks.

Do not have two workers edit a shared file concurrently. Tasks 1→2 are
sequential; the isolated `embedding_store.py` core may be developed after Task 1
while Task 2 runs, but its CLI/schema integration is merged by the single
embedding owner.

### Task 1: Freeze the variant registry and model adapters

**Files:**
- Create: `src/geo_index/embedding_variants.py`
- Create: `src/geo_index/embedding_encoders.py`
- Create: `src/geo_index/retrieval_profile.py`
- Create: `tests/test_embedding_variants.py`
- Create: `tests/test_embedding_encoders.py`
- Create: `tests/test_retrieval_profile.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Produces: `EmbeddingVariant`, `VARIANTS`,
  `DEFAULT_VARIANT = "bge_small_v15"`,
  `LEGACY_BGE_QUERY_REVISION` fixed to
  `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`, and
  `get_variant(key: str) -> EmbeddingVariant`.
- Produces: `resolve_revisions(spec) -> ResolvedModels`,
  `load_document_encoder(spec, resolved, device) -> DocumentEncoder`, and
  `load_query_encoder(spec, resolved, device) -> QueryEncoder`.
- Produces:
  `EncodedDocuments(vectors: np.ndarray, truncated_count: int, max_observed_tokens: int)`.
- Every encoder exposes its immutable `spec`, exact `resolved_revision`, and
  effective prompt/prefix metadata.
- Encoder contract:
  `encode_documents(records: Sequence[dict]) -> EncodedDocuments` and
  `encode_queries(texts: Sequence[str]) -> np.ndarray`.
- Produces:
  `encode_one_query(encoder: QueryEncoder, text: str) -> np.ndarray`.
- Produces:
  `prefetch_query_assets(spec, resolved_query_revision, cache_dir) -> None`,
  `verify_cached_query_assets(spec, resolved_query_revision, cache_dir) -> Path`,
  and
  a `python -m geo_index.embedding_encoders prefetch-query` CLI used only while
  building the hosted image.
- Produces a frozen `RetrievalProfile` and the canonical
  `RETRIEVAL_PROFILE_V1` with the exact hidden tuning values specified in Track
  4. Index build/readiness, core retrieval, evaluation, and later MCP
  integration all import this one object.

- [ ] **Step 1: Add directly imported model dependencies**

```bash
uv add "transformers>=4.51,<5" "huggingface-hub>=0.34,<1" "psutil>=7,<8"
```

Qwen's official usage requires Transformers 4.51 or newer
([model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)). Keep the
existing sentence-transformers dependency.

- [ ] **Step 2: Write failing registry tests**

```python
import pytest

from geo_index.embedding_variants import DEFAULT_VARIANT, VARIANTS, get_variant


def test_registry_is_exact_and_identifiers_are_unique():
    assert DEFAULT_VARIANT == "bge_small_v15"
    assert set(VARIANTS) == {
        "bge_small_v15",
        "medcpt_v1",
        "qwen3_06b_1024_v1",
    }
    assert [VARIANTS[key].dimension for key in VARIANTS] == [384, 768, 1024]
    assert len({item.column for item in VARIANTS.values()}) == 3
    assert len({item.index_name for item in VARIANTS.values()}) == 3


def test_unknown_variant_fails_before_other_work():
    with pytest.raises(ValueError, match="unknown embedding variant"):
        get_variant("arbitrary/model")
```

Also assert the exact model IDs, artifact prefixes, max lengths, query
instruction/prompt choices, and database names in the table below.
Assert `LEGACY_BGE_QUERY_REVISION` is the exact 40-character SHA above; this is
the same compatibility query revision frozen by Track 4.
In `tests/test_retrieval_profile.py`, assert every exact v1 field/value,
canonical serialization, and that mutation is impossible.

- [ ] **Step 3: Implement the frozen registry**

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class EmbeddingVariant:
    key: str
    dimension: int
    column: str
    index_name: str
    artifact_prefix: Path
    backend: Literal["sentence_transformer", "medcpt"]
    document_model: str
    query_model: str
    document_max_length: int
    query_max_length: int
    query_prefix: str | None = None
    query_prompt_name: str | None = None
    document_input: Literal["combined", "title_body_pair"] = "combined"
    default_batch_size: int = 32


VARIANTS = {
    "bge_small_v15": EmbeddingVariant(
        key="bge_small_v15",
        dimension=384,
        column="embedding",
        index_name="series_hnsw",
        artifact_prefix=Path("data/processed/embeddings"),
        backend="sentence_transformer",
        document_model="BAAI/bge-small-en-v1.5",
        query_model="BAAI/bge-small-en-v1.5",
        document_max_length=512,
        query_max_length=512,
        query_prefix="Represent this sentence for searching relevant passages: ",
        default_batch_size=64,
    ),
    "medcpt_v1": EmbeddingVariant(
        key="medcpt_v1",
        dimension=768,
        column="embedding_medcpt_768",
        index_name="series_hnsw_medcpt_768",
        artifact_prefix=Path("data/processed/embeddings_medcpt_768"),
        backend="medcpt",
        document_model="ncbi/MedCPT-Article-Encoder",
        query_model="ncbi/MedCPT-Query-Encoder",
        document_max_length=512,
        query_max_length=64,
        document_input="title_body_pair",
        default_batch_size=32,
    ),
    "qwen3_06b_1024_v1": EmbeddingVariant(
        key="qwen3_06b_1024_v1",
        dimension=1024,
        column="embedding_qwen3_06b_1024",
        index_name="series_hnsw_qwen3_06b_1024",
        artifact_prefix=Path("data/processed/embeddings_qwen3_06b_1024"),
        backend="sentence_transformer",
        document_model="Qwen/Qwen3-Embedding-0.6B",
        query_model="Qwen/Qwen3-Embedding-0.6B",
        document_max_length=8192,
        query_max_length=8192,
        query_prompt_name="query",
        default_batch_size=4,
    ),
}
```

`get_variant` strips no input and performs no fuzzy matching. The exact key must
exist.

In `retrieval_profile.py`, define one frozen typed value with fusion `rrf-v1`,
cosine distance, `deep=200`, `k0=60`, `facet_pool=1000`, HNSW `m=16`,
`ef_construction=64`, `ef_search=100`, and
`iterative_scan="relaxed_order"`, plus
`result_tie_breaker="gse_asc_after_ann_materialization"` and
`facet_tie_breaker="value_asc"`, `bm25_version="bm25-v2"`, and
`blank_facet_version="facet-all-matches-v1"`. `bm25-v2` is an intentional bump
from Track 4's score-only `bm25-v1` because embedding Task 4 makes tied BM25
cutoffs stable. Do not duplicate these defaults in another module or change
their semantics without creating a new profile/version.

- [ ] **Step 4: Write adapter tests with fake encoders**

No unit test downloads a model. Inject fake sentence-transformer,
tokenizer/model, and revision resolver objects. Assert:

```python
def test_qwen_prompts_only_queries(fake_sentence_transformer, qwen_spec):
    encoder = load_query_encoder(
        qwen_spec, RESOLVED_QWEN, "cpu",
        sentence_transformer_factory=lambda *a, **k: fake_sentence_transformer,
    )
    vector = encoder.encode_queries(["find PBMC studies"])
    assert fake_sentence_transformer.calls[-1]["prompt_name"] == "query"
    assert vector.shape == (1, 1024)


def test_medcpt_uses_title_body_and_cls(fake_medcpt, medcpt_spec):
    encoder = load_document_encoder(
        medcpt_spec, RESOLVED_MEDCPT, "cpu",
        tokenizer_factory=fake_medcpt.tokenizer_factory,
        model_factory=fake_medcpt.model_factory,
    )
    batch = encoder.encode_documents(
        [{"title": "Study", "embed_text": "Title: Study\nSummary: Body"}]
    )
    assert fake_medcpt.tokenizer_inputs == [["Study", "Summary: Body"]]
    assert fake_medcpt.max_length == 512
    assert batch.vectors.shape == (1, 768)
    assert batch.truncated_count == 0
    assert batch.max_observed_tokens > 0
    assert np.allclose(np.linalg.norm(batch.vectors, axis=1), 1.0)
```

Add tests that BGE preserves the existing query prefix, documents receive no
query prompt, MedCPT queries use max length 64, every output is finite
`float32`, and wrong output dimensions raise `ValueError`. With fake model
factories and an empty temporary cache, test that `prefetch-query` selects only
the registered query model, uses the supplied resolved SHA rather than `main`,
rejects a manifest for a different variant, and writes an atomic image marker
with variant/model/query SHA/vector artifact hash. Test
`verify_cached_query_assets` with network access monkeypatched to fail: the
exact cached SHA succeeds via `local_files_only=True` and missing/wrong SHAs
fail. Hugging Face documents commit revisions, cache directories, and
`local_files_only` behavior in its
[download API](https://huggingface.co/docs/huggingface_hub/en/package_reference/file_download).

- [ ] **Step 5: Implement adapters and revision resolution**

`resolve_revisions` calls `huggingface_hub.model_info(model_id,
revision=requested).sha` once per distinct model and returns both requested
`"main"` and resolved SHA. Model constructors receive the resolved SHA.
`encode_one_query` calls `encode_queries([text])`, validates the resulting
`(1, dimension)` finite `float32` array, and returns row zero. It does not accept
a second variant key that could disagree with the encoder's own `spec`.

Sentence-transformer adapters call `encode(..., normalize_embeddings=True,
convert_to_numpy=True)`. Document adapters count tokenizer inputs longer than
their registered maximum and return that count with the vectors. BGE prepends
`query_prefix` only to queries. Qwen passes `prompt_name="query"` only for
queries. Before any encoding, set `model.max_seq_length` to the registry's
document/query maximum. Count truncation with the same tokenizer, prompt,
special-token policy, and maximum used for inference.

MedCPT uses `AutoTokenizer`/`AutoModel`, `model.eval()`,
`torch.inference_mode()`, the CLS vector
`last_hidden_state[:, 0, :]`, and `torch.nn.functional.normalize`. For an article:

```python
title = str(record.get("title") or "")
prefix = f"Title: {title}\n" if title else ""
body = str(record["embed_text"]).removeprefix(prefix)
return [title, body]
```

The MedCPT model cards specify paired encoders in the same 768-dimensional space
([article](https://huggingface.co/ncbi/MedCPT-Article-Encoder),
[query](https://huggingface.co/ncbi/MedCPT-Query-Encoder)).

The prefetch CLI accepts only a registry key, a committed manifest path, and a
cache directory, plus an image-marker destination. It validates that the
manifest key/model/revision match the registry, then instantiates the query
encoder at the manifest-pinned resolved revision to populate that cache. It
atomically writes a marker containing schema version, variant, query model,
resolved query SHA, vector artifact hash, and manifest SHA-256. It never accepts
an arbitrary Hugging Face model ID and never silently re-resolves `main`.

- [ ] **Step 6: Run and commit**

```bash
uv run pytest tests/test_embedding_variants.py tests/test_embedding_encoders.py tests/test_retrieval_profile.py -v
git add pyproject.toml uv.lock src/geo_index/embedding_variants.py src/geo_index/embedding_encoders.py src/geo_index/retrieval_profile.py tests/test_embedding_variants.py tests/test_embedding_encoders.py tests/test_retrieval_profile.py
git commit -m "feat: define embedding variants"
```

### Task 2: Build resumable, provenance-complete artifacts

**Files:**
- Create: `src/geo_index/embedding_artifacts.py`
- Modify: `src/geo_index/build_embeddings.py`
- Create: `tests/test_embedding_artifacts.py`
- Create: `tests/test_build_embeddings.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `BuildState` and `EmbeddingManifest`.
- Produces: `sha256_file(path) -> str`,
  `validate_artifact(spec, prefix) -> EmbeddingManifest`, and atomic JSON writes.
- CLI:
  `geo-embed build --variant KEY [--resume]` and
  `geo-embed adopt-legacy --variant bge_small_v15`.

- [ ] **Step 1: Write failing state/finalization tests**

Use a three-record JSONL and a fake two-dimensional test spec/encoder injected
directly into the builder. Assert:

```python
def test_resume_starts_after_committed_batch(tmp_path, fake_encoder):
    first = build_variant(
        spec=TEST_SPEC,
        input_path=FIXTURE,
        out_prefix=tmp_path / "candidate",
        encoder=fake_encoder,
        stop_after_batches=1,
    )
    assert first.complete is False
    assert first.next_row == 2

    second = build_variant(
        spec=TEST_SPEC,
        input_path=FIXTURE,
        out_prefix=tmp_path / "candidate",
        encoder=fake_encoder,
        resume=True,
    )
    assert second.complete is True
    assert fake_encoder.encoded_ids == ["GSE1", "GSE2", "GSE3"]
```

The fake records already encoded before the crash must not be encoded again.
Add tests rejecting changed input hash, variant, dimension, resolved model SHA,
ID order, NaN/Infinity, wrong shape, and materially non-unit norms. Also prove a
second process cannot acquire the artifact lock, a mismatched existing final
artifact is never overwritten, and consumers reject final vector/ID files until
their manifest exists and validates. Inject a failure after each ID rename,
vector rename, manifest write, and state removal; every resulting file-state
combination must either resume idempotently or fail closed without overwriting a
final file.

- [ ] **Step 2: Define manifest and state schemas**

`EmbeddingManifest` serializes this complete shape:

```json
{
  "schema_version": 1,
  "variant": "medcpt_v1",
  "dimension": 768,
  "dtype": "float32",
  "normalized": true,
  "count": 222961,
  "input": {
    "path": "data/processed/geo_series.jsonl",
    "sha256": "hex",
    "document_template_version": "geo-series-embed-text-v1"
  },
  "document_encoder": {
    "model": "ncbi/MedCPT-Article-Encoder",
    "requested_revision": "main",
    "resolved_revision": "hex",
    "max_length": 512,
    "pooling": "cls",
    "input": "title_body_pair"
  },
  "query_encoder": {
    "model": "ncbi/MedCPT-Query-Encoder",
    "requested_revision": "main",
    "resolved_revision": "hex",
    "max_length": 64,
    "pooling": "cls",
    "query_policy": {"kind": "none", "prompt_name": null, "prompt_text": null}
  },
  "ids_sha256": "hex",
  "vectors_sha256": "hex",
  "truncation": {
    "documents_truncated": 0,
    "documents_total": 222961,
    "max_observed_tokens": 0
  },
  "built_at": "UTC ISO-8601",
  "build_seconds": 0.0,
  "runtime": {
    "python": "version",
    "numpy": "version",
    "torch": "version",
    "transformers": "version",
    "sentence_transformers": "version"
  },
  "provenance_status": "complete"
}
```

`BuildState` includes schema version, variant, input hash, resolved model SHAs,
dimension, count, next row, temporary matrix path, and start time.
For BGE, `query_policy` records the exact prefix. For Qwen it records
`kind="prompt"`, `prompt_name="query"`, and the exact prompt text read from the
resolved model's `model.prompts`. MedCPT records `kind="none"`. Truncation
metadata records both the count and maximum observed pre-truncation token length.
For `provenance_status="complete"`, resolved revisions and `built_at` are
required. For `legacy_adopted`, the document revision and build time are nullable;
the query revision is resolved and pinned at adoption time, but is explicitly
not presented as the unknown historical document revision.

- [ ] **Step 3: Implement crash-safe persistence**

Write JSON to a sibling `.tmp` file, `flush`/`os.fsync` it, then
`os.replace`. Flush the NumPy memmap before atomically advancing `next_row`.
Artifact paths are:

```text
<prefix>.lock
<prefix>.partial.npy
<prefix>.ids.partial.json
<prefix>.state.json
<prefix>.npy
<prefix>.ids.json
<prefix>.manifest.json
```

Hold an exclusive nonblocking `fcntl.flock` on `<prefix>.lock` for build,
legacy adoption, resume, and finalization. If another process owns it, fail
without writing. If a final vector/manifest already exists, validate and return
it when it matches; otherwise fail rather than replacing it implicitly.

Finalization is an explicit idempotent state machine. On completion:

1. flush/close the memmap;
2. validate shape, finite values, count, order, and norms for every batch, using
   `np.allclose(norms, 1.0, atol=1e-3, rtol=0)`;
3. atomically replace `.ids.partial.json` with `.ids.json`;
4. replace `.partial.npy` with `.npy`;
5. hash the final vector and ID files;
6. atomically write the manifest;
7. remove state;
8. fsync the containing directory after every rename/removal sequence.

While holding the lock, recovery derives its phase from both state and files and
accepts all valid intermediate combinations: both partial files; final IDs plus
partial vectors; partial IDs plus final vectors; both final files without a
manifest; and both final files with a valid manifest. Before continuing, it
validates any final file against the state/input/order and refuses a missing
counterpart, an unexpected final destination, or any hash mismatch. The
manifest is the sole publication marker: consumers ignore final ID/vector files
until it exists and validates. Resume completes the missing rename(s), writes or
validates the manifest, removes state, and fsyncs the directory. Never use
`os.replace` to overwrite an already-present final destination.

- [ ] **Step 4: Refactor `geo-embed` around registry keys**

```bash
uv run geo-embed build --variant medcpt_v1 --resume
uv run geo-embed build --variant qwen3_06b_1024_v1 --resume
uv run geo-embed adopt-legacy --variant bge_small_v15
```

`build` accepts `--input`, `--batch-size`, `--device`, and
`--requested-revision` but no arbitrary model, dimension, column, prompt, or
output prefix. Defaults come from the registry.

For a new build, resolve the requested floating revisions once and write those
SHAs into `BuildState` before loading a model. On `--resume`, read state first
and load its stored SHAs directly; do not resolve `main` again, so an upstream
repository update cannot strand a valid partial build. If the operator supplies
a requested revision on resume, require it to equal the state's original
requested revision before continuing.

`adopt-legacy` validates the existing `embeddings.npy`/`.ids.json`, computes
current hashes, and writes a manifest with
`provenance_status="legacy_adopted"`. Historical resolved revision and build time
are null because they cannot be reconstructed honestly. For the runtime query
revision, it records and verifies `LEGACY_BGE_QUERY_REVISION`—the exact SHA
frozen by Track 4—and never resolves floating `main`. Keep the historical
document revision null. Add a test proving adoption fails if that exact snapshot
is unavailable and that an upstream `main` change cannot alter the adopted
manifest.

- [ ] **Step 5: Exclude generated artifacts**

Ignore `*.lock`, `*.partial.npy`, `*.ids.partial.json`, `*.state.json`, the two
candidate matrices/ID files, and generated manifests under `data/processed`.
The reviewed manifests used in a real comparison are exported later from the
canonical ready database state into `eval/embedding_manifests/` and committed.

- [ ] **Step 6: Run and commit**

```bash
uv run pytest tests/test_embedding_artifacts.py tests/test_build_embeddings.py -v
git add .gitignore src/geo_index/embedding_artifacts.py src/geo_index/build_embeddings.py tests/test_embedding_artifacts.py tests/test_build_embeddings.py
git commit -m "feat: build resumable embedding artifacts"
```

### Task 3: Add typed columns, restartable loading, and per-model indexes

**Files:**
- Create: `src/geo_index/embedding_store.py`
- Create: `tests/test_embedding_store.py`
- Modify: `src/geo_index/pg_hybrid.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes `RETRIEVAL_PROFILE_V1` for HNSW build and readiness validation.
- Produces: `migrate(conn)`, `adopt_baseline(conn, manifest)`,
  `load_variant(conn, spec, manifest, batch_size)`,
  `build_index(conn, spec)`, `variant_status(conn, key)`, and
  `require_ready(conn, key) -> ReadyEmbeddingVariant`.
- Produces:
  `export_manifest(conn, key, destination) -> EmbeddingManifest`.
- `ReadyEmbeddingVariant` contains the registry spec, stored manifest, artifact
  ID, verified row coverage, and current valid index definition.
- Adds `geo-embedding-db = "geo_index.embedding_store:main"`.

- [ ] **Step 1: Write safe-DDL and state tests**

Fake-connection tests assert SQL identifiers are `psycopg.sql.Identifier`
objects derived after `get_variant`. Add an opt-in Postgres test that starts from
a `series(id, gse, embedding vector(384))` fixture and verifies:

```python
assert column_dimension(conn, "embedding") == 384
assert column_dimension(conn, "embedding_medcpt_768") == 768
assert column_dimension(conn, "embedding_qwen3_06b_1024") == 1024
```

Also prove migration is idempotent and never rewrites or renames `embedding`.

- [ ] **Step 2: Implement idempotent migration**

```sql
ALTER TABLE series
  ADD COLUMN IF NOT EXISTS embedding_medcpt_768 vector(768),
  ADD COLUMN IF NOT EXISTS embedding_qwen3_06b_1024 vector(1024);

CREATE TABLE IF NOT EXISTS embedding_variant_state (
    variant          TEXT PRIMARY KEY,
    artifact_id      TEXT NOT NULL,
    manifest         JSONB NOT NULL,
    expected_count   INTEGER NOT NULL CHECK (expected_count >= 0),
    next_offset      INTEGER NOT NULL DEFAULT 0 CHECK (next_offset >= 0),
    status           TEXT NOT NULL
                     CHECK (status IN ('loading', 'complete', 'indexed')),
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at     TIMESTAMPTZ,
    indexed_at       TIMESTAMPTZ
);
```

`artifact_id` is the manifest's `vectors_sha256`. Store the complete manifest as
JSONB for database/artifact comparison.

- [ ] **Step 3: Write restart and rollback tests**

Use a five-row Postgres fixture and batch size two. Prove:

- the first committed batch advances `next_offset` to two;
- a failure before commit leaves vectors/state unchanged;
- replaying a committed batch is idempotent;
- a changed artifact hash cannot resume the same column;
- a missing or duplicate GSE aborts before advancing state;
- a new load refuses a nonempty target column when no matching state row exists;
- incomplete variants cannot be indexed or returned by `require_ready`;
- baseline adoption streams every DB vector in artifact order and refuses even
  one mismatched value before marking `bge_small_v15` complete/indexed;
- two load/index/adopt/export processes for one variant cannot run concurrently;
- temporary ID tables are dropped after success and failure, including when the
  same pooled connection is reused;
- canonical manifest export refuses a stale/mismatched artifact ID and writes
  atomically only for a ready variant.

- [ ] **Step 4: Implement the loader**

At the start of every load/resume process, acquire a nonblocking session advisory
lock derived from the fixed registry key; hold it across every committed batch
until the complete operation finishes. Apply the same per-variant lock to
adopt, index, and export. A second process fails clearly instead of waiting or
racing. PostgreSQL session advisory locks persist across transactions until
release or session end
([official locking guide](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS)).
Validate artifact IDs are unique and create one session temporary
`embedding_load_ids(gse text primary key) ON COMMIT PRESERVE ROWS` containing
the complete artifact ID set. Reject missing database GSEs before writing any
vector. If no state row exists, require the target column to be empty; otherwise
refuse the load rather than mixing unknown prior vectors. In `finally`, drop
`embedding_load_ids` and release the advisory lock so connection reuse cannot
inherit temp rows or locks.

For each batch:

1. lock its `embedding_variant_state` row `FOR UPDATE`;
2. verify `artifact_id`, manifest, expected count, and offset;
3. create a temporary `(gse text primary key, embedding vector(N)) ON COMMIT
   DROP` staging table using the registry dimension;
4. `COPY` the aligned GSE/vector batch into it;
5. reject a stage count mismatch or a GSE absent from `series`;
6. update only the registry column for the staged GSEs;
7. advance `next_offset` and `updated_at` in the same transaction;
8. commit.

Use `psycopg.sql.Identifier` for the column and index. On final batch, join
`embedding_load_ids` back to `series` and verify exactly `expected_count`
artifact IDs have non-null target vectors; also verify no target vector exists
outside the artifact ID set. Then mark `complete`.
Use Psycopg's documented SQL-composition objects for every dynamic identifier
([Psycopg SQL API](https://www.psycopg.org/psycopg3/docs/api/sql.html)).

- [ ] **Step 5: Build HNSW only for complete variants**

Run index creation with autocommit because PostgreSQL does not allow
`CREATE INDEX CONCURRENTLY` inside a transaction block
([PostgreSQL `CREATE INDEX`](https://www.postgresql.org/docs/current/sql-createindex.html)):

```sql
CREATE INDEX CONCURRENTLY series_hnsw_medcpt_768
ON series USING hnsw (embedding_medcpt_768 vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX CONCURRENTLY series_hnsw_qwen3_06b_1024
ON series USING hnsw (embedding_qwen3_06b_1024 vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

Render the actual statement from the registry plus
`RETRIEVAL_PROFILE_V1.hnsw_m`/`hnsw_ef_construction` rather than duplicating
these literals. Refuse indexing before `complete`. If a same-name index is valid,
inspect catalog table/column/opclass and effective HNSW options before adopting
it as `indexed`. Treat absent `reloptions` as pgvector's documented defaults
`m=16` and `ef_construction=64`; do not compare raw `pg_get_indexdef` strings.
If it is invalid, drop it and retry. If its valid effective definition differs,
fail without dropping it. After creation, verify `pg_index.indisvalid` and the
same effective definition before marking `indexed`.

`adopt_baseline` first creates an ordinal/GSE temporary table and streams
`series.embedding` in artifact order, comparing every stored `float32` vector
to the adopted matrix with `np.allclose(db, artifact, atol=1e-6, rtol=0)`
before it binds that artifact hash. This covers the existing loader's
six-decimal serialization; tests accept only that rounding drift and reject a
larger mismatch. Row/count/dimension checks alone are insufficient. It then performs the same effective
catalog/definition validation for `series_hnsw`. `require_ready` rechecks
artifact-scoped coverage, `pg_index.indisvalid`, and the effective catalog
definition/options on every process startup rather than trusting stale state
alone.

pgvector recommends loading before HNSW creation
([index build guidance](https://github.com/pgvector/pgvector#hnsw)).

- [ ] **Step 6: Add operational commands**

```bash
uv run geo-embedding-db migrate
uv run geo-embedding-db adopt --variant bge_small_v15
uv run geo-embedding-db load --variant medcpt_v1 --batch-size 500
uv run geo-embedding-db index --variant medcpt_v1
uv run geo-embedding-db load --variant qwen3_06b_1024_v1 --batch-size 250
uv run geo-embedding-db index --variant qwen3_06b_1024_v1
uv run geo-embedding-db status
uv run geo-embedding-db export-manifest --variant medcpt_v1 \
  --output eval/embedding_manifests/medcpt_v1.json
```

`status` prints only variant, expected/non-null counts, state, index validity,
artifact hash prefix, and relation/index bytes. It never prints DSNs or vectors.
`export-manifest` calls `require_ready`, verifies the state-row `artifact_id`
equals `manifest.vectors_sha256`, and atomically writes canonical sorted JSON.
Evaluation and image builds consume only these DB-exported files; nobody copies
a working-directory manifest into `eval/embedding_manifests` by hand.

- [ ] **Step 7: Run and commit**

```bash
uv run pytest tests/test_retrieval_profile.py tests/test_embedding_store.py -v
GEO_TEST_PG=1 uv run pytest tests/test_embedding_store.py -m integration -v
git add pyproject.toml src/geo_index/embedding_store.py src/geo_index/pg_hybrid.py tests/test_embedding_store.py
git commit -m "feat: store alternate embedding columns"
```

### Task 4: Route internal retrieval through the registry

**Files:**
- Modify: `src/geo_index/pg_hybrid.py`
- Modify: `src/geo_index/web.py`
- Create: `tests/test_pg_hybrid_variants.py`
- Modify: `tests/test_pg_hybrid.py` after Track 2 creates it

**Interfaces:**
- Consumes Task 1's canonical `RETRIEVAL_PROFILE_V1`.
- Produces:

```python
def search_rows(
    conn,
    query: str,
    *,
    ready: ReadyEmbeddingVariant | None = None,
    query_encoder=None,
    qv: np.ndarray | None = None,
    topk: int = 15,
    deep: int | None = None,
    mode: str = "hybrid",
    k0: int | None = None,
    filters: SearchFilters = SearchFilters(),
) -> list[dict]:
    ...

def search_with_facets(
    conn,
    query: str,
    *,
    ready: ReadyEmbeddingVariant | None = None,
    query_encoder=None,
    qv: np.ndarray | None = None,
    filters: SearchFilters | None = None,
    topk: int = 15,
    deep: int | None = None,
    mode: str = "hybrid",
    k0: int | None = None,
    facet_pool: int | None = None,
) -> SearchResponse:
    ...
```

- Produces:
  `load_query_encoder_for_ready(ready: ReadyEmbeddingVariant) -> QueryEncoder`.
- Reuses `encode_one_query(encoder, text) -> np.ndarray` from Task 1.

- [ ] **Step 1: Write column-routing tests before SQL changes**

Use a recording cursor and a fixed `qv`. For every variant, assert its dense and
hybrid statement contains only its registered identifier. Assert BM25 contains
none of the three embedding columns. Also assert:

```python
with pytest.raises(ValueError, match="expected 768"):
    search_rows(
        conn,
        "query",
        mode="dense",
        ready=READY_MEDCPT,
        qv=np.zeros(384, dtype=np.float32),
    )
assert conn.cursor_calls == 0
```

Add nonfinite-vector, not-ready, filters-before-limit, and hybrid reuse cases.
Test unknown keys at the boundary that calls `require_ready`, before cursor/model
work. For query-scoped facets under MedCPT and Qwen, assert every nested
candidate retrieval uses only that selected column and never the baseline.
Add exact-score ties at the BM25 cutoff, exact-distance ties within a fixed ANN
candidate set, exact-RRF ties, and tied facet counts. Assert GSE/value ascending
at each applicable outer order. An opt-in Postgres `EXPLAIN` test must still
show the selected HNSW index; do not append GSE directly to the inner ANN order
and accidentally disable kNN planning.

- [ ] **Step 2: Replace global model/dimension assumptions**

Remove `EMBED_MODEL`, `DIM`, and the single `BGE_QUERY_INSTRUCTION` from
`pg_hybrid.py`. The CLI, service, web app, and evaluator call
`require_ready(conn, key)` exactly once per variant at startup and cache the
returned immutable `ReadyEmbeddingVariant`; BM25 resolves none. `search_rows`
and `search_with_facets` require that ready object for dense/hybrid and perform
only cheap mode/vector/dimension checks per call. They never run artifact-wide
coverage or catalog checks inside a timed/request retrieval.
When a query encoder is needed,
`load_query_encoder_for_ready` loads the manifest's exact resolved query-model
SHA—not the registry's floating `main`. For legacy BGE it uses the runtime query
SHA pinned by `adopt-legacy` and preserves the manifest's
`legacy_adopted` warning. Validate `qv.dtype` can be converted to `float32`,
shape equals `(spec.dimension,)`, and every value is finite.

Construct dynamic SQL from `ready.spec` with `psycopg.sql.SQL` and
`psycopg.sql.Identifier(ready.spec.column)`. Parameters remain `%(qv)s`,
`%(query)s`, and numeric bounds. Dense and hybrid order expressions must use the
same chosen identifier. Track 2's filter predicate stays inside dense and BM25
branches before each `LIMIT`. Dense/hybrid transactions set
`hnsw.ef_search=100` and `hnsw.iterative_scan=relaxed_order` for all variants.

Preserve pgvector's kNN plan with a two-stage dense branch. The inner query is a
`MATERIALIZED` ANN candidate CTE ordered **only** by the raw distance operator
and bounded by `deep`. The outer query re-sorts that fixed candidate set by
`distance + 0` then GSE ascending (the `+ 0` forces the strict sort on PostgreSQL
17+), and only then assigns dense ranks. This follows pgvector's relaxed-scan
re-sort pattern. BM25 orders score descending/GSE ascending before its cutoff;
fused RRF orders descending/GSE ascending; facet buckets order count
descending/value ascending. Never rely on database row order for ties.

The stable tie policy makes ordering deterministic **within the ANN candidate
set**; it does not turn approximate HNSW into an exact scan or guarantee
bit-for-bit membership at the inner `deep` boundary. The relevance run captures
and hashes one ranking per system. If exact boundary reproducibility becomes a
requirement, add a separately measured exact/strict retrieval profile rather
than pretending relaxed ANN provides it.

Thread the same `ready` object through `search_with_facets` into its primary
`search_rows` call. Bind it into Track 2's nested facet retriever with
`functools.partial(search_rows, ready=ready)` before passing it to
`facet_counts`; passing raw `search_rows` would lack the selected model state.

Use Task 1's frozen profile. `search_rows` and
`search_with_facets` resolve `deep=None`, `k0=None`, and `facet_pool=None` from
that object and take their HNSW session settings from it; `topk` remains a
request/run input. Tests assert the effective calls/SQL equal the profile and
reject an attempted mutation. Explicit evaluator/CLI overrides are recorded in
their run provenance; the MCP service supplies no overrides. Do not maintain a
second default copy in `pg_hybrid.py`, the web app, or the evaluator; embedding
Task 6 later removes the remote MCP track's temporary compatibility copy.

- [ ] **Step 3: Keep loading/index commands separated**

`pg_hybrid init` creates the baseline schema plus the two candidate columns for a
clean database. `pg_hybrid load` continues loading only the baseline and corpus.
Candidate loads/indexes delegate to `geo-embedding-db`; `pg_hybrid index` must
not drop candidate indexes.

Add `--embedding-variant` to the search CLI with choices from `VARIANTS`:

```bash
uv run python -m geo_index.pg_hybrid search \
  "CRISPR screen in T cells" \
  --mode hybrid \
  --embedding-variant medcpt_v1
```

For dense/hybrid, the CLI resolves `require_ready` once before loading the
encoder or issuing retrieval and passes that object through the entire command;
BM25 resolves no embedding state.

- [ ] **Step 4: Configure the local demo**

`web.py` reads `GEO_EMBEDDING_VARIANT` once at startup, validates it before
loading a model, calls `require_ready` once, retains the ready object, lazily
loads the manifest-pinned query encoder, and includes
`embedding_variant` in the JSON response. BM25 responses use null. Do not add a
browser query parameter or UI selector.

- [ ] **Step 5: Run and commit**

```bash
uv run pytest tests/test_retrieval_profile.py tests/test_pg_hybrid_variants.py tests/test_pg_hybrid.py -v
uv run pytest -v
git add src/geo_index/pg_hybrid.py src/geo_index/web.py tests/test_pg_hybrid_variants.py tests/test_pg_hybrid.py
git commit -m "feat: route search by embedding variant"
```

### Task 5: Extend Track 3 into a fair seven-system comparison

**Files:**
- Modify: `src/geo_index/retrieval_eval.py`
- Modify: `tests/test_retrieval_eval.py`
- Create: `tests/test_retrieval_eval_variants.py`
- Create: `eval/embedding_manifests/.gitkeep`
- Modify: `eval/README.md`

**Interfaces:**
- Consumes Track 3's `QueryCase`, judgments, pool format, metrics, and strict
  unjudged-result behavior.
- Produces: `SystemSpec` and
  `evaluation_systems(variant_keys) -> tuple[SystemSpec, ...]`.
- Produces:
  `validate_comparison_corpus(manifests) -> ComparisonCorpusIdentity`.
- Produces one report entry per system plus artifact/query provenance.

- [ ] **Step 1: Write orchestration tests**

```python
def test_system_matrix_runs_bm25_once_and_each_variant_twice(fake_runner):
    systems = evaluation_systems(
        ["bge_small_v15", "medcpt_v1", "qwen3_06b_1024_v1"]
    )
    assert [item.system_id for item in systems] == [
        "bm25",
        "bge_small_v15/dense",
        "bge_small_v15/hybrid",
        "medcpt_v1/dense",
        "medcpt_v1/hybrid",
        "qwen3_06b_1024_v1/dense",
        "qwen3_06b_1024_v1/hybrid",
    ]
```

Add fakes proving one query encoder loads per variant, one vector per
query/variant is reused across dense and hybrid, incomplete database variants
fail before retrieval, different variants never share report keys, and a newly
surfaced unjudged GSE makes the run exit nonzero.
Also assert `require_ready` runs exactly once per requested variant before any
query and that every dense/hybrid/facet call receives the corresponding cached
ready object.
Add parameterized preflight tests showing that a changed input SHA, ID SHA,
count, or document-template version in any one manifest aborts before BM25,
model loading, or database retrieval. ID SHA binds both membership and order.
All three exact manifests must pass and yield one recorded
`ComparisonCorpusIdentity`.

- [ ] **Step 2: Extend pooling**

`pool` accepts:

```bash
uv run geo-eval-retrieval pool \
  --queries eval/retrieval_queries.jsonl \
  --qrels eval/retrieval_qrels.jsonl \
  --variants bge_small_v15,medcpt_v1,qwen3_06b_1024_v1 \
  --depth 20 \
  --output eval/retrieval_pool.jsonl
```

Call BM25 once per query, then dense/hybrid for each variant. Deduplicate the
seven top-20 lists by `(query_id, gse)` and preserve existing judgments. Keep
retrieval system/rank hidden from the human review queue.

Before the first query, load the canonical DB-exported manifests and call
`validate_comparison_corpus`. Require identical `input.sha256`, `ids_sha256`,
`count`, and `input.document_template_version` across BGE, MedCPT, and Qwen.
The optional artifact-builder `--input` remains useful for fixtures or separate
experiments, but a nonmatching artifact is ineligible for this official
bake-off.

- [ ] **Step 3: Review newly surfaced candidates**

Run Track 3's `validate` command. A human assigns 0/1/2 grades using the existing
rubric. LLM/subagent draft labels are allowed only as suggestions; reviewed
qrels remain authoritative.

- [ ] **Step 4: Run the comparison**

```bash
uv run geo-eval-retrieval run \
  --queries eval/retrieval_queries.jsonl \
  --qrels eval/retrieval_qrels.jsonl \
  --variants bge_small_v15,medcpt_v1,qwen3_06b_1024_v1 \
  --topk 20 \
  --deep 200 \
  --k0 60 \
  --run-id embedding-bakeoff-v1 \
  --output eval/results/embedding-bakeoff-v1.json
```

The report includes for every system:

- manifest/vector/input/ID SHA-256 values;
- the single validated comparison-corpus identity shared by all variants;
- document/query model IDs and resolved revisions;
- database artifact ID and readiness state;
- Recall@20, NDCG@10, and MRR@20 overall/per slice;
- query-encoder and database latency distributions;
- single-worker idle RSS, warmed-encoder RSS, and peak RSS while encoding;
- build seconds, truncation rate, table bytes, and index bytes;
- query/qrels hashes and retrieval settings.

Before timed runs, set `hnsw.ef_search=100` and
`hnsw.iterative_scan=relaxed_order`, verify every index has effective
`m=16`/`ef_construction=64` (including absent catalog options that mean those
pgvector defaults), warm each encoder and index, and then interleave
systems/query order with `random.Random(20260710)`. Run five timed repetitions.
Report median and p95 separately for encoder load, query encoding, and
database-only retrieval; do not combine cold model load with per-query latency.
Memory and cold-load measurements run in a **fresh subprocess for each variant
and each of the five repetitions**. Each child records pre-load RSS, cold load
time, warmed RSS, per-query encoding times, and 50 ms peak-RSS samples for the
fixed query set, then exits before the next variant. This prevents a later
variant from inheriting earlier encoder memory and gives five independent load
observations. When CUDA is used, each child also resets and reports peak
allocated/reserved device memory; label unsupported device-memory metrics as
unavailable rather than estimating them.

The normal relevance/database run may keep one encoder and cached ready object
per variant, reuse one query vector across dense/hybrid, and interleave systems.
Exclude `require_ready` and encoder load from database-only timings. Use one
captured, tie-stable ranking per system for relevance metrics and record its
result hash; latency repetitions do not replace or average that quality ranking.

Before pooling or running, export every exact lightweight manifest through
`geo-embedding-db export-manifest` into
`eval/embedding_manifests/<variant>.json`. The evaluator compares each export's
artifact ID with `require_ready`, applies the same four-field corpus-identity
preflight, and refuses stale or incomparable files before retrieval. Do not
commit matrices or ID lists.

- [ ] **Step 5: Produce a decision table, not an automatic switch**

The report/build-log summary marks:

- aggregate and slice wins/losses against BGE;
- any slice regression greater than 0.05 NDCG@10;
- latency/memory/storage tradeoffs;
- queries needing more judgments;
- `promote`, `keep-baseline`, or `expand-eval` with written evidence.

The protected slices are exactly `conceptual`, `filtered`, and `exact`; report
all three even when a small slice makes uncertainty large. Before the final run,
record numeric one-worker warm/peak RSS and p95 latency limits for the intended
host in the run configuration. If the intended host or limits are not selected,
the result may recommend quality but cannot mark `promote`; use `expand-eval` or
`provisional-quality-winner`. No command changes the active environment variable
automatically.

- [ ] **Step 6: Run and commit**

```bash
uv run pytest tests/test_retrieval_eval.py tests/test_retrieval_eval_variants.py -v
git add src/geo_index/retrieval_eval.py tests/test_retrieval_eval.py tests/test_retrieval_eval_variants.py eval/README.md eval/embedding_manifests
git commit -m "feat: compare embedding retrieval variants"
```

Do not commit `eval/results` unless the repository's Track 3 policy is changed
explicitly; record the measured summary in [[42-Build-Log]].

### Task 6: Wire one active variant into the private MCP service

**Files:**
- Modify after Track 4 lands: `src/geo_index/mcp_settings.py`
- Modify after Track 4 lands: `src/geo_index/search_service.py`
- Modify after Track 4 lands: `src/geo_index/mcp_models.py`
- Modify after Track 4 lands: `src/geo_index/mcp_server.py`
- Modify: corresponding Track 4 tests
- Modify after Track 4 lands: `Dockerfile`
- Modify: `README.md`
- Modify: `wiki/42-Build-Log.md` after evaluation

**Interfaces:**
- `SearchService(embedding_variant: str = DEFAULT_VARIANT, ...)`.
- Output-only `embedding_variant: str | None` and `retrieval_version: str`.
- `SearchService.retrieval_version_for(mode: str) -> str`.
- No new MCP input.

- [ ] **Step 1: Write service/config tests**

Assert:

- missing `GEO_EMBEDDING_VARIANT` selects `bge_small_v15`;
- MedCPT/Qwen keys select the corresponding query encoder;
- an unknown key fails settings validation before service/model/database I/O;
- BM25 never loads an encoder and reports `embedding_variant=None` plus
  `retrieval_version="bm25-v2"` after the canonical profile integration;
- repeated dense/hybrid calls load the active encoder once;
- startup/readiness rejects an active candidate not marked `indexed`;
- MCP tool schemas contain no `embedding_variant` input;
- structured dense/hybrid output reports the configured key and manifest-derived
  retrieval version;
- a container built for each candidate reads the matching manifest-pinned query
  model from its image cache with Hugging Face networking disabled;
- missing, malformed, or runtime/DB-mismatched `/app/embedding-image.json` fails
  startup before any tool becomes ready.

- [ ] **Step 2: Replace Track 4's baseline compatibility constant**

`McpSettings.from_env` calls `get_variant` directly. During `open()`,
`SearchService` calls `require_ready` for the configured variant and retains the
validated database manifest. It keeps one cached `ReadyEmbeddingVariant` and
one lazy query encoder, then passes that same ready object through every
dense/hybrid or query-scoped-facet retrieval.

Remove Track 4's compatibility copy of `RETRIEVAL_PROFILE_V1` and import the
canonical object from `retrieval_profile.py`. Add a test that the MCP service,
core retrieval, and provenance fingerprint all use that same object.

Define a retrieval fingerprint as the first 12 hexadecimal characters of the
SHA-256 of canonical JSON containing exactly: variant key, dimension,
`ids_sha256`, `vectors_sha256`, and the query encoder's model, resolved revision,
max length, pooling method, and complete query policy, plus the canonical
`RETRIEVAL_PROFILE_V1` created in Task 1. That immutable profile contains the RRF and
distance algorithm versions, `deep`, `k0`, `facet_pool`, HNSW `m`,
`ef_construction`, `ef_search`, `iterative_scan`, and the result/facet tie
policies plus BM25/blank-facet version constants. This binds the stored document vectors, query semantics, and every
hidden result-changing retrieval setting while deliberately excluding
request-visible fields such as `limit` and non-semantic fields such as build
time and runtime package versions. A change to any profile setting creates a
new profile constant rather than mutating v1.
Then define:

```python
def retrieval_version_for(self, mode: str) -> str:
    if mode == "bm25":
        return RETRIEVAL_PROFILE_V1.bm25_version
    if mode not in {"dense", "hybrid"}:
        raise ValueError(f"unsupported retrieval mode: {mode}")
    return (
        f"{self.ready.spec.key}:"
        f"{retrieval_fingerprint(self.ready.manifest)}:"
        f"{mode}-v1"
    )
```

Test that changing the query revision, query policy, ID hash, vector hash, or
any retrieval-profile setting changes the retrieval version, while changing
only `built_at` or runtime metadata does not. Also assert service calls and SQL
session settings use the same profile object so the fingerprint cannot drift
from effective execution.

For BM25-only output, use the profile's `bm25-v2` and set the per-query
`embedding_variant` field to null. Track 4 remains `bm25-v1`; the v2 bump is
required because Task 4 changes tie behavior. Any later BM25 ranking/facet
semantic change must create another explicit version rather than silently
reusing v2.

After `require_ready`, read the immutable image marker (inject its path in unit
tests; production uses `/app/embedding-image.json`) and compare its variant,
query model, resolved query revision, vector artifact hash, and manifest hash to
the active database manifest, hashing the same canonical sorted JSON used by
`export-manifest`. Then call `verify_cached_query_assets(...)`,
which internally requires `local_files_only=True`, for the exact resolved SHA.
Any mismatch or missing
snapshot fails `open()` even if the first request would be BM25. Actual query
encoder construction may remain lazy, but it must also use local-only mode and
the already-verified SHA.

Update Track 4's injected `SearchWithFacets` protocol to replace the legacy
`model` keyword with `query_encoder` and add keyword-only `ready`.
`SearchService.open()` caches one `ReadyEmbeddingVariant`;
`search_datasets` passes it, and `facet_values` passes a
`functools.partial(search_rows, ready=ready)` to `facet_counts`. Add an MCP-service regression
test proving MedCPT/Qwen query-scoped facets never execute the baseline column.

- [ ] **Step 3: Keep the MCP schema stable**

`search_datasets` and `facet_values` return the new output provenance from the
service. Tool arguments remain exactly those in [[47-MCP-Server-Plan]]. The
client cannot override deployment choice.
For `facet_values` without a text query, return
`retrieval_version=RETRIEVAL_PROFILE_V1.blank_facet_version` (currently
`facet-all-matches-v1`) and `embedding_variant=None`
regardless of the requested mode; query-scoped facets use the normal
BM25/dense/hybrid provenance. Test both paths.

- [ ] **Step 4: Make the hosted image variant-aware**

Replace Track 4's fixed BGE prefetch with a build-time registry key. Copy only
the small committed embedding manifests, not vector matrices or corpus data:

```dockerfile
ARG GEO_EMBEDDING_VARIANT=bge_small_v15
ARG GEO_QUERY_REVISION
ARG GEO_EMBEDDING_ARTIFACT_ID
COPY eval/embedding_manifests ./eval/embedding_manifests
RUN /app/.venv/bin/python -m geo_index.embedding_encoders prefetch-query \
      --variant "$GEO_EMBEDDING_VARIANT" \
      --manifest "eval/embedding_manifests/$GEO_EMBEDDING_VARIANT.json" \
      --cache-dir "$HF_HOME" \
      --image-marker /app/embedding-image.json \
      --expected-query-revision "$GEO_QUERY_REVISION" \
      --expected-artifact-id "$GEO_EMBEDDING_ARTIFACT_ID" \
    && chmod -R a=rX "$HF_HOME"
LABEL org.geo-metadata-index.embedding-variant="$GEO_EMBEDDING_VARIANT" \
      org.geo-metadata-index.query-revision="$GEO_QUERY_REVISION" \
      org.geo-metadata-index.embedding-artifact="$GEO_EMBEDDING_ARTIFACT_ID"
ENV GEO_EMBEDDING_VARIANT=$GEO_EMBEDDING_VARIANT \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1
```

Supply the two exact build arguments from the canonical DB-exported manifest;
the CLI verifies them before writing the marker. Build separate immutable images
per evaluated variant; do not mount a mutable shared model cache in production.
OCI labels aid external inspection, while `/app/embedding-image.json` is the
runtime-verifiable source. At runtime the environment may repeat the same key
but cannot switch the image; startup fails when setting, marker, DB manifest, or
local snapshot disagree.

Do not reuse Track 4's argument-free build command after this replacement.
With `jq` available, build from the reviewed DB-exported manifest like this
(substitute the actual selected variant):

```bash
VARIANT=medcpt_v1
MANIFEST="eval/embedding_manifests/${VARIANT}.json"
QUERY_REVISION="$(jq -er '.query_encoder.resolved_revision | select(type == "string" and length == 40)' "$MANIFEST")"
ARTIFACT_ID="$(jq -er '.vectors_sha256 | select(type == "string" and length == 64)' "$MANIFEST")"

docker build \
  --build-arg GEO_EMBEDDING_VARIANT="$VARIANT" \
  --build-arg GEO_QUERY_REVISION="$QUERY_REVISION" \
  --build-arg GEO_EMBEDDING_ARTIFACT_ID="$ARTIFACT_ID" \
  -t "geo-mcp:${VARIANT}" .

docker image inspect "geo-mcp:${VARIANT}" \
  --format '{{json .Config.Labels}}'
```

The three required `jq -e`/length checks and the prefetch verifier must make an
empty, null, malformed, wrong-variant, or stale value fail the build. Inspect
the labels and `/app/embedding-image.json`, then run the offline-cache and MCP
smokes below against this exact tag.

- [ ] **Step 5: Run full verification**

```bash
uv run pytest tests/test_embedding_variants.py tests/test_embedding_encoders.py tests/test_embedding_artifacts.py tests/test_build_embeddings.py tests/test_embedding_store.py tests/test_retrieval_profile.py tests/test_pg_hybrid_variants.py tests/test_retrieval_eval.py tests/test_retrieval_eval_variants.py -v
uv run pytest tests/test_mcp_settings.py tests/test_search_service.py tests/test_mcp_server.py tests/test_mcp_http.py -v
uv run pytest -v
GEO_TEST_PG=1 uv run pytest -m integration -v
```

Expected: offline tests require no network/model/Postgres; opt-in tests prove all
three dimensions/indexes, variant routing, filtered retrieval, and MCP
provenance against the real database.

- [ ] **Step 6: Run the selected-variant smoke**

After the evaluation decision, set the winning key explicitly:

```bash
uv run python -m geo_index.pg_hybrid search \
  "single cell RNA in human PBMC" --mode hybrid \
  --embedding-variant medcpt_v1
```

Use the actual winner rather than copying `medcpt_v1` blindly. Repeat one BM25,
dense, hybrid, filtered, evaluation, and remote MCP smoke. Set
`GEO_EMBEDDING_VARIANT` separately for the MCP/container smoke; the
`pg_hybrid search` CLI uses its explicit flag. Record measured metrics and the
active manifest hash in [[42-Build-Log]].

Build that variant's container and repeat BM25 plus dense/hybrid MCP calls with
outbound Hugging Face access blocked. The non-root process must load the exact
manifest-pinned query encoder from `/opt/huggingface` without writing there.

- [ ] **Step 7: Commit integration and measured documentation**

```bash
git add Dockerfile src/geo_index/mcp_settings.py src/geo_index/search_service.py src/geo_index/mcp_models.py src/geo_index/mcp_server.py tests/test_mcp_settings.py tests/test_search_service.py tests/test_mcp_models.py tests/test_mcp_server.py tests/test_mcp_http.py README.md wiki/42-Build-Log.md
git commit -m "feat: configure active embedding variant"
```

## Definition of done

- The baseline `embedding` column and index are unchanged.
- Both candidate columns have exact typed dimensions and independent valid HNSW
  indexes.
- Registry keys are the only route to models, artifacts, columns, indexes, and
  prompts.
- Candidate artifacts are resumable, hash-validated, finite, normalized,
  aligned, and provenance-complete.
- Database loading resumes by committed batch and refuses a changed artifact.
- Incomplete variants cannot be indexed, searched, evaluated, or selected for
  MCP.
- Dense/hybrid SQL uses the selected registered column; BM25 uses none.
- The seven systems use the same query/qrels corpus and all top-20 results are
  judged before scoring.
- Reports separate model quality, truncation, latency, build, and storage
  evidence.
- MCP has one configured active variant, reports it, and exposes no selector.
- The model decision and evidence are recorded without training or automatic
  promotion.

## Explicitly deferred

- More than the three registered variants.
- Qwen MRL/truncated-dimensional columns unless full 1,024-dimensional Qwen wins
  quality but misses an operational budget.
- Half-precision, binary quantization, or subvector indexes.
- A generic multi-model child table.
- Per-field/multi-vector retrieval.
- Learned routing, regression, reranking, or automatic promotion.
- Dropping candidate columns; decide cleanup after the prototype evidence is no
  longer needed.

## Sources

- BGE small v1.5 model card — https://huggingface.co/BAAI/bge-small-en-v1.5
- MedCPT Article Encoder — https://huggingface.co/ncbi/MedCPT-Article-Encoder
- MedCPT Query Encoder — https://huggingface.co/ncbi/MedCPT-Query-Encoder
- MedCPT paper — https://academic.oup.com/bioinformatics/article/39/11/btad651/7335842
- Qwen3-Embedding-0.6B model card and requirements — https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
- pgvector vector storage and HNSW guidance — https://github.com/pgvector/pgvector
- pgvector mixed-dimension indexing — https://github.com/pgvector/pgvector#can-i-store-vectors-with-different-dimensions-in-the-same-column
- pgvector HNSW guidance — https://github.com/pgvector/pgvector#hnsw
- pgvector vector storage formula — https://github.com/pgvector/pgvector#vector-type
- PostgreSQL concurrent index behavior — https://www.postgresql.org/docs/current/sql-createindex.html
- PostgreSQL advisory-lock semantics — https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS
- Psycopg safe SQL composition — https://www.psycopg.org/psycopg3/docs/api/sql.html
- Hugging Face Hub revisions/cache/`local_files_only` — https://huggingface.co/docs/huggingface_hub/en/package_reference/file_download
