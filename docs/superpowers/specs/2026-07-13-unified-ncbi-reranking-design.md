# Unified NCBI Fallback and LLM Reranking Design

**Date:** 2026-07-13
**Status:** Approved for implementation planning

## Summary

GEOscope will move search-quality behavior into one shared service beneath the
MCP and marketing transports. The service will recognize exact GSE accessions,
retrieve a deeper Elasticsearch candidate set, query NCBI GEO concurrently,
merge and deduplicate both sources, rerank the union with `gpt-5.6-luna`, and
return ten results by default.

NCBI-only records are first-class candidates. A record that is absent from the
local Elasticsearch corpus may appear in the final ranked results with partial
metadata and explicit NCBI provenance. This is required for the NCBI request to
act as a coverage backstop rather than only a comparison display.

Natural-language query understanding and structured query rewriting are not in
this stage. They will be considered only if evaluation shows that reranking
cannot enforce important intent or that the unmodified NCBI query has
insufficient recall.

GPT-5.6 Luna is the fixed model for this evaluation stage.
Sonnet 5 migration is deferred until after the Luna baseline is recorded and
reviewed; it will be a separate model migration so the baseline and follow-up
measurements are not mixed.

## Goals

- Make exact queries such as `GSE310900` deterministic and correct.
- Improve nuanced relevance, including honoring explicit constraints such as
  `mouse`, without introducing a query-planning call yet.
- Recover relevant or newly published GSE records that are missing from the
  Elasticsearch corpus.
- Return ten results by default from both MCP and the marketing demo.
- Ensure MCP, the marketing site, and future consumers use the same retrieval
  and ranking behavior.
- Fail open to useful Elasticsearch results when NCBI or OpenAI is unavailable.
- Produce enough provenance and timing data to decide whether a later
  query-understanding stage is justified.

## Non-goals

- Do not add LLM query rewriting, ontology extraction, or automatic filter
  generation in this stage.
- Do not synchronously ingest full SOFT metadata for NCBI-only candidates.
- Do not add NCBI-only records to Elasticsearch during a user request.
- Do not change offline canonical ingestion or embedding generation.
- Do not make the marketing API an independent search implementation.
- Do not include generation or summarization of the result set.

## Architectural boundary

Search orchestration will live in the shared service used by both MCP and the
marketing API. Elasticsearch remains responsible for lexical, dense, hybrid,
and facet retrieval. Separate components will own NCBI candidate retrieval and
OpenAI reranking. The shared service will own their concurrency, union,
deduplication, filtering, fallbacks, and output provenance.

The marketing API will no longer perform its own second NCBI search after the
shared search completes. The shared execution result will contain both the
final ranked records and the native NCBI candidate list so the marketing page
can retain its comparison column without making duplicate E-utilities calls.
The MCP response will expose the final unified ranking and per-result source,
while transport-specific presentation remains outside the shared layer.

The durable repository rule is recorded in `AGENTS.md`: search correctness and
relevance changes belong in the shared MCP/Elasticsearch layer, not only in the
marketing site.

## Components

### Exact-accession router

Normalize surrounding whitespace and casing, then recognize the full-string
pattern `^GSE[1-9][0-9]*$`.

- Perform a direct Elasticsearch document lookup by GSE ID. Do not create a
  query embedding and do not invoke the reranker.
- If the local document exists and satisfies all requested structured filters,
  return it as the sole result.
- If it is absent locally, perform an accession-qualified NCBI lookup and adapt
  the returned ESummary record into an NCBI-only candidate.
- If neither source has the accession, return no results.
- If active filters contradict the exact record, return no results rather than
  silently violating the filter contract.
- Build candidate-scoped facet buckets from the zero-or-one returned record so
  the existing four-facet output contract remains valid without a broad search.

### Elasticsearch candidate source

For a natural-language query, run the existing retrieval mode and structured
filters but request a deeper pool for reranking.

- The default user-visible result count becomes 10.
- For the default request, retrieve 40 Elasticsearch candidates.
- For explicit limits, target four times the requested limit, with a floor of
  40 and a hard cap of 100.
- Hydrate compact reranking fields: GSE, title, summary snippet, study type,
  organism IDs, assay categories, assay labels, and sample count.
- Keep existing Elasticsearch candidate-pool facet computation. External NCBI
  records do not alter facet counts because ESummary metadata is incomplete;
  facet provenance must continue to identify the local candidate-pool scope.

### NCBI candidate source

Run a live E-utilities search for the same user query, restricted to GEO Series,
concurrently with Elasticsearch retrieval.

- Request up to 20 native candidates for natural-language queries.
- Preserve native order and total count for the marketing comparison.
- Fetch ESummary metadata required for candidate display and reranking.
- Normalize taxon and study-type values through existing deterministic
  normalizers when possible.
- Mark metadata that ESummary cannot establish as `unavailable`; do not claim
  it is absent from the underlying study.
