# Claude Sonnet 5 Reranker Migration Design

## Status and decision

Approved on 2026-07-13.

Replace the shared GPT-5.6 Luna reranker with an Anthropic-only Claude Sonnet 5
reranker. The production request uses model `claude-sonnet-5`, effort `low`,
and `thinking: {"type": "disabled"}`. This is a complete provider migration,
not a dual-provider switch or a marketing-site experiment.

The migration preserves the already-approved search contract: exact GSE
lookups bypass embeddings and reranking; natural-language searches retrieve up
to 100 Elasticsearch and 100 NCBI candidates in parallel; every active filter
is applied to NCBI-only candidates; the deduplicated union of up to 200 records
is reranked once; public searches default to 10 results and accept 1 through
50; and provider failure falls back to deterministic source ordering.

## Goals

- Make Claude Sonnet 5 the only search reranking provider in every transport.
- Use the lowest approved reasoning profile: low effort with thinking disabled.
- Preserve strict, complete candidate-set validation and fail-open behavior.
- Preserve bounded usage, latency, degradation, and source provenance.
- Evaluate the real provider locally with representative queries before
  deployment.
- Commit, merge, push, deploy through the existing DigitalOcean App Platform
  source workflow, and verify the production behavior and result quality.

## Non-goals

- Do not keep an OpenAI runtime fallback or add caller-selectable providers.
- Do not change candidate generation, source union, filters, exact-accession
  routing, final result limits, or facet semantics.
- Do not add the previously deferred query-understanding layer.
- Do not expose provider exception strings, API keys, prompts, or raw responses
  through MCP, HTTP, logs, fixtures, or evaluation reports.
- Do not enable temperature, top-p, top-k, manual thinking budgets, tools,
  citations, or assistant-message prefilling.

## Provider adapter

Keep the existing generic reranker protocol, result type, ranking function, and
safe response-error hierarchy. Replace `OpenAIReranker` with
`AnthropicReranker`, backed by the official synchronous Anthropic Python SDK.
The adapter owns one client, closes it through the existing shared service
lifecycle, uses the configured end-to-end timeout, and allows one SDK retry to
match the current bounded provider policy.

The Messages request is:

- `model="claude-sonnet-5"`;
- `system` containing the current relevance instructions;
- one user message containing compact JSON with the query, requested result
  count, and every bounded candidate exactly once;
- `thinking={"type": "disabled"}`;
- `output_config.effort="low"`;
- `output_config.format.type="json_schema"`;
- a static JSON schema containing a required `rankings` array whose items have
  required string `gse` and integer `relevance_score` fields;
- a bounded `max_tokens` derived from candidate count and capped at 8,000.

The schema is intentionally static. Candidate accessions must not be embedded
as a changing enum because Anthropic compiles and caches structured-output
grammars by schema; a query-specific enum would defeat that cache and add
first-request compilation latency to every search. Constraints unsupported by
Anthropic's raw JSON-schema subset are enforced after parsing instead.

## Response validation and failure behavior

Accept only a normal completion containing one text block with a JSON object
that validates as the strict `RankingEnvelope`. Then require:

- exactly one ranking for every supplied candidate;
- no duplicate, missing, modified, or invented GSE accession;
- an integer score from 0 through 100 for every item.

Treat `stop_reason="refusal"` as a typed refusal. Treat
`stop_reason="max_tokens"`, missing or multiple unexpected output blocks,
invalid JSON, schema violations, and identifier mismatches as typed invalid
output. Normalize the Anthropic SDK timeout to the existing reranker-timeout
category. All completed but unusable responses retain safe input/output usage
counts before deterministic fallback. Transport failures remain categorized
without provider text.

## Configuration and provenance

`SearchQualitySettings` becomes provider-neutral in behavior and
Anthropic-specific in credentials:

- replace the secret field with `anthropic_api_key`, excluded from `repr`;
- require `ANTHROPIC_API_KEY` whenever reranking is enabled;
- fix `GEO_RERANK_MODEL` to `claude-sonnet-5`;
- rename the deployment effort variable to `GEO_RERANK_EFFORT` and require
  `low`;
- add `GEO_RERANK_THINKING=disabled` and reject every other value;
- preserve the candidate and timeout bounds.

