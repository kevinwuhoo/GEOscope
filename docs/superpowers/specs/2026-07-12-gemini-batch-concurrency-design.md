# Gemini Batch Concurrency Design

**Date:** 2026-07-12

**Status:** Approved for implementation planning

## Context

The canonical Gemini embedding build contains 249,736 records in 250
deterministic JSONL shards. The existing adapter processes one shard at a time:
upload, submit, poll to completion, download, validate, then advance. That
behavior is resumable and fail-closed, but observed provider latency makes the
complete run take roughly 10–15 hours.

The active Gemini project is usage Tier 3. Google permits multiple concurrent
batch jobs, subject to the project's per-model enqueued-token quota. The user
selected a fixed concurrency of four.

The production run is paused safely. Twelve result shards are downloaded. The
next shard has a persisted provider job ID and has succeeded provider-side but
has not yet been downloaded. Resume behavior must consume that existing job;
it must not upload or submit the shard again.

## Goals

- Keep up to four Gemini embedding batch jobs active concurrently.
- Preserve the current paid-work guards, deterministic shards, and exact
  GSE/result alignment.
- Retain a single writer for `gemini_state.json`; do not introduce threads,
  processes, or state-file locks.
- Resume all existing uploads and jobs without duplicate paid submissions.
- Remain fail-closed for ambiguous provider outcomes.
- Assemble the final matrix in canonical shard and GSE order regardless of job
  completion order.
- Preserve concurrency `1` as the backward-compatible default.

## Non-goals

- Changing request text, shard boundaries, model, dimensions, or price
  estimation.
- Running synchronous per-document token counting.
- Increasing concurrency automatically beyond the configured value.
- Deleting provider files or changing their retention policy.
- Redesigning artifact publication or local-model builders.
- Fixing the provider's missing `tokenCount` field in this change.

## Public interface

Add a positive integer `gemini_concurrency` setting and propagate it through
the existing builder boundary to `build_gemini_vectors`. The command-line
interface gains:

```text
--gemini-concurrency N
```

Its default is `1`. Values below one are rejected before client construction.
The resumed production command will explicitly pass `--gemini-concurrency 4`.
The option has no effect on local embedding providers.

## Architecture

### Single cooperative coordinator

One process and one SDK client own all provider transitions. Parallelism exists
at Google: the coordinator submits up to four jobs, then polls their states in
round-robin cycles. Because every local mutation remains serialized, the
existing atomic JSON replacement continues to provide the durable state
boundary without locks or merge logic.

At startup, the coordinator classifies every shard:

1. **Complete:** its local result JSONL exists. No provider call is necessary.
2. **Submitted:** it has a persisted `job_name` but no local result. It enters
   the active set and is polled; it is never uploaded or submitted again.
3. **Submission intent:** it has a display name but no job ID. Reconcile that
   identity against provider jobs before any new paid request.
4. **Uploaded:** it has an uploaded file but no safe submission identity. Keep
   the existing fail-closed legacy behavior.
5. **Pending:** it has no upload, intent, job, or local result and is eligible
   to fill an open slot.

Persisted nonterminal jobs count against concurrency. If a resumed state
contains more active jobs than the configured concurrency, the coordinator
polls all of them and submits nothing new until the active count falls below
the limit. It never cancels paid work merely to enforce a lower local setting.

### Fill and poll cycle

Each scheduler cycle performs these operations in order:

1. Poll every active job once.
2. For each succeeded job, persist the output file name, download to a
   temporary path, atomically publish the result JSONL, and remove the job from
   the active set.
3. Persist a terminal failure and stop submitting new work. Existing provider
   job IDs remain recoverable; the command exits with the provider error.
4. Fill open slots from pending shards in deterministic shard-index order.
   Upload and submission remain sequential local operations, and each durable
   transition is written before proceeding.
5. Sleep for the normal polling interval only when the cycle made no progress.