- For structured-filter requests, admit an NCBI-only candidate only when its
  available metadata proves that it satisfies every active filter. For example,
  a candidate without sex metadata cannot satisfy an active sex filter.
- Never accept arbitrary NCBI entry types; candidates must have a valid GSE
  accession and Series entry type.

### Candidate merger

Represent every candidate with a shared internal model containing normalized
display metadata, source provenance, and optional source ranks and scores.

- Deduplicate strictly by normalized GSE accession.
- When both sources return a GSE, prefer complete Elasticsearch metadata, fill
  only missing safe display fields from NCBI, and set `source` to `both`.
- Use `source: elasticsearch` for local-only candidates and `source: ncbi` for
  external-only candidates.
- Preserve Elasticsearch retrieval score and original rank as diagnostics; do
  not compare that score numerically with NCBI rank.
- Bound every text and array field before constructing an MCP output model.

### GPT-5.6 Luna reranker

Use the OpenAI Responses API with model `gpt-5.6-luna`, low reasoning effort,
and Structured Outputs. Luna is selected for a latency-sensitive, high-volume
classification/ranking call.

The request contains:

- the user's original query;
- the requested result count;
- each candidate's GSE, title, bounded summary snippet, study type, organism,
  assay metadata, sample count, and source;
- instructions to treat explicit organism, assay, tissue, condition,
  intervention, and experimental-context requirements as important relevance
  evidence;
- instructions to judge study relevance rather than lexical overlap and never
  invent, remove, or modify candidate identifiers.

The schema returns one entry per input candidate:

```json
{
  "rankings": [
    {
      "gse": "GSE11803",
      "relevance_score": 97
    }
  ]
}
```

Application validation must require the returned GSE set to match the input set
exactly, with no duplicates, unknown IDs, or missing IDs. Scores are bounded
integers from 0 through 100. Final order is descending relevance score, then
Elasticsearch rank, then NCBI rank, then GSE accession for deterministic ties.
Return the first requested number of candidates, ten by default.

The final result contract retains `rank` as the displayed rank and uses `score`
for the reranker relevance score when reranking succeeds. Add `source`,
`retrieval_score`, and `original_rank` as bounded provenance fields. When the
reranker is bypassed or unavailable, `score` retains the source retrieval score
where one exists and the provenance indicates that no reranking occurred.

## Request flow

### Exact GSE request

1. Validate and normalize the request.
2. Recognize the exact accession.
3. Look up the local document directly.
4. If absent locally, query NCBI by accession.
5. Enforce active filters against the resolved record.
6. Return zero or one result without embedding or reranking calls.

### Natural-language request

1. Validate the query, mode, filters, and limit.
2. Start Elasticsearch and NCBI candidate retrieval concurrently.
3. If Elasticsearch succeeds, retain its facets and hydrated candidates.
4. Merge, normalize, filter, and deduplicate all available candidates.
5. Invoke the Luna reranker once on the candidate union.
6. Validate the structured response and select the top requested results.
7. Return unified results, local facet data, native comparison data for the
   marketing adapter, and retrieval/reranking provenance.

## Failure handling and latency

Search must remain useful when optional external dependencies fail.

- Elasticsearch failure remains a request failure because it owns the primary
  corpus and facets.
- NCBI timeout, rate limit, malformed response, or transient failure records an
  error in provenance and continues with Elasticsearch candidates.
- OpenAI timeout, refusal, malformed structured output, missing candidate,
  duplicate candidate, or unknown candidate fails open to deterministic source
  ordering. Do not return a partially trusted reranker order.
- Fallback source order is all Elasticsearch candidates in their original
  hybrid order followed by NCBI-only candidates in native order. Duplicates
  remain merged in their Elasticsearch position.
- An NCBI-only candidate set cannot replace a failed Elasticsearch request in
  this stage because the current response requires local facet semantics.
- Bound NCBI and OpenAI calls with configurable timeouts. Start with a 5-second
  NCBI timeout and an 8-second reranker timeout, then tune from measured
  production latency.
- Use one reranker request and no semantic repair retry. Provider transport may
  retry a single transient connection failure only when it remains within the
  configured timeout.
- Use low reasoning effort and compact, bounded candidate snippets to control
  latency and cost.

## Configuration and lifecycle

Add explicit production configuration for:

- `OPENAI_API_KEY`;
- `GEO_RERANK_ENABLED`, enabled in production after deployment validation;
- `GEO_RERANK_MODEL`, defaulting to `gpt-5.6-luna`;
- `GEO_RERANK_REASONING_EFFORT`, defaulting to `low`;
- `GEO_RERANK_CANDIDATE_LIMIT`, defaulting to `40`;
- `GEO_RERANK_TIMEOUT_SECONDS`, defaulting to `8`;
- `GEO_NCBI_TIMEOUT_SECONDS`, defaulting to `5`.

