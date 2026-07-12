# Elasticsearch Full-Featured Live Comparison Design

**Date:** 2026-07-11
**Status:** Approved for implementation planning

## Purpose

Add a quick, repeatable live test that demonstrates the complete Elasticsearch
search experience against the loaded `geo-series` corpus. The primary path must
combine BM25 and one real query embedding through Elasticsearch-native reciprocal
rank fusion (RRF), apply normalized filters, compute disjunctive facets, preserve
stable GSE ordering, and report provenance in the same response.

Run that path with each loaded embedding family—BGE, MedCPT, and Qwen—over an
identical set of researcher-style dataset searches. Write the results as stable,
versioned Markdown tables so reviewers can understand ranking changes in a Git
diff.

This is a qualitative live smoke test. It does not select a winning model,
replace the judged retrieval evaluation, or expose a model selector in the
public search interface.

## Scope

### Included

- Real query encoders for:
  - `bge_small_v15` / 384 dimensions
  - `medcpt_v1` / 768 dimensions
  - `qwen3_06b_1024_v1` / 1,024 dimensions
- One internal comparison command that runs against `ELASTICSEARCH_URL` and
  environment-provided credentials.
- Elasticsearch health, mapping, document-count, and vector-coverage preflights.
- Exact GSE lookup and blank-query/filter/facet preflights.
- Full hybrid searches using native RRF, BM25, dense kNN, filters, disjunctive
  facets, stable ordering, and provenance together.
- Standalone BM25 and dense searches as diagnostic explanations of the hybrid
  ranking.
- A fixed, versioned query fixture representing plausible PhD researcher dataset
  searches.
- Deterministic Markdown output with side-by-side model comparisons and
  overlap-at-five summaries.
- Fake-client/unit tests for formatting, orchestration, validation, and failure
  behavior, plus opt-in live execution against the local container.

### Excluded

- Relevance judgments, qrels, NDCG, MRR, recall, or a best-model declaration.
- Changes to Elasticsearch mappings, loading, or the indexed documents.
- Vector generation for corpus documents.
- A public model selector or per-request model switching in the application.
- PostgreSQL comparison, Elastic Cloud provisioning, snapshots, aliases, or
  managed deployment work.
- Performance benchmarking. Runtime timings may be printed to the console for
  operator feedback but must not appear in the committed report because they
  create noisy diffs.

## Architecture

The test has four small units:

1. A versioned JSONL query fixture defines query text, intent, and normalized
   filters.
2. A query-embedding adapter creates one encoder per registry model and validates
   that every emitted vector is one-dimensional, finite, normalized, and the
   registered size.
3. A comparison runner calls `ElasticsearchSearchService` with each encoder and
   collects exact, BM25, dense, hybrid, filter, facet, ordering, and provenance
   evidence.
4. A deterministic Markdown renderer writes one reviewable report atomically.

The comparison runner is internal evaluation infrastructure. Production still
constructs one `ElasticsearchSearchService` with the deployment-selected
`ELASTICSEARCH_ACTIVE_MODEL`; the runner constructs three services sequentially
only to compare fixed registry entries.

## Query Embedding Adapters

The adapters reuse the fixed registry as the source of model IDs, query formats,
dimensions, normalization, maximum lengths, and pooling behavior.

### BGE

- Model: `BAAI/bge-small-en-v1.5`
- Format the input with the registry query template.
- Encode with `sentence-transformers`.
- L2-normalize the result.
- Require shape `(384,)` and finite values.

### MedCPT

- Model: `ncbi/MedCPT-Query-Encoder`
- Tokenize the unmodified registry-formatted query.
- Use the query encoder's CLS representation.
- L2-normalize the result.
- Require shape `(768,)` and finite values.

### Qwen

- Model: `Qwen/Qwen3-Embedding-0.6B`
- Format the input with the registry retrieval instruction.
- Load through `sentence-transformers` with the model's required remote-code
  setting and left-padding behavior.
- L2-normalize the result.
- Require shape `(1024,)` and finite values.

Load one model at a time, reuse it for every query, then release it before loading
the next model. This bounds local memory and makes the command usable on a laptop.
Resolve and record each query-model revision in the report. A model download is
allowed for this explicit live-test command, but never from the loader or search
service itself.

