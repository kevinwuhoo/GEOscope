---
title: Embedding Bake-off Runbook
tags: [embeddings, gemini, medcpt, qwen, bge, elasticsearch, evaluation, plan, v1]
status: approved-plan
created: 2026-07-10
updated: 2026-07-10
---

# 52 · Embedding Bake-off Runbook

← [[Home]] · replaces the active execution plan in
[[49-Alternate-Embedding-Bakeoff-Implementation-Plan]] · uses
[[46-Retrieval-Evaluation-Plan]] and [[51-Search-Database-Bakeoff-and-Elasticsearch-Plan]]

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` or
> `superpowers:executing-plans` to implement this runbook task-by-task. Do not
> start paid embedding jobs until the provider-neutral artifact tests pass and a
> dry-run cost estimate has been recorded.

## Decision and current state

Add **Google `gemini-embedding-2` at its full 3,072 dimensions** to the bakeoff.
Compare it fairly with BM25 and the existing/planned local pipelines:

- BGE small v1.5, 384 dimensions;
- MedCPT article/query encoders, 768 dimensions;
- Qwen3 Embedding 0.6B, 1,024 dimensions;
- Gemini Embedding 2, 3,072 dimensions.

Do not reduce dimensions merely to make storage smaller. Relevance is the
selection target; latency, storage, and cost are reported constraints, not
proxies for quality. Do not train a regression model, router, learned fusion, or
reranker for this prototype.

**No new embedding run has happened yet.** The BGE matrix already exists. The
old `embeddings_pubmedbert.npy` is not accepted as MedCPT because it lacks the
required model and input provenance. MedCPT, Qwen, and Gemini still need to be
generated.

The `codex/embedding-bakeoff-first-draft` branch contains a useful first draft of
the registry, artifact integrity, and evaluation seam. It is PostgreSQL-specific
and should not be merged as-is. Port the provider-neutral pieces and replace its
database-column loader with the Elasticsearch build described here.

## Why Gemini is the hosted candidate

The hosted-model research considered OpenAI `text-embedding-3-large`, Gemini
Embedding 2, Voyage Context 4, and Cohere Embed 4.

| Model | Full dimension | Why it remains interesting | Bakeoff disposition |
|---|---:|---|---|
| **Gemini Embedding 2** | **3,072** | New general model with reported scientific-domain strength, asymmetric retrieval formatting, low batch price | **Run now** |
| OpenAI `text-embedding-3-large` | 3,072 | Mature API, strong general benchmark, simple batch option | Documented runner-up; add only if Gemini is inconclusive |
| Voyage Context 4 | up to 2,048 | Vendor reports strong medical/domain retrieval and context-aware document embeddings | Add later if biomedical slice remains weak |
| Cohere Embed 4 | configurable | Long-context and healthcare-oriented positioning | Defer; less compelling first paid comparison |

Google reports Gemini Embedding 2 as generally available with an 8,192-token
input and 128–3,072 output dimensions. Its research paper reports strong
multilingual and zero-shot scientific-domain results. An independent Agentset
comparison also ranked it first across a small multi-domain benchmark and found
its strongest slice on SciFact, but that evaluation used judge-based labels and
the leading models were close. These are reasons to test Gemini, not evidence
that it will win on GEO.

## Cost expectation

The frozen corpus contains 222,961 documents, approximately 569 million
characters and 79.7 million whitespace-delimited words. A conservative planning
range is roughly 105–150 million input tokens. At the documented Gemini batch
rate of $0.10 per million tokens, the corpus job should be about **$10–15**, plus
small query/evaluation usage. Record the API's returned usage; do not treat this
estimate as an invoice.

Standard synchronous pricing is higher, so use the batch API for document
embeddings and synchronous calls for the small fixed query set. Paid-tier Gemini
API terms state that prompts and responses are not used to improve Google
products. Confirm the project/account terms again immediately before submission.

## Fixed experimental question

Which complete retrieval pipeline gives the best GEO series relevance while
preserving exact/filter use cases and remaining practical to operate?

The official comparison has **nine systems**:

1. BM25 once, independent of embedding model;
2. BGE dense;
3. BGE hybrid;
4. MedCPT dense;
5. MedCPT hybrid;
6. Qwen dense;
7. Qwen hybrid;
8. Gemini dense;
9. Gemini hybrid.

Every dense/hybrid system uses the same corpus rows, document composition,
filters, candidate depths, RRF profile, and final tie policy. The query and
document formatting may differ only where the model's published retrieval
contract requires it.

## Frozen corpus and document contract

Before generating another vector, freeze and record:

- source JSONL SHA-256;
- ordered-GSE SHA-256 and row count (`222,961` for the current snapshot);
- document-template version;
- normalization/enrichment version;
- exact included fields and separators;
- empty/missing-field behavior;
- maximum input/truncation behavior for each model.

Use one GSE-level narrative document per row. Do not inject new normalized labels
into only some model inputs. If normalized-label injection is worth testing, run
it later as a separate document-composition ablation after selecting the model.

There are 129 current documents longer than 32,768 characters. They may exceed
an 8,192-token model limit. Record returned token/truncation statistics, keep a
deterministic truncation policy, and report affected GSEs. Do not silently let
different SDK defaults choose different content.

## Model registry

Use one fixed code registry; callers select a safe key, never a model ID, vector
field, prompt, or artifact path. Initial entries:

| Key | Document encoder | Query encoder/policy | Dim | Elastic field |
|---|---|---|---:|---|
| `bge_small_v15` | pinned BGE small v1.5 | published retrieval instruction on query only | 384 | `embedding_bge_384` |
| `medcpt_v1` | pinned MedCPT Article Encoder | pinned MedCPT Query Encoder | 768 | `embedding_medcpt_768` |
| `qwen3_06b_1024_v1` | pinned Qwen3 Embedding 0.6B, no document prompt | published query prompt | 1,024 | `embedding_qwen3_06b_1024` |
| `gemini_embedding_2_3072_v1` | `gemini-embedding-2`, explicit document wrapper | same model, explicit query wrapper | 3,072 | `embedding_gemini_3072` |

For Gemini text retrieval, freeze these wrapper templates from the provider's
recommended asymmetric format:

```text
document: title: {title} | text: {content}
query:    task: search result | query: {content}
```

Record model ID, API version, build timestamp, wrapper version, output dimension,
input hashes, and returned token/truncation usage. A stable hosted model ID is not
enough provenance by itself.

## Artifact and manifest contract

Each model build produces:

```text
data/embeddings/<variant>/
  vectors.f32.npy
  ordered_gse.txt
  manifest.json
  build_state.json       # only while incomplete
  failures.jsonl         # only if any rows need retry
