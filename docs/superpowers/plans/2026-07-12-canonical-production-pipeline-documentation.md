# Canonical Production Pipeline Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document one canonical Gemini-only production pipeline, its files and commands, while retaining BGE, MedCPT, and Qwen only as development/evaluation options.

**Architecture:** Add one authoritative wiki runbook and link it from current-state overview, architecture, ingestion, embedding, and README pages. Preserve historical bake-off pages but label them development/evaluation-only so operators cannot mistake them for production instructions.

**Tech Stack:** Markdown, Obsidian wikilinks, pytest documentation assertions, Git.

## Global Constraints

- Production uses only `gemini_embedding_2_3072_v1` and Elasticsearch field `embedding_gemini_3072`.
- `geo-soft-etl` is the canonical production orchestration command.
- BGE, MedCPT, and Qwen remain available only for local development, comparison, regression testing, and historical evaluation.
- Historical design records remain intact; current-state claims must be corrected when misleading.
- Do not expose `.env` secrets or provider IDs.

---

### Task 1: Lock the production documentation contract

**Files:**
- Modify: `tests/test_primary_path_docs.py`
- Test: `tests/test_primary_path_docs.py`

**Interfaces:**
- Consumes: repository Markdown through `_read(path: str) -> str`
- Produces: assertions requiring the canonical pipeline page, Gemini-only production copy, concrete file locations, and development-only alternate-model copy

- [ ] **Step 1: Add failing documentation assertions**

Require `README.md` and `wiki/57-Canonical-Production-Pipeline.md` to contain
`Canonical production pipeline`, `geo-soft-etl`,
`gemini_embedding_2_3072_v1`, `embedding_gemini_3072`,
`data/processed/series_records`, `data/processed/embedding_artifacts`,
`data/processed/elasticsearch_load_report.json`, and
`development/evaluation only`. Require current overview/architecture/ingestion
pages to link `[[57-Canonical-Production-Pipeline]]`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_primary_path_docs.py -q`

Expected: FAIL because `wiki/57-Canonical-Production-Pipeline.md` does not exist.

### Task 2: Write the canonical runbook and current-state links

**Files:**
- Create: `wiki/57-Canonical-Production-Pipeline.md`
- Modify: `README.md`
- Modify: `wiki/Home.md`
- Modify: `wiki/00-Overview.md`
- Modify: `wiki/20-Architecture-Overview.md`
- Modify: `wiki/21-Ingestion-Pipeline.md`
- Modify: `wiki/25-Embeddings-and-Cost.md`
- Modify: `wiki/52-Embedding-Bakeoff-Runbook.md`
- Test: `tests/test_primary_path_docs.py`

**Interfaces:**
- Consumes: CLI flags from `geo_index.prefect_etl`, artifact contract from `geo_index.build_embedding_artifact`, and the live 288,904-row production report
- Produces: one operator-facing production procedure and unambiguous dev/evaluation labeling for alternate embeddings

- [ ] **Step 1: Create the authoritative wiki runbook**

Document the complete flow, environment setup, exact `geo-soft-etl` command,
all durable and temporary paths, Batch-only behavior, `$9.55` minimum ceiling
for the completed inventory estimate, concurrency 4, resume behavior,
validation commands, Elasticsearch field/index names, current coverage, and
explicit dev-only model commands.

- [ ] **Step 2: Update current-state entry points**

Add a concise `Canonical production pipeline` section to `README.md`, link the
new runbook from Home/Overview/Architecture/Ingestion, and replace stale
embedding selection copy with Gemini-only production language.

- [ ] **Step 3: Label historical bake-off instructions**

Add a prominent notice to `wiki/52-Embedding-Bakeoff-Runbook.md` that it is a
development/evaluation workflow and not the canonical production pipeline.

- [ ] **Step 4: Run focused documentation tests**

Run: `uv run pytest tests/test_primary_path_docs.py -q`

Expected: all documentation contract tests pass.

### Task 3: Verify and publish

**Files:**
- Verify: all files listed above

**Interfaces:**
- Consumes: completed documentation edits
- Produces: a clean, tested commit on `main` pushed to `origin/main`

- [ ] **Step 1: Scan for conflicting current-state claims**

Run targeted `rg` searches for production/default claims involving BGE,
MedCPT, or Qwen and correct current-state contradictions while leaving clearly
historical/evaluation sections intact.

- [ ] **Step 2: Run verification**

Run: `git diff --check`

Run: `uv run pytest tests/test_primary_path_docs.py -q`

Run: `uv run pytest -q`

Expected: whitespace check succeeds; focused and full suites pass with only
their documented opt-in integration skips.

- [ ] **Step 3: Commit and push**

Stage only the plan, documentation test, README, and wiki files from this task.
Commit with `docs: document canonical Gemini production pipeline`, then push
`main` to `origin/main`.
