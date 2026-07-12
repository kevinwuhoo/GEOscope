# Canonical SOFT ETL Wiki Authority Design

**Date:** 2026-07-11

## Goal

Replace fragmented and partly stale planning guidance with one authoritative
wiki page for the implemented SOFT-to-canonical-record ETL and embedding
artifact pipeline, while preserving the original plans as clearly labeled
history.

## Scope

- Archive the full contents of `wiki/52-Embedding-Bakeoff-Runbook.md` and
  `wiki/53-Prefect-SOFT-ETL-and-Embedding-Prototype-Plan.md` under
  `wiki/archived/` with dated filenames and explicit archived/superseded
  notices.
- Replace the two original files with short redirect stubs so existing
  bookmarks and external references lead readers to the authoritative page.
- Create `wiki/56-Canonical-SOFT-ETL-and-Embedding-Operations.md` as the sole
  current operational source of truth.
- Update active wiki backlinks that currently treat pages 52 or 53 as current
  guidance.
- Preserve `wiki/42-Build-Log.md` as the chronological evidence log rather than
  duplicating all historical measurements in the new operations page.

This is documentation-only work. It does not change ETL, embedding, Prefect,
normalization, storage, Elasticsearch, or provider behavior.

## Archive Layout

Create:

```text
wiki/archived/
  2026-07-11-52-Embedding-Bakeoff-Runbook.md
  2026-07-11-53-Prefect-SOFT-ETL-and-Embedding-Prototype-Plan.md
```

Each archived page retains its original body and gains frontmatter or a top
notice containing:

- `status: archived`;
- archive date `2026-07-11`;
- a link to
  `[[56-Canonical-SOFT-ETL-and-Embedding-Operations]]`;
- a statement that the page records the implementation plan/runbook and is no
  longer operational authority.

The original page paths remain as minimal stubs. A stub contains the original
title, an archived status, a link to the dated historical copy, and a prominent
link to page 56. It must not retain operational instructions that could diverge
from page 56.

## Authoritative Page

`wiki/56-Canonical-SOFT-ETL-and-Embedding-Operations.md` will describe the
merged state on `main` and cover:

1. **Authority and scope** — this page is current; page 42 is evidence;
   incremental state belongs to page 54; Elasticsearch remains outside this
   ETL implementation.
2. **Pipeline data flow** — raw family SOFT, conservative table stripping,
   metadata-only SOFT, canonical JSON, normalized projections, raw narrative
   `embed_text`, and independent embedding artifacts.
3. **SOFT simplification contract** — bulk sample/platform/series-matrix tables
   are removed; complete repeated series/platform/sample attribute maps are
   retained; redundant retained series-table rows are ignored by the canonical
   parser; unknown metadata attributes remain structured.
4. **Canonical schema and paths** — bucketed GSE JSON paths, all top-level
   aggregate/normalized fields, nested attribute maps, and sample/platform
   shapes.
5. **Existence-only state machine** — skip-before-read, deletion-for-rebuild,
   no hashes/mtimes/update detection/versions, bounded Prefect batches, all
   futures resolved, atomic publication, and malformed-input behavior.
6. **Embedding-owner boundary** — the exact `build_missing_embeddings(...)`
   interface, `replace_gses` semantics including retries and explicit record
   deletion, and durable replacement intent.
7. **Artifact contract and storage** — `vectors.npy`, `ids.json`, and
   `metadata.json`; numeric GSE alignment; float32 validation; memory-mapped
   matrix access; membership via the smaller ID file; no need to discard
   existing artifacts if storage changes later.
8. **Model status** — final BGE, MedCPT, and Qwen dimensions/counts/truncations;
   Gemini request preparation status and the fact that no paid corpus job was
   submitted.
9. **Gemini safety** — full untruncated formatted input, informational byte
   estimate, no default synchronous token counting, bounded batch/file API,
   cross-shard provider-error aggregation, resumable state, deterministic job
   reconciliation, and fail-closed ambiguous states.
10. **Commands and recovery** — direct CLI invocation, explicit one-record
    rebuild, artifact rebuild/adoption, optional Prefect observability, and the
    no-paid Gemini guard.
11. **Validation evidence** — frozen corpus counts, second-run zero-parse proof,
    deletion/rebuild proof, 5,000-file independent validation, final test
    results, and links to the detailed build log.
12. **Deferred work and deviations** — content hashes/snapshots/deltas, Gemini
    paid artifact, matrix-assembly memory optimization, local Qwen token limit,
    and search/Elasticsearch work owned elsewhere.

Operational claims must match merged code and the final evidence already
recorded in `wiki/42-Build-Log.md`. The new page should summarize measurements
needed to operate or audit the pipeline and link to page 42 for full history.

## Backlink Migration

Update current guidance links in these pages to point to page 56:

- `wiki/Home.md`
- `wiki/21-Ingestion-Pipeline.md`
- `wiki/40-Roadmap.md`
- `wiki/42-Build-Log.md`
- `wiki/48-Alternate-Embedding-Bakeoff.md`
- `wiki/49-Alternate-Embedding-Bakeoff-Implementation-Plan.md`
- `wiki/51-Search-Database-Bakeoff-and-Elasticsearch-Plan.md`
- `wiki/54-Incremental-Corpus-Future-State.md`
- `wiki/55-Prefect-and-Local-Elasticsearch-Coworker-Prompts.md`

When a sentence explicitly discusses the historical plan or bakeoff proposal,
link to the dated archived page instead. When it describes current commands,
contracts, or ownership, link to page 56.

Update `wiki/21-Ingestion-Pipeline.md` from checkpoint language to the frozen
249,736-record implementation state, without deleting its separate historical
GEOmetadb or deferred-v2 discussion. Update `wiki/Home.md` so page 56 is the
current entry point and pages 52/53 are labeled archived rather than current.

## Verification

Before committing:

- search active wiki pages for links that still present pages 52/53 as current;
- verify both redirect stubs link to page 56 and the dated archives;
- verify both archives link back to page 56;
- confirm page 56 contains every required operational section above;
- check the frozen counts, model dimensions, truncation counts, Gemini dry-run
  metrics, validator result, and final test result against page 42 and artifact
  metadata;
- run a Markdown/wikilink audit for all changed pages;
- confirm only the intended wiki files and this documentation workflow's spec
  and plan are committed, leaving unrelated dirty files untouched.

## Non-Goals

- Do not rewrite the chronological build log as a runbook.
- Do not mark deferred incremental or Elasticsearch work as implemented.
- Do not change files under `data/` or track embedding matrices in Git.
- Do not modify application code, tests, dependencies, or generated Obsidian
  state.