```

The finalized manifest includes:

- variant key, model/provider ID, resolved local revision where applicable;
- SDK/API version and embedding wrapper/prompt version;
- dimension, dtype, normalization policy, pooling, token limits;
- corpus SHA, ordered-GSE SHA, document-template version, row count;
- vector file SHA and nonfinite-vector count;
- batch/request identifiers and returned usage for hosted builds;
- started/completed timestamps and build code revision.

Finalize atomically only after every row is present, ordered, finite, and the
correct dimension. The loader accepts finalized manifests only. Never infer
readiness from a file merely existing.

## Build sequence

### Stage 0 — Port provider-neutral infrastructure

- [ ] Port/adapt the registry and artifact-integrity concepts from
  `codex/embedding-bakeoff-first-draft` into:
  `src/geo_index/embedding_registry.py`,
  `src/geo_index/embedding_artifacts.py`, and
  `src/geo_index/build_embeddings.py`.
- [ ] Separate local and hosted adapters in
  `src/geo_index/embedding_local.py` and
  `src/geo_index/embedding_gemini.py`.
- [ ] Make registry import lightweight: no PyTorch load, network call, or SDK
  client creation during import.
- [ ] Test hash identity, atomic finalization, resume, wrong dimensions,
  nonfinite values, provider failures, and retry ordering with fakes.
- [ ] Dry-run all commands without a Gemini API key and verify that no paid call
  can occur accidentally.

### Stage 1 — Adopt the existing BGE baseline

- [ ] Verify the existing matrix shape (`222,961 × 384`), dtype, finite values,
  ordered GSE alignment, and exact document composition.
- [ ] Create an honest adoption manifest. If the original model revision or
  input provenance cannot be proven, mark it unknown rather than inventing it.
- [ ] If ordered alignment cannot be proven, rebuild BGE instead of adopting it.

### Stage 2 — Generate MedCPT

- [ ] Pin both article and query encoder revisions.
- [ ] Encode title/body pairs with the published pooling/token policy.
- [ ] Persist a resumable 768-dimensional float32 matrix and final manifest.
- [ ] Smoke-test query/document compatibility before the full run.
- [ ] Do not relabel the old PubMedBERT matrix as MedCPT.

### Stage 3 — Generate Qwen

- [ ] Pin Qwen3 Embedding 0.6B to a resolved commit.
- [ ] Use the full 1,024-dimensional output; no document prompt and the frozen
  published query prompt.
- [ ] Persist a resumable float32 matrix and final manifest.
- [ ] Record actual max sequence length, truncation, runtime, and peak memory.

### Stage 4 — Submit and assemble Gemini batch embeddings

- [ ] Require `GEMINI_API_KEY`, an explicit paid-run flag, expected input SHA,
  expected row count, output directory, and a printed token/cost estimate.
- [ ] Build deterministic JSONL batch requests containing stable row/GSE IDs,
  the document wrapper, model ID, and `output_dimensionality=3072`.
- [ ] Split files/jobs only at deterministic row boundaries and write a submission
  manifest before upload.
- [ ] Submit through the current official Google GenAI SDK/batch API; record every
  provider job and file identifier.
- [ ] Poll/resume without resubmitting successful jobs. Retain provider errors by
  row ID and retry only failed/missing rows.
- [ ] Download results, validate response-to-row identity, assemble in frozen GSE
  order, normalize only if the chosen cosine pipeline requires it, and finalize
  the manifest.
- [ ] Verify shape (`222,961 × 3,072`), finite values, complete coverage, hashes,
  actual token usage, and actual charge estimate.

### Stage 5 — Build the versioned Elasticsearch index

- [ ] Load all four ready vectors with the normalized documents into one new
  versioned index using [[51-Search-Database-Bakeoff-and-Elasticsearch-Plan]].
- [ ] Use explicit quality-first vector mappings; for Gemini start with
  `int8_hnsw` and retain Elasticsearch's original float vectors for rescoring.
- [ ] Reject the entire index build if any active manifest fails alignment or
  coverage checks.
- [ ] Validate vector-field coverage and exact query embeddings for each variant
  before the alias can move.

### Stage 6 — Freeze ANN and fusion profiles

- [ ] For each vector field, compare ANN results with exact cosine
  `script_score` on a representative query/filter sample.
- [ ] Sweep a small predeclared grid of `num_candidates` and rescore oversampling;
  select the smallest profile meeting the recorded recall target.
- [ ] Measure p50/p95 latency and candidate count. Do not assume one profile fits
  all vector dimensions.
- [ ] Freeze RRF rank window, candidate depths, and final GSE tie-break before
  pooling official judgments.

### Stage 7 — Pool, review, and score

- [ ] Start from the fixed 16-query set in [[46-Retrieval-Evaluation-Plan]] and
  preserve protected `conceptual`, `filtered`, and `exact` slices.
- [ ] Pool the nine systems deeply enough that every newly surfaced Gemini,
  MedCPT, or Qwen candidate can be judged.
- [ ] Blind the reviewer to system/model identity and randomize pooled items.
- [ ] Never score unjudged new candidates as irrelevant; re-pool after adding a
  system or changing a retrieval profile.
- [ ] Report NDCG@10, Recall@20, and MRR@20 overall and by protected slice.
- [ ] Also report ANN recall, p50/p95 query latency, query-embedding latency,
  index/storage size, build time, truncation rate, and document/query cost.
- [ ] Save qrels, run files, profile configuration, and a machine-readable report.

## Promotion rule

Do not automatically promote the largest, newest, or highest-benchmark model.
Promote only after reviewed GEO qrels show:

- aggregate NDCG@10 improves over BGE;
- at least one conceptual slice improves;
- no protected `conceptual`, `filtered`, or `exact` slice loses more than 0.05
  absolute NDCG@10;
- exact/filter behavior is unchanged;
- operational latency, availability, storage, and cost are acceptable for the
  invite-only prototype.

If two models are effectively tied, prefer the simpler/cheaper operating
pipeline. Record the decision and uncertainty; do not train a selector to avoid
making it.

## Hosted-query deployment implications

Choosing Gemini means every live dense/hybrid user query needs a compatible
Gemini query embedding. The remote service therefore gains:

- `GEMINI_API_KEY` or equivalent managed credential;
- network egress and provider availability dependency;
- a bounded embedding timeout and clear failure behavior;
- deployment readiness tied to the exact Gemini manifest/wrapper;
- tiny but nonzero per-query cost.

BM25 remains available if the hosted query encoder is unavailable only if the
product explicitly chooses that degradation policy and reports it in retrieval
provenance. Never silently label a BM25-only response as hybrid.

## Acceptance criteria

- Four finalized manifests share identical corpus, ordered-GSE, count, and
  document-template identities.
- BGE adoption is evidence-based or it is rebuilt.
- MedCPT, Qwen, and Gemini matrices have complete coverage and correct dimensions.
- Gemini's actual usage, truncation, batch IDs, and estimated charge are recorded.
- The versioned Elasticsearch index contains all four searchable vector fields.
- ANN profiles are justified by exact-recall measurements.
- The nine-system pool has reviewed qrels for newly surfaced documents.
- The report includes relevance by slice plus latency, storage, truncation, and
  cost; no model is promoted automatically.
- The chosen active pipeline is recorded in the alias/index build and MCP
  retrieval provenance.

## Primary references

- [Gemini embeddings guide](https://ai.google.dev/gemini-api/docs/embeddings)
- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini Embedding 2 model details](https://ai.google.dev/gemini-api/docs/models/gemini-embedding-2)
- [Gemini Embedding 2 research paper](https://arxiv.org/abs/2605.27295)
- [Agentset embedding benchmark](https://agentset.ai/blog/gemini-2-embedding)
- [OpenAI embeddings guide](https://platform.openai.com/docs/guides/embeddings)
- [OpenAI Batch API](https://platform.openai.com/docs/guides/batch)
- [Voyage Context 4 announcement](https://blog.voyageai.com/2026/01/15/voyage-context-4/)
- [Cohere Embed 4 documentation](https://docs.cohere.com/docs/cohere-embed)
- [BGE small v1.5 model card](https://huggingface.co/BAAI/bge-small-en-v1.5)
- [MedCPT article encoder](https://huggingface.co/ncbi/MedCPT-Article-Encoder)
- [MedCPT query encoder](https://huggingface.co/ncbi/MedCPT-Query-Encoder)
- [Qwen3 Embedding 0.6B model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