## Researcher Query Set

The versioned fixture contains these seven cases:

| Query ID | Researcher query | Filters | Intent |
|---|---|---|---|
| `control_childhood_malaria` | whole blood transcriptomics of children with severe malaria | none | Traceable control related to the known `GSE1124` record. |
| `human_tumor_exhausted_t_cells` | single-cell RNA sequencing of exhausted CD8 T cells in human solid tumors | `organism_ids=[NCBITaxon:9606]`, `assay_labels=[scRNA-seq]` | Find human tumor immune-state scRNA-seq datasets. |
| `mouse_brain_spatial_injury` | spatial transcriptomics of mouse hippocampus after traumatic brain injury | `organism_ids=[NCBITaxon:10090]` | Find mouse spatial-expression studies involving brain injury. |
| `crispr_interferon_t_cells` | CRISPR knockout screen for regulators of interferon response in T cells | none | Find genetic perturbation screens despite terminology variation. |
| `rare_disease_fibroblasts` | fibroblast transcriptomes from patients with rare inherited connective tissue disorders | none | Find patient-derived rare-disease fibroblast expression datasets. |
| `ribosome_er_stress` | ribosome profiling during endoplasmic reticulum stress | none | Find Ribo-seq or ribosome-footprinting stress experiments. |
| `airway_viral_infection` | airway epithelial response to respiratory viral infection | none | Find airway infection-response datasets across virus names. |

Queries intentionally mix modality, tissue, disease, perturbation, organism, and
cross-vocabulary concepts. Filters use only the normalized public filter
contract.

## Live-Test Flow

### 1. Infrastructure preflight

Before loading query models, the command must verify:

- the container endpoint is reachable through `ELASTICSEARCH_URL`;
- the server reports Elasticsearch `9.4.2`;
- `geo-series` exists and uses mapping revision `geo-series-v1`;
- the cluster is not red;
- document count is nonzero;
- BGE, MedCPT, and Qwen vector coverage each equals the document count;
- mapped vector dimensions are 384, 768, and 1,024 respectively.

Gemini is not part of this comparison because the current corpus has no Gemini
artifact. Its mapped field and zero coverage are recorded as context, not treated
as a failure.

### 2. Contract preflight

Exercise these backend-neutral behaviors once:

- exact lookup for lowercase `gse1124` returns canonical `GSE1124`;
- a blank BM25 query with human and expression-array filters returns only matching
  documents;
- OR-within a facet and AND-across facets are satisfied by every returned hit;
- equal-score blank-query hits have ascending GSE secondary order;
- blank-query facets report `all_matches` scope;
- the organism facet omits its own human filter and still shows at least one
  alternative organism;
- the assay facet omits its own filter while retaining the other active filters.

### 3. Full-featured hybrid run

For each model and query, construct a service using that model's fixed vector
field and query encoder, then issue one `mode="hybrid"` search with:

- `topk=5`
- `deep=100`
- `num_candidates=500`
- `k0=60`
- `facet_pool=100`
- `bucket_limit=10`
- the fixture's normalized filters

The existing service sends a native RRF retriever containing a standard BM25
retriever and a dense kNN retriever. The normalized filter is applied at the RRF
level, so both retrieval branches see the same constraints. The same service
response also carries bounded, query-scoped disjunctive facets and provenance.

For every hybrid response, require:

- five results for the current corpus and fixed query fixture;
- every result satisfies all active filters;
- facets have `candidate_pool` scope;
- every facet candidate count is positive and no greater than `facet_pool`;
- hits are score descending with GSE ascending for exact score ties;
- provenance reports backend `elasticsearch`, mapping `geo-series-v1`, the current
  model key, its registered vector field and dimensions, and mode `hybrid`.

### 4. Diagnostic component runs

For the same query and filters:

- run BM25 once because it is model-independent;
- run dense retrieval once per model;
- keep the same `topk`, `deep`, `num_candidates`, facet bounds, and filters.

These results explain which lexical and semantic candidates contributed to the
hybrid rankings. They are not the primary acceptance path and cannot substitute
for a passing hybrid run.

## Markdown Report

