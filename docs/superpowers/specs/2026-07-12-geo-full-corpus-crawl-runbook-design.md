# GEO Full-Corpus Crawl Runbook — Design

**Date:** 2026-07-12
**Status:** approved scope; documentation implementation pending review

## Purpose

Preserve the operational evidence and reusable procedure from the July 2026
full-corpus GEO family-SOFT crawl. The documentation must distinguish a
completed public corpus from records that are temporarily or permanently
unavailable, so later freshness work does not mistake an expected access gap
for a failed crawl.

## Documentation changes

1. Add `wiki/56-GEO-Full-Corpus-Crawl-Runbook.md` as the canonical operational
   runbook. It will contain:
   - the completed-run snapshot and its explicit scope;
   - observed concurrency and retry behavior;
   - error classification, including private/embargoed accessions;
   - handling of exceptionally large family-SOFT files; and
   - a repeatable incremental-top-up procedure.
2. Add short links from `wiki/42-Build-Log.md` and
   `wiki/54-Incremental-Corpus-Future-State.md` to the new runbook. The build
   log remains historical; the incremental-state note remains strategic.

## Facts to preserve

- The catalog snapshot contained 288,905 GSE accessions.
- 288,904 public metadata-only family SOFT files were landed and validated.
- `GSE335901` is private, scheduled for public release on 2028-07-08. Its FTP
  family file returned 404 and its `acc.cgi` brief request returned an HTML
  access page rather than SOFT. It is an expected unavailable record, not a
  download defect.
- The stable operating point was 12 concurrent FTP downloads. Increasing to
  16 and 20 materially increased final 503 failures; no 429 responses were
  observed.
- Each transient failure receives five total attempts with 1, 2, 4, and
  8-second waits between retries. Missing targets are reconsidered by a later
  pass; completion requires documenting any residual unavailable records.
- One exceptionally large family file (`GSE7576`) exceeded 33 GB compressed.
  Its long streaming transfer held completion counters steady while it
  continued to grow, so file growth must be checked before treating a quiet
  progress log as a stall.

## Incremental procedure

The runbook will prescribe a dated, manifest-driven delta process:

1. Query the live GEO GSE count and retain a dated catalog snapshot.
2. Diff that snapshot against locally materialized targets; never infer a
   delta from a historical failure log.
3. Fetch only newly public or previously unavailable accessions with the
   bounded single-host concurrency used for the full crawl.
4. Record each residual accession as `private_or_embargoed`,
   `not_yet_generated`, or `transient_failure`, with last-checked time and
   evidence. Do not treat the first two as ordinary retry failures.
5. Re-run the delta on a measured cadence; promote successful outputs through
   the existing canonical-record and embedding pipeline idempotently.

No crawler code, runtime configuration, or production data will change as
part of this documentation update.
