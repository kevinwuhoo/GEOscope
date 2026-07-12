# Incremental Gemini Artifact Synchronization Design

## Status and decision

Approved direction: expand the canonical Gemini artifact from 249,736 records to
the complete current inventory of 288,895 records without re-embedding unchanged
records.

The current canonical record tree contains 39,159 records that are absent from
the published Gemini artifact. A delta-only request preparation produced an
upper-bound estimate of 95,408,485 input tokens across 40 shards. At the Gemini
batch price configured by this project, the exact estimated ceiling is
`$9.5408485`; the operational authorization ceiling will therefore be `$9.55`.
No provider submission is part of this design or its implementation phase.

## Problem

`build_missing_embeddings` currently treats any non-empty `replace_gses` set as
a reason to rebuild the entire embedding artifact. Prefect materialized 39,159
previously missing records and passed those accessions to the builder, so the
builder prepared 289 shards for all 288,895 records and estimated a redundant
`$62.3071` full rebuild. Three jobs were submitted before the mismatch was
noticed; all three were cancelled and their local workspace was quarantined.
The original 249,736-row artifact and Elasticsearch index remained intact.

The interrupted Prefect run also means all 288,895 canonical JSON records now
exist. A subsequent discovery returns no new `created_gses`, so correctness
cannot depend only on Prefect's per-run change set. The builder must compare the
published artifact inventory with the current canonical inventory on every run.

## Goals

- Produce one canonical Gemini artifact whose IDs exactly match the current
  288,895-record inventory.
- Reuse every unchanged vector byte-for-byte.
- Submit only records that are new or explicitly replaced.
- Remove vectors for records no longer present without provider work.
- Preserve the currently published artifact until a complete replacement has
  been assembled and validated.
- Resume a matching interrupted delta run without duplicate submissions.
- Refuse stale or ambiguous recovery state instead of guessing.
- Require both explicit paid authorization and an explicit cost ceiling before
  creating a Gemini client, uploading files, or submitting jobs.
- Let Prefect load every available vector model into Elasticsearch and require
  complete Gemini coverage for the canonical document inventory.

## Non-goals

- Re-embedding the 249,736 unchanged Gemini records.
- Making BGE, MedCPT, or Qwen coverage complete for the 39,159 new records.
- Splitting one model key across permanent base and delta artifacts.
- Changing the Elasticsearch document schema or vector field names.
- Automatically spending against the `$9.55` ceiling during implementation or
  tests.

## Considered approaches

### 1. Incremental canonical merge (selected)

Validate the published artifact, compute the exact inventory delta, encode only
new or replaced records, and assemble one new matrix in canonical numeric-GSE
order. This preserves the single-artifact contract, minimizes provider cost,
and keeps Elasticsearch loading unchanged.

### 2. Full rebuild

The current behavior is simpler but would submit all 288,895 records for an
estimated `$62.3071`, duplicating almost all completed work. It is rejected on
cost, time, and unnecessary provider-risk grounds.

### 3. Permanent base-plus-delta artifacts

Keep the existing artifact immutable and teach every consumer to join multiple
physical artifacts for one model key. This avoids a matrix copy but complicates
validation, registry semantics, Elasticsearch loading, and future compaction.
It is rejected because the canonical single-artifact boundary is valuable and
the one-time local matrix copy is acceptable.

## Synchronization model

The builder loads the current canonical record inventory and, when present, the
validated published artifact. It then partitions IDs as follows:

- `new`: present in the canonical inventory but absent from the artifact;
- `changed`: present in both and explicitly named by `replace_gses`;
- `removed`: present in the artifact but absent from the canonical inventory;
- `reused`: present in both and not explicitly replaced.

When no published artifact exists, the base digest uses an explicit absent-base
sentinel, every target ID is `new`, and the same synchronization path performs
the initial full build.

The provider delta is `new | changed`. `removed` rows are omitted during
assembly without a provider call. `reused` rows are copied from the published
memory-mapped matrix. The target matrix is always written in the same numeric
GSE order as `load_record_inventory`.

This inventory comparison is mandatory even when `replace_gses` is empty. That
invariant is what makes the current post-interruption state recover correctly:
the 39,159 missing artifact IDs are detected from inventory rather than from
the now-empty Prefect change set.

If the artifact IDs already match the canonical inventory and `replace_gses`
is empty, the builder returns `skipped` without preparing requests, requiring
credentials, or checking a paid ceiling.

## Cost authorization boundary

Paid Gemini work requires both:

1. `allow_paid_gemini=True`; and
2. a finite, nonnegative `gemini_max_cost_usd` that is at least the exact
   request estimate.

The standalone builder and `geo-soft-etl` expose the ceiling as
`--gemini-max-cost-usd`. Request files may be prepared locally to calculate the
estimate, but an absent or insufficient ceiling raises an authorization error
before client construction, file upload, batch listing, or job submission.

For the current delta, the eventual paid command will use a `$9.55` ceiling.
Implementation and offline verification use no paid ceiling and make no network
calls. Resuming incomplete provider work still requires the ceiling because
unsent shards may remain.