This ordering downloads already-completed work before creating more paid work
and ensures freed slots are reused promptly.

### Submission safety

For each pending shard, preserve the current sequence:

1. Upload the deterministic JSONL file and persist `uploaded_file_name`.
2. Generate and persist a unique `submission_display_name`.
3. Call `batches.create_embeddings`.
4. Persist `job_name` immediately after the provider returns it.

If the process fails after provider creation but before job-name persistence,
restart reconciliation accepts exactly one provider job matching the persisted
display name. Zero or multiple matches remain ambiguous and fail closed except
for a definitive quota rejection described below.

### Quota backoff

If `create_embeddings` returns a definitive HTTP 429 response:

1. Persist the 429 outcome while retaining the submission display name.
2. List provider jobs with that exact display name.
3. Accept and persist exactly one match.
4. Fail closed on multiple matches.
5. If there are zero matches, atomically clear only that failed submission
   intent, retain the existing upload, record a bounded exponential backoff,
   and retry later with a new display name.

The backoff starts at 30 seconds and caps at 300 seconds. A quota rejection
pauses new submissions but does not prevent polling or downloading already
active jobs. Network errors, timeouts, and other ambiguous create failures do
not use the zero-match exception; they retain the intent and preserve the
existing fail-closed behavior.

### Ordered result assembly

Provider lifecycle management and matrix assembly become separate phases.
The coordinator does not append vectors in provider completion order.

After every shard has a local result JSONL, the coordinator reads those files
in deterministic shard-index order, validates every row, aggregates row
failures in canonical order, and concatenates vectors in the existing record
inventory order. The final shape remains `(record_count, 3072)` with contiguous
`float32` storage.

## State compatibility

The existing schema-version-2 state remains readable. Any new retry metadata
is optional and has conservative defaults, so paused sequential runs resume
without migration. Existing complete results, uploads, display names, job IDs,
job states, and output file names remain authoritative.

The first resumed operation for the paused production run must retrieve the
persisted successful job and download its output. Tests must prove no upload or
batch creation occurs for that shard.

## Error behavior

- A terminal provider job failure stops new submissions and raises with the
  job name, state, and provider error.
- Per-row errors are collected from all downloaded result shards and raised as
  one `GeminiBatchRowError`; no artifact is published.
- Missing, duplicate, unexpected, wrong-dimensional, or nonfinite responses
  retain their current validation failures.
- An exception never erases provider identifiers or completed result files.
- Restart never resubmits a shard with a persisted job ID.

## Testing

All tests use fake clients and make no paid provider calls. Add coverage for:

- concurrency `1` preserving sequential behavior;
- initial fill creating exactly four active jobs;
- one completion downloading its result and admitting exactly one next shard;
- round-robin polling without completion-order matrix reordering;
- resume with complete, provider-succeeded, running, intent-only, and pending
  shards in one state;
- resume downloading the paused production pattern without upload or create;
- a persisted active count above the configured limit;
- definitive 429 with zero, one, and multiple reconciliation matches;
- bounded backoff while existing jobs continue to be polled;
- ambiguous non-429 creation remaining fail-closed;
- terminal job failure stopping new submissions while preserving other job
  IDs;
- deterministic row-error aggregation across out-of-order completions;
- invalid concurrency being rejected before client construction.

Run the focused Gemini and builder suites, then the full test suite. After
tests pass, resume the real build with concurrency four and confirm from the
state file that no more than four nonterminal jobs are active and the paused
job is downloaded rather than recreated.

## Acceptance criteria

- The CLI can resume the existing production state with concurrency four.
- At most four provider jobs are active unless a resumed state already exceeds
  the configured limit.
- Only one process writes the shared state document.
- No completed, submitted, or ambiguous shard is blindly resubmitted.
- All 250 result files assemble into exactly 249,736 aligned 3,072-dimensional
  vectors.
- Concurrency one remains compatible with existing behavior and tests.
- Focused and full test suites pass before the paid run resumes.
