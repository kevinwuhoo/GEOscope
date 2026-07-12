---
title: GEO Full-Corpus Crawl Runbook
tags: [runbook, ingestion, geo, crawl]
status: completed-v1-run
created: 2026-07-12
---

# 56 · GEO Full-Corpus Crawl Runbook

← [[Home]] · historical evidence in [[42-Build-Log]] · future corpus identity
in [[54-Incremental-Corpus-Future-State]]

This is the operational record for the **(v1)** full-corpus family-SOFT crawl
completed on 2026-07-12. It records the outcome, the observed limits, and the
bounded process for a later incremental top-up. It does not introduce a new
crawler or scheduler.

## Completed-run outcome

The frozen catalog contained **288,905 GSE accessions**. The crawl landed and
validated **288,904** public, metadata-only family SOFT files. The single
residual accession, `GSE335901`, is not a crawl defect: GEO reports that it is
private and scheduled for public release on 2028-07-08. [GEO accession page](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE335901)

The final success condition is therefore:

```text
288,904 public metadata files materialized
1 private_or_embargoed accession recorded for later recheck
```

Treat this as a completed public snapshot, not as an assertion that every
catalog identifier was publicly downloadable on the run date.

## What the run established

- **Concurrency:** 12 concurrent FTP workers was the stable operating point.
  Runs at 16 and 20 produced materially more final 503 failures. No 429
  response was observed.
- **Retries:** a transient request receives five total attempts, with waits of
  1, 2, 4, and 8 seconds between retries. A target without its final metadata
  file remains eligible for the next pass.
- **Completion semantics:** the resume loop stops when a complete pass adds no
  new metadata file, then validates and writes `data/raw/_crawl.DONE`. Before
  accepting that marker, classify every residual target rather than treating
  its existence as proof that all catalog accessions were public.
- **Large-file signal:** a quiet completion counter is not, by itself, a
  stalled crawler. `GSE7576` streamed more than 33 GB of compressed family
  SOFT before its small metadata output could be published. Inspect active
  `*.tmp` file size and modification time before restarting a quiet job.
- **Source etiquette:** retain bounded, measured concurrency. Do not fan out
  across IP addresses to evade source limits; NCBI publishes explicit usage
  guidance for its programmatic services. [NCBI E-utilities usage guidance](https://www.ncbi.nlm.nih.gov/books/NBK25497/)

## Residual unavailable accession

| Accession | Classification | Evidence | Action |
|---|---|---|---|
| `GSE335901` | `private_or_embargoed` | FTP family-SOFT returned 404; `acc.cgi?view=brief` returned an HTML access page, and GEO's accession page schedules release for 2028-07-08. [GEO accession page](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE335901) | Preserve a residual record; do not repeatedly retry before the release date. |

`private_or_embargoed` is distinct from `not_yet_generated` (a newly public
record whose family file is still being built) and `transient_failure`
(network or service failure after the bounded retry budget). Only the last
class is an ordinary immediate retry candidate.

## Incremental additions (v2+)

The next freshness run should be a dated, manifest-driven delta—not another
unbounded full crawl:

1. Record a dated live GSE enumeration and retain the exact catalog snapshot
   that supplied the run.
2. Diff that snapshot against final metadata targets. Do not derive a delta
   from the appended `_failures.log`; it records attempts, not current source
   state.
3. Fetch only new or previously unavailable accessions with the bounded,
   measured single-host concurrency established above.
4. Persist residual rows with accession, classification
   (`private_or_embargoed`, `not_yet_generated`, or `transient_failure`),
   last-checked time, and an evidence URL or response summary.
5. Route only successful new metadata files through the existing idempotent
   canonical-record and embedding path. Preserve unavailable rows for a later
   scheduled recheck.

This documents the **(v2+)** operational contract. It does not implement a
cron, manifest format, or source-change detector; those belong to the deferred
corpus-identity design in [[54-Incremental-Corpus-Future-State]].

## Operator checklist

Before a top-up:

1. Snapshot the live catalog count and accession list with a date.
2. Verify the selected concurrency against a small measured sample.
3. Confirm there is enough disk for raw temporary files as well as stripped
   metadata; a single family file can be tens of GB.

During a top-up:

1. Watch target-file count, the crawler log, and LaunchAgent state together.
2. On a quiet log, inspect active `*.tmp` file growth before concluding the
   process is hung.
3. Keep `_pending.log` and `_failures.log` as evidence; do not delete them to
   force a retry.

After a top-up:

1. Reconcile catalog, successful targets, and classified residuals.
2. Record the resulting snapshot and residual classifications.
3. Materialize only the newly successful SOFT files downstream; existence-based
   processing makes this idempotent.

## Sources

- [GEO accession display: GSE335901](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE335901)
- [NCBI E-utilities usage guidance](https://www.ncbi.nlm.nih.gov/books/NBK25497/)
