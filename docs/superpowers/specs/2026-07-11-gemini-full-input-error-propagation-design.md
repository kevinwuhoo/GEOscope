# Gemini Full-Input Error Propagation Design

**Date:** 2026-07-11

## Goal

Send complete canonical GEO metadata documents to `gemini-embedding-2` without
preliminary truncation. If Google rejects an input, preserve and return the
provider's row error with its GSE rather than silently changing the document.

## Decisions

- Remove the 8,000-byte Gemini document cap and send the full formatted title
  and `embed_text` for every record.
- Keep the 1,000-request and 100 MiB transport shard limits. These bound files
  and resumable jobs; they do not alter individual documents.
- Do not call `models.count_tokens` by default. Google's exact counter is a
  synchronous API call per document and would add roughly 249,736 preliminary
  network requests to the batch-only corpus path.
- Retain the byte-derived token/cost value only as a conservative estimate. It
  is informational and never rejects or truncates an input.
- Set Gemini `truncation_count` to zero and remove cap-specific usage metadata.
- Scan a completed result shard for all provider row errors. Raise one
  structured exception containing each failing GSE and Google's exact error
  payload. Do not publish a partial canonical artifact.
- Preserve request, state, and result shards after failure so the error is
  reproducible and successful provider work is not duplicated.

## Data Flow

1. Format each complete document deterministically.
2. Write the unmodified request into bounded JSONL shards.
3. Submit or resume only missing provider shards.
4. Download every terminal result shard.
5. Validate identities, dimensions, and finite values while collecting row
   errors.
6. If any row failed, return the structured GSE/error failure and leave the
   prior artifact untouched. Otherwise assemble and publish the complete matrix.

## Testing

- Replace the truncation test with a long multibyte document test proving exact
  text preservation.
- Add a multi-error result test proving all failing GSEs and provider payloads
  are returned.
- Keep paid/key guards, deterministic sharding, resume, identity, dimension,
  and no-synchronous-embedding tests.
- Run the focused Gemini/builder suite, the full repository suite, and a
  full-corpus no-network request-preparation dry run before committing.

## Deferred

An optional exact token-count audit can be added later as a separately
authorized operation. It will never mutate or truncate canonical input.