Remove `OPENAI_API_KEY` from the search deployment templates and remove the
OpenAI SDK dependency if no runtime import remains. Add the Anthropic SDK as a
bounded project dependency and update the lockfile.

Keep the public `rerank_reasoning_effort` provenance field for compatibility,
with value `low`. Add a bounded optional `rerank_thinking` value so live and
production responses can prove that thinking is disabled. Model provenance
must report `claude-sonnet-5` only when reranking was attempted.

## Evaluation and live verification

Rename Luna-specific evaluation concepts to Sonnet without weakening the
baseline comparison:

- run keys and report labels become `sonnet`;
- the evaluator fails closed unless Anthropic reranking is enabled with a key,
  the approved model/effort/thinking configuration is effective, and at least
  one natural-language case attempts reranking;
- caller-supplied price flags remain generic;
- reports continue to include Recall@40, full-pool judged presence, nDCG@10,
  MRR, constraints, NCBI-only recovery, latency, fallback, usage, and cost.

Provider tests are opt-in with `GEO_TEST_ANTHROPIC=1` and
`ANTHROPIC_API_KEY`. They cover a small strict-schema request and the bounded
200-candidate request. Default tests never make paid calls.

Before deployment, source the ignored repository `.env` and exercise at least:

1. `mouse skeletal muscle gene expression after endurance exercise in insulin resistance`;
2. `human breast cancer transcriptomics before and after neoadjuvant chemotherapy with treatment response data`;
3. exact `GSE310900`, confirming exact routing bypasses Sonnet.

Record result accessions, source, rerank model, attempted/applied state,
degradation categories, latency, and token usage without recording prompts or
secrets. The first two queries must apply Sonnet successfully. The exact query
must return `GSE310900` and show no rerank attempt.

## Deployment

Update the App Platform template, environment examples, README, and DigitalOcean
runbook for the Anthropic credential and approved model configuration. The
generated `.do/app.yaml` and all secret-bearing environment files remain
ignored and uncommitted.

After offline and local live verification:

1. commit the migration on `feature/unified-ncbi-reranking`;
2. integrate the branch into current `main` without losing concurrent work;
3. inspect the existing App Platform component configuration and ensure
   `ANTHROPIC_API_KEY` remains a runtime secret while
   `GEO_RERANK_ENABLED=true`, `GEO_RERANK_MODEL=claude-sonnet-5`,
   `GEO_RERANK_EFFORT=low`, and `GEO_RERANK_THINKING=disabled` are explicit
   runtime variables; update them through the DigitalOcean control plane if
   the current component differs, without reading or printing secret values;
4. push `main` to the configured GitHub origin, triggering App Platform's
   `deploy_on_push` workflow;
5. confirm production health/readiness during rollout;
6. poll representative public search requests until provenance reports
   `claude-sonnet-5`, low effort, thinking disabled, and reranking applied;
7. verify the mouse query prioritizes mouse studies, the human query returns
   appropriate human studies, and exact `GSE310900` still bypasses reranking;
8. inspect bounded runtime logs or public degradation provenance if deployment
   fails, without exposing secret values.

Do not declare deployment complete based only on a successful Git push or
health endpoint. Completion requires provider-backed query evidence from the
production URL.

## Testing

Test-driven implementation must cover:

- settings defaults, enabled validation, secret redaction, and nested loading;
- exact Anthropic Messages request shape, including low effort, disabled
  thinking, absence of sampling parameters, static schema, timeout, and retry;
- zero, small, and 200-candidate requests;
- valid output, refusal, max-token truncation, malformed content, wrong IDs,
  duplicate IDs, score bounds, usage preservation, and timeout normalization;
- shared-service lifecycle, exact bypass, natural reranking, fallback, and
  provenance;
- evaluator configuration, metrics, labels, and cleanup;
- MCP/marketing parity and frontend provenance parsing;
- deployment templates, documentation, dependency lock, and absence of stale
  Luna/OpenAI search-reranker configuration.

Run the complete Python suite, frontend suite, production build, compilation,
lock verification, secret scan, and diff check before commit and again after
integration. Run paid provider tests only under the explicit Anthropic opt-in.

## Official references

- https://platform.claude.com/docs/en/about-claude/models/whats-new-sonnet-5
- https://platform.claude.com/docs/en/build-with-claude/effort
- https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python
