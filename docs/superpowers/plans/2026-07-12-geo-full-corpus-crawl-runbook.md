# GEO Full-Corpus Crawl Runbook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the completed full-corpus GEO crawl as a cited operational runbook and define the bounded process for later incremental additions.

**Architecture:** A new numbered wiki note owns operational evidence and the repeatable delta procedure. The build log and future-state note link to it for their respective historical and strategic contexts; the master source index records every external source added by the note.

**Tech Stack:** Markdown, Obsidian wikilinks, NCBI GEO web pages, NCBI E-utilities documentation.

## Global Constraints

- Follow `wiki/CLAUDE.md`: every external factual claim needs an inline citation, a `## Sources` section, and an entry in `wiki/99-Sources.md`.
- Label the captured crawl as a **(v1)** operational record; incremental manifest work is **(v2+)** and remains documented rather than implemented.
- Do not modify crawler code, launchd configuration, source data, or existing unrelated worktree changes.
- Stage and commit only the documentation files listed in this plan.

---

### Task 1: Create the canonical crawl runbook and source-index entries

**Files:**
- Create: `wiki/56-GEO-Full-Corpus-Crawl-Runbook.md`
- Modify: `wiki/99-Sources.md`

**Interfaces:**
- Consumes: the completed crawl evidence in `data/raw/_crawl_daemon.log`, the private-accession page for `GSE335901`, and the approved design in `docs/superpowers/specs/2026-07-12-geo-full-corpus-crawl-runbook-design.md`.
- Produces: the canonical `[[56-GEO-Full-Corpus-Crawl-Runbook]]` wiki target and indexed source URLs for downstream links.

- [ ] **Step 1: Write the runbook with explicit outcome and scope**

  Create a front matter block with title `GEO Full-Corpus Crawl Runbook`, tags
  `runbook`, `ingestion`, `geo`, `crawl`, status `completed-v1-run`, and date
  `2026-07-12`. State that the catalog snapshot was 288,905 GSE accessions and
  that 288,904 public metadata-only family SOFT files were landed and validated.
  State separately that one accession remains intentionally unavailable, so the
  count is not presented as a silent crawl defect.

- [ ] **Step 2: Record observed behavior and operator signals**

  Add an `## What the run established` section containing these facts:

  ```markdown
  - 12 concurrent FTP workers was the stable operating point; 16 and 20 caused
    materially more final 503 failures. No 429 response was observed.
  - A transient request has five total attempts, separated by 1, 2, 4, and
    8-second waits. A later pass reconsiders targets whose final metadata file
    is still absent.
  - A quiet completion counter is not itself a stall: `GSE7576` streamed more
    than 33 GB of compressed SOFT before its metadata target was published.
    Inspect the active `.tmp` file's size and modification time before
    restarting a quiet crawler.
  ```

  Explain that the final loop stops after a pass adds no new target and writes
  `_crawl.DONE`; therefore residual accessions must be classified and recorded
  before treating the run as complete.

- [ ] **Step 3: Classify the residual record accurately**

  Add an `## Residual unavailable accession` section that identifies
  `GSE335901` as private until 2028-07-08. State that its FTP family file
  returned 404 and that the `acc.cgi` brief request returned an HTML access
  page, not SOFT. Call it `private_or_embargoed`, not a retryable transport
  failure. Cite the direct GEO accession page inline.

- [ ] **Step 4: Define the future incremental procedure**

  Add a `## Incremental additions (v2+)` section with this ordered procedure:

  ```markdown
  1. Record a dated live GSE enumeration and retain the catalog snapshot used
     for the run.
  2. Diff the snapshot against final metadata targets; do not use an appended
     failure log as the delta source.
  3. Fetch only new or previously unavailable accessions with bounded,
     measured single-host concurrency. Do not fan out across IP addresses to
     circumvent source limits.
  4. Persist residual rows with accession, classification
     (`private_or_embargoed`, `not_yet_generated`, or `transient_failure`),
     last-checked time, and evidence URL or response summary.
  5. Send only successful new metadata files through the existing idempotent
     canonical-record and embedding path; preserve unavailable rows for a
     later scheduled recheck.
  ```

  Clarify that this is a documented future process, not a new v1 scheduler or
  crawler feature. Cite NCBI's E-utilities usage guidance for the rate-limit
  statement.

- [ ] **Step 5: Add sources locally and to the master index**

  End the runbook with `## Sources` containing the direct GSE335901 GEO page
  and NCBI's E-utilities usage guide. Add the same two URLs under the GEO/NCBI
  section in `wiki/99-Sources.md`, avoiding duplicate entries if they already
  exist.