## Durable delta plan and recovery

Before provider work, the builder atomically writes a versioned synchronization
plan into the model's temporary workspace. The plan records:

- model key and schema version;
- target inventory digest and ordered target IDs;
- published base-artifact ID digest;
- ordered delta IDs and explicitly replaced IDs;
- counts for new, changed, removed, and reused records;
- the exact request estimate used for cost authorization.

On resume, the builder recomputes the current target and base digests. It resumes
only if they match the durable plan. A mismatch raises a diagnostic error and
does not delete, overwrite, upload, or submit anything. This prevents a stale
workspace from being applied to a changed record tree or a different published
artifact.

Gemini's existing deterministic display names, shard state, submission
reconciliation, and result downloads remain responsible for at-most-once shard
submission. Cancelled or terminally failed jobs remain terminal errors; they
are never silently resubmitted. The quarantined workspace from the aborted full
rebuild is outside the active temporary path and is never considered for
recovery.

## Assembly and atomic publication

After the delta provider result is complete, the builder creates the target
`vectors.npy` in the active temporary artifact directory. For each ordered
target ID it copies either the existing published row or the matching delta row.
The builder then writes `ids.json` and `metadata.json` and validates the entire
temporary artifact with the existing registry-backed validator.

Metadata records the target count plus synchronization lineage: prior artifact
count and creation time, reused/encoded/removed counts, and delta provider usage.
The existing required metadata fields remain valid; no consumer-facing schema
version change is needed.

Only a validated temporary artifact may enter the existing marker/backup/rename
swap. Until that point the published artifact is untouched. If provider work,
assembly, validation, or process execution fails, the published artifact
remains readable. After a successful swap, the replacement marker and backup
are removed and a subsequent run returns `skipped`.

## Prefect and Elasticsearch behavior

Prefect continues to pass materialized accessions as `replace_gses`, Gemini
concurrency, paid authorization, and now the explicit cost ceiling. The builder
also discovers missing artifact IDs independently, so a prior materialization
followed by an interrupted embedding run is recoverable.

After a complete Gemini artifact is published, Prefect preflights it before
creating the Elasticsearch client. The existing replacement loader receives all
registered model keys. Existing BGE, MedCPT, and Qwen vectors remain available
for their 249,736 covered records; the 39,159 new records may contain only the
Gemini vector. The load is successful only when:

- every canonical record was indexed;
- the index document count equals 288,895;
- `embedding_gemini_3072` coverage equals 288,895; and
- the loader reports no terminal failures.

## Error handling

- Invalid base artifacts fail before request preparation.
- `replace_gses` values absent from the canonical inventory fail before provider
  work.
- Missing or insufficient cost ceilings fail before any Gemini API interaction.
- Stale synchronization plans fail closed and preserve both the published
  artifact and temporary evidence.
- Provider row failures prevent assembly and publication.
- Missing or duplicate delta rows prevent assembly and publication.
- Nonfinite, misdimensioned, misordered, or count-mismatched merged artifacts
  fail validation before the atomic swap.
- Any materialization or embedding failure prevents Elasticsearch client
  construction, preserving the existing index.
- Elasticsearch bulk or coverage failures remain nonzero terminal flow errors
  with counters and diagnostics retained in the report.

## Verification strategy

### Unit and integration tests

- Adding records encodes only missing IDs and reuses existing rows exactly.
- Explicitly replaced existing IDs are re-encoded while other rows are reused.
- Removed IDs are omitted without provider work.
- An already synchronized inventory skips without credentials or a ceiling.
- The delta matrix is assembled in numeric GSE order regardless of input order.
- A failed delta or failed merged-artifact validation leaves the published
  artifact unchanged.
- Matching durable plans resume; target or base digest mismatches fail closed.
- Missing, nonfinite, or insufficient ceilings fail before client creation.
- A ceiling equal to the exact estimate is accepted; a lower value is rejected.
- Prefect forwards concurrency and the ceiling and gates Elasticsearch on any
  materialization or embedding error.
- Replacement loading preserves every available vector field and enforces full
  Gemini coverage.

### Offline acceptance

- Ruff passes on changed modules and tests.
- Focused incremental-builder, Gemini, Prefect, and Elasticsearch tests pass.
- The full offline suite passes with only documented external-service skips.
- A no-network dry run against the current 288,895-record inventory reports 40
  delta shards and the exact `$9.5408485` estimate, then stops at the missing
  paid authorization boundary.

### Paid and live acceptance (separately authorized)

- Run the 40-shard delta with concurrency four and a `$9.55` ceiling.
- Validate a 288,895-row, 3,072-dimensional canonical Gemini artifact.
- Re-run the builder and require `status=skipped` with no provider work.
- Load all available vectors into Elasticsearch twice and require zero failures,
  288,895 documents, and 288,895 Gemini vectors on both passes.
- Run `geo-soft-etl` end to end and require Gemini `skipped`, Elasticsearch
  `indexed`, complete Gemini coverage, and exit status zero.
