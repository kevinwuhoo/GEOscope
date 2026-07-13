# Hackathon README Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the developer-oriented root README with a concise, evidence-backed hackathon showcase for GEOscope.

**Architecture:** The root README becomes a judge-facing narrative whose quantitative claims are backed by the existing wiki and local run artifacts. It links to the live demo and deeper documentation while leaving developer setup for a later `DEVELOPMENT.md`.

**Tech Stack:** Markdown, repository wiki documentation, local JSON run reports, deployed GEOscope health/readiness endpoints

## Global Constraints

- Modify only `README.md`; preserve every unrelated worktree change.
- Lead with the verified live demo at `https://geoscope.kevinformatics.com`.
- Search behavior must be described as shared Elasticsearch/MCP-layer behavior used by every consumer, not a website-only implementation.
- Do not include dependency installation or service setup. Preserve only the
  compact canonical command handoff required by the repository's primary-path
  documentation contract.
- Do not include the retrospective about softening the original single-cell keyword thesis.
- Describe structured extraction as an experiment that was not loaded into the production index.
- Report the structured-extraction experiment total as $121.61: $47.52 for
  the OpenAI pilot and $74.10 for the 10,000-record Gemini run.
- Project the measured Gemini run to all 288,904 public records as
  approximately $2,141, excluding embeddings and reranking.
- Show the soon-to-be-merged unified NCBI/Sonnet 5 reranking path with solid
  workflow lines and keep its behavior in the shared MCP/search service.
- Distinguish ontology-backed IDs from controlled assay labels and experimental heavy-tailed ontology mapping.

---

### Task 1: Replace the root README with the hackathon narrative

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: `wiki/20-Architecture-Overview.md`, `wiki/21-Ingestion-Pipeline.md`, `wiki/22-Ontology-Normalization.md`, `wiki/23-Search-and-Retrieval.md`, `wiki/25-Embeddings-and-Cost.md`, `wiki/27-MCP-Interface.md`, `wiki/42-Build-Log.md`, `wiki/57-Canonical-Production-Pipeline.md`, and local structured-extraction reports.
- Produces: a standalone judge-facing project overview with links to the deployed demo and deeper wiki evidence.

- [x] **Step 1: Replace the existing operator runbook**

Write a compact README with these sections in order:

```markdown
# GEOscope

> See what GEO search misses.

[Try the live demo](https://geoscope.kevinformatics.com)

## Goal
## Overview
## What we accomplished
## Methods
## Experiments and findings
## Current scope
## Project documentation
```

The body must include these verified facts:

- 288,904 canonical GSE documents, Gemini artifact rows, Elasticsearch documents, and `embedding_gemini_3072` vectors at the completed checkpoint.
- Metadata-only SOFT input, one canonical series record per GSE, Prefect orchestration, Gemini Batch embeddings, and Elasticsearch audit.
- BM25, 3,072-dimensional Gemini dense retrieval, native RRF hybrid fusion, normalized filters, and disjunctive facets.
- Organism mapped to NCBITaxon IDs, sex mapped to PATO IDs, and assay represented by controlled category/detail labels.
- Public React/FastAPI demo and exactly three MCP tools: `search_datasets`, `get_dataset`, and `facet_values`.
- BGE Small, MedCPT, Qwen3, and Gemini embedding artifacts; metadata-source and datastore comparisons; ontology-normalization measurements.
- Structured extraction prototypes across multiple biological and experimental domains, followed by a 10,000-GSE Gemini Flash-Lite Batch experiment with 15 successful jobs, 9,439 validated outputs, 561 recorded failures, a $57.66 estimate, and a $110.10 conservative maximum.
- A clearly labeled linear full-corpus projection of about $1,666 expected and $3,181 at the same conservative maximum, explaining why structured extraction was not placed on the deployed full-corpus path.

- [x] **Step 2: Validate Markdown and quantitative claims**

Run:

```bash
git diff --check -- README.md
rg -n "288,904|3,072|9,439|561|57.66|110.10|1,666|3,181|search_datasets|get_dataset|facet_values" README.md
```

Expected: `git diff --check` exits zero, and `rg` finds every required evidence point.

- [x] **Step 3: Confirm scope and worktree isolation**

Run:

```bash
rg -n "uv sync|docker compose|pip install|pnpm install|single-cell keyword thesis" README.md
git status --short
git diff -- README.md
```

Expected: the first command returns no matches; status still shows pre-existing frontend/wiki changes plus the planned documentation files; the README diff contains only the judge-facing rewrite.

- [x] **Step 4: Review the final narrative against the approved design**

Confirm the README:

- leads with the live demo and goal;
- explains the complete shared pipeline and consumer surfaces;
- separates accomplished production behavior from experiments;
- states current GSE-level and ontology-normalization limits honestly; and
- links to the wiki architecture, methods, experiments, build log, and canonical pipeline pages.

---

### Task 2: Add extraction economics and unified LLM reranking

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: local extraction run reports, recorded Gemini usage metadata, and
  the unified NCBI reranking worktree design.
- Produces: judge-facing cost rationale and a solid end-to-end workflow shared
  by the website and MCP server.

- [x] **Step 1: Update the workflow and retrieval method**

Add solid workflow edges for concurrent Elasticsearch and NCBI candidate
retrieval, GSE merge/deduplication, Sonnet 5 reranking, and delivery through the
shared website/MCP search layer. Explain that exact accession lookups bypass
reranking, while natural-language search uses 40 Elasticsearch candidates and
20 NCBI candidates to produce the final top 10. Document deterministic
Elasticsearch fallback behavior for optional NCBI or reranker failures.

- [x] **Step 2: Replace manifest-only estimates with measured experiment cost**

State that the structured-extraction work cost $121.61 in total: $47.52 for the
OpenAI pilot and $74.10 for the Gemini 10,000-record run. Preserve the manifest
estimate of $57.66 as pre-run context, then project the measured Gemini cost to
all 288,904 public records as approximately $2,141, with the existing
conservative ceiling of approximately $3,181. Explicitly exclude embedding and
reranking costs from this total.

- [x] **Step 3: Verify claims and documentation contracts**

Run:

```bash
git diff --check -- README.md
rg -n "121\.61|47\.52|74\.10|2,141|3,181|Sonnet 5|40 Elasticsearch|20 NCBI|top 10|search_datasets|get_dataset|facet_values" README.md
env UV_CACHE_DIR=/private/tmp/geo-index-uv-cache uv run pytest -q
pnpm test -- --run
```

Expected: the diff check exits zero, every required claim is present, the
Python suite passes with only opt-in live integrations skipped, and all
frontend tests pass. At the verified checkpoint, the Python suite reported 391
passed and 9 skipped, and the frontend suite reported 7 passed.