### Task 2: Add scoped cross-links from the existing historical and future notes

**Files:**
- Modify: `wiki/42-Build-Log.md`
- Modify: `wiki/54-Incremental-Corpus-Future-State.md`

**Interfaces:**
- Consumes: `[[56-GEO-Full-Corpus-Crawl-Runbook]]` from Task 1.
- Produces: historical and future-state entry points that route readers to the
  operational details without duplicating them.

- [ ] **Step 1: Link the build log at its current-architecture update**

  Add one concise paragraph immediately after the current-architecture update
  in `wiki/42-Build-Log.md`:

  ```markdown
  > **Full-corpus crawl record (2026-07-12):** the complete operational outcome,
  > retry/concurrency evidence, and the one private residual accession are in
  > [[56-GEO-Full-Corpus-Crawl-Runbook]].
  ```

- [ ] **Step 2: Link the future-state note from its purpose section**

  Add one paragraph immediately after the first `## Purpose` paragraph in
  `wiki/54-Incremental-Corpus-Future-State.md`:

  ```markdown
  The observed full-corpus crawl and its bounded top-up procedure are captured
  in [[56-GEO-Full-Corpus-Crawl-Runbook]]. This page remains the **(v2+)**
  design for immutable record and embedding revisions rather than a crawler
  implementation plan.
  ```

### Task 3: Verify documentation integrity and commit the isolated documentation change

**Files:**
- Verify: `docs/superpowers/specs/2026-07-12-geo-full-corpus-crawl-runbook-design.md`
- Verify: `docs/superpowers/plans/2026-07-12-geo-full-corpus-crawl-runbook.md`
- Verify: `wiki/56-GEO-Full-Corpus-Crawl-Runbook.md`
- Verify: `wiki/42-Build-Log.md`
- Verify: `wiki/54-Incremental-Corpus-Future-State.md`
- Verify: `wiki/99-Sources.md`

**Interfaces:**
- Consumes: the documentation changes from Tasks 1–2.
- Produces: a focused commit containing only the crawl documentation.

- [ ] **Step 1: Check wiki conventions and references**

  Run:

  ```bash
  rg -n '56-GEO-Full-Corpus-Crawl-Runbook|GSE335901|private_or_embargoed|Incremental additions' \
    wiki/56-GEO-Full-Corpus-Crawl-Runbook.md wiki/42-Build-Log.md \
    wiki/54-Incremental-Corpus-Future-State.md wiki/99-Sources.md
  ```

  Expected: the runbook exists; both cross-links resolve by note name; the
  residual classification appears; and both external sources are locally
  indexed.

- [ ] **Step 2: Check scope and unfinished-marker hygiene**

  Run:

  ```bash
  rg -n -i 'TO''DO|TB''D|place''holder|implement'' later' \
    docs/superpowers/specs/2026-07-12-geo-full-corpus-crawl-runbook-design.md \
    docs/superpowers/plans/2026-07-12-geo-full-corpus-crawl-runbook.md \
    wiki/56-GEO-Full-Corpus-Crawl-Runbook.md || true
  git diff --check
  ```

  Expected: no unfinished-marker output and no whitespace errors.

- [ ] **Step 3: Review and stage only documentation files**

  Run:

  ```bash
  git diff -- \
    docs/superpowers/specs/2026-07-12-geo-full-corpus-crawl-runbook-design.md \
    docs/superpowers/plans/2026-07-12-geo-full-corpus-crawl-runbook.md \
    wiki/56-GEO-Full-Corpus-Crawl-Runbook.md wiki/42-Build-Log.md \
    wiki/54-Incremental-Corpus-Future-State.md wiki/99-Sources.md
  git add \
    docs/superpowers/specs/2026-07-12-geo-full-corpus-crawl-runbook-design.md \
    docs/superpowers/plans/2026-07-12-geo-full-corpus-crawl-runbook.md \
    wiki/56-GEO-Full-Corpus-Crawl-Runbook.md wiki/42-Build-Log.md \
    wiki/54-Incremental-Corpus-Future-State.md wiki/99-Sources.md
  git diff --cached --check
  ```

  Expected: the staged diff contains only the six listed documentation files
  and is free of whitespace errors.

- [ ] **Step 4: Commit the documentation**

  Run:

  ```bash
  git commit -m "docs: record GEO full-corpus crawl runbook"
  ```

  Expected: one commit containing only the crawl runbook, its design/plan, and
  its cross-links/source index.