Write `eval/elasticsearch-live-comparison.md` in deterministic query-fixture and
model-registry order. Do not include timestamps, wall-clock durations, absolute
paths, credentials, random identifiers, or machine-specific device details.

The report contains:

1. **Run provenance** — Git commit, index, mapping revision, Elasticsearch version,
   document count, retrieval parameters, and query-fixture digest.
2. **Model readiness** — model key, query model and resolved revision, vector field,
   dimensions, normalization, and corpus coverage.
3. **Feature proof matrix** — exact lookup, health, mapping, coverage, full hybrid,
   filters, facets, own-filter omission, stable ordering, and provenance, each as
   `PASS` or `FAIL` with a concise deterministic note.
4. **Per-query hybrid tables** — rank plus side-by-side BGE, MedCPT, and Qwen cells
   formatted as `GSE — title`.
5. **Per-query diagnostic tables** — one BM25 column and side-by-side dense columns.
6. **Facet evidence** — filters, facet scope, candidate count, and top three buckets
   for every model's hybrid response.
7. **Overlap@5** — pairwise BGE/MedCPT, BGE/Qwen, and MedCPT/Qwen intersection count
   for dense and hybrid results. This describes disagreement without claiming
   relevance quality.

Escape Markdown metacharacters and collapse whitespace in titles. Preserve full
GSE accessions. Cap displayed titles at a fixed length so tables remain readable
and diffs remain stable.

Write to a temporary sibling and replace the destination atomically only after
all preflights and searches pass. On failure, exit nonzero, print the failing
feature/model/query to stderr, and leave the previous successful report unchanged.

## Command Interface

Register one internal command:

```bash
GEO_TEST_ELASTIC=1 uv run geo-elasticsearch-compare \
  --queries eval/elasticsearch_live_queries.jsonl \
  --topk 5 \
  --output eval/elasticsearch-live-comparison.md
```

The command requires `GEO_TEST_ELASTIC=1` to prevent accidental model downloads
or live requests during ordinary tests. Connection settings come exclusively
from `ELASTICSEARCH_URL` and the existing credential environment variables.

The model set is fixed internally to the three registry entries. There is no
`--model` flag because this command's contract is to compare all loaded models,
and production must continue to expose only one deployment-selected model.

## Testing Strategy

### Unit and fake-client tests

- Query templates are applied exactly once.
- Each adapter returns a finite `float32` vector with its registered dimension and
  L2 norm.
- Wrong dimensions and nonfinite values fail before Elasticsearch receives a
  request.
- Query fixtures reject unknown filters, blank fields, duplicate IDs, and unstable
  ordering.
- The runner executes BM25 once and dense plus full hybrid once per model/query.
- Full hybrid calls preserve filters and the fixed retrieval parameters.
- Feature validation rejects wrong provenance, filter leaks, facet scope errors,
  excessive candidate counts, missing hits, and unstable ties.
- Markdown rendering is deterministic and correctly escapes titles.
- Overlap@5 uses GSE sets and returns integer intersections from zero through five.
- Atomic output preserves the previous report when any model/query fails.
- Credentials and absolute paths never appear in rendered output or error text.

### Opt-in live test

The live test runs the command against the already-loaded container without
resetting or mutating `geo-series`. It verifies a successful exit, parses the
generated Markdown, checks that every fixed query and model appears, and confirms
the final document count remains unchanged.

The existing reset-based `tests/test_elasticsearch_live.py` remains separate and
must not run against the real corpus after ingestion.

## Acceptance Criteria

- One command runs all seven queries with BGE, MedCPT, and Qwen.
- Every model generates real query vectors with the fixed query format and correct
  dimensions.
- The primary result for every model/query is a native-RRF response combining BM25
  and dense kNN while applying filters and returning facets and provenance.
- Exact lookup, blank-query facets, disjunctive own-filter omission, OR/AND filter
  semantics, bounded query facets, and stable GSE ordering all pass live.
- Standalone BM25 and dense diagnostic rankings are recorded beside hybrid
  rankings.
- The deterministic Markdown report makes all three models and their differences
  reviewable in Git.
- The live command does not reset the index, mutate corpus artifacts, or change the
  production active-model contract.
- Ordinary offline tests do not load models or contact Elasticsearch.