The shared service owns and closes its Elasticsearch, NCBI HTTP, and OpenAI
clients. Startup validation must reject unsupported model or timeout settings.
When reranking is enabled, a missing OpenAI key is a readiness/configuration
error rather than a silent permanent fallback. Test factories may inject fake
candidate sources and rerankers without credentials or network calls.

## Response and provenance

Every final result identifies whether it came from Elasticsearch, NCBI, or both.
Search-level provenance records:

- retrieval version and active embedding model;
- whether exact-accession routing was used;
- Elasticsearch and NCBI candidate counts;
- deduplicated candidate count;
- whether reranking was attempted and applied;
- reranker model and reasoning effort;
- component latency in milliseconds;
- bounded failure category for a degraded NCBI or reranker path.

Do not expose provider exception text, credentials, prompts, or internal stack
traces. Logs may correlate a request using an opaque request ID but must not log
API keys or full provider response objects.

## Marketing behavior

The marketing demo uses the same final ranked results as MCP and requests ten by
default. Its right-hand native GEO column uses the native candidate list from
the same shared execution; it must not issue another NCBI request. Membership
badges are derived from the shared native candidate/accession data rather than a
separate membership query. Because only the top 20 native candidates are
fetched, the UI must describe absence as "not in the displayed NCBI top 20" and
must not claim that a record is absent from the complete native result set. The
UI identifies NCBI-only results in the final GEOscope column so partial metadata
is understandable.

## Testing

### Unit tests

- Exact accession normalization and routing bypass embedding and reranking.
- `GSE310900` returns the direct local document when indexed.
- An exact accession missing locally uses an NCBI-only record.
- An exact record that contradicts active filters is excluded.
- Elasticsearch and NCBI candidates merge deterministically by GSE.
- A duplicate candidate prefers local metadata and has source `both`.
- NCBI-only metadata uses `unavailable` rather than false absence claims.
- NCBI-only candidates must prove every requested structured filter.
- Reranker input is bounded and includes all candidate IDs exactly once.
- Valid structured output produces deterministic final ranks and ten default
  results.
- Missing, duplicate, or invented reranker IDs trigger source-order fallback.
- NCBI timeout still returns locally ranked results.
- OpenAI timeout or refusal still returns deterministic results.
- Marketing and MCP adapters consume the same shared execution result and the
  marketing adapter performs no second NCBI call.

### Integration tests

- Fake Elasticsearch, NCBI, and OpenAI clients verify concurrent retrieval,
  union, deduplication, and a single reranker call.
- FastAPI and in-memory MCP tests assert matching result order and provenance.
- A provider-gated smoke test verifies the Responses API Structured Output
  schema with `gpt-5.6-luna`; it is skipped without explicit credentials.
- Existing Elasticsearch facet and filter tests remain unchanged except for
  the new provenance fields and default result count.

### Retrieval evaluation

Create a versioned JSONL evaluation set containing the current marketing
examples, exact-accession cases, existing retrieval-evaluation queries, and
adversarial constraint cases. At minimum include:

- `GSE310900`;
- `mouse skeletal muscle gene expression after endurance exercise in insulin
  resistance`;
- human-versus-mouse constraint pairs;
- relevant records known to be absent from the local snapshot;
- queries where the original hybrid order is already strong;
- NCBI-zero-result queries.

For each query, record expected accessions or graded judgments and explicit
constraints. Compare the existing hybrid baseline with unified reranking using
Recall@40, nDCG@10, MRR, explicit-constraint violation rate, NCBI-only recovery,
p50/p95 latency, reranker fallback rate, and estimated OpenAI cost per query.

## Success criteria

- Exact indexed GSE queries return the requested record at rank one without an
  embedding or reranker call.
- Exact GSE records missing locally but present in NCBI are returned with source
  `ncbi`.
- The mouse endurance-exercise query ranks mouse studies ahead of human studies
  when otherwise relevant.
- nDCG@10 and explicit-constraint violation rate improve over the current hybrid
  baseline without a material Recall@40 regression.
- Queries with already-good rankings do not suffer a material nDCG@10 decline.
- NCBI or OpenAI outages still yield deterministic Elasticsearch results.
- MCP and marketing requests produce the same final top-ten ordering.
- Measured p95 latency and per-query cost are recorded before production enablement.

## References

- [GPT-5.6 Luna model](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)

## Staged follow-up decision

After the evaluation set is run, classify remaining failures:

- If the correct record is absent from both candidate sources, improve candidate
  generation or NCBI query construction.
- If the correct record is present but the reranker violates clear intent,
  revise the reranking prompt or model configuration.
- If unmodified natural language yields poor NCBI recall or explicit constraints
  remain unreliable, design the deferred query-understanding layer that emits a
  validated semantic query, structured filters, and NCBI fielded query.
- If reranking meets the success criteria, do not add query understanding yet.
