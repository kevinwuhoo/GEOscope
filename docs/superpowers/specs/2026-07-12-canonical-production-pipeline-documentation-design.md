# Canonical Production Pipeline Documentation Design

**Status:** Approved in conversation on 2026-07-12.

## Goal

Make the current documentation name one canonical production pipeline and
remove ambiguity about which embedding belongs in production.

## Canonical production path

The documented production path is:

```text
metadata-only GEO SOFT
  -> canonical series JSON records
  -> Gemini gemini-embedding-2 document embeddings
  -> canonical Gemini matrix artifact
  -> Elasticsearch geo-series index
  -> audited Gemini vector coverage
```

`geo-soft-etl` is the single canonical orchestration command. Production uses
only `gemini_embedding_2_3072_v1`, stored in Elasticsearch as
`embedding_gemini_3072`.

## Development and evaluation models

The BGE, MedCPT, and Qwen artifact builders and Elasticsearch fields remain
available for local development, comparison, regression testing, and historical
evaluation. They are not production dependencies and incomplete coverage for
those fields is not a production failure.

## Documentation changes

Update the README and current-state wiki pipeline/runbook pages to document:

- the canonical command and required environment files;
- canonical record, artifact, temporary resume-state, and report locations;
- paid Batch API authorization and concurrency flags;
- the Gemini-only production invariant;
- how to validate the artifact and Elasticsearch coverage;
- how development-only embeddings can be built or loaded explicitly;
- the completed 288,904-row production state.

Historical design and implementation-plan documents remain unchanged unless a
current-state claim would otherwise mislead operators.

## Verification

Run documentation tests, the full offline test suite, whitespace checks, and a
targeted scan for conflicting current-state claims that describe a non-Gemini
model as production-default.
