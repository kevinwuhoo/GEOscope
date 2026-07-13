# Claude Sonnet 5 Reranker Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the shared GPT-5.6 Luna reranker with an Anthropic-only Claude Sonnet 5 reranker using low effort and disabled thinking, verify it with real queries, and deploy and verify the production service.

**Architecture:** Preserve `McpSearchService` and its generic reranker protocol, candidate union, validation, fallback, lifecycle, and transport parity. Replace only the provider adapter and provider-specific configuration, then rename the evaluator and rollout surfaces from Luna/OpenAI to Sonnet/Anthropic. Use Anthropic's static Structured Output schema and enforce the dynamic candidate set in application validation so the provider grammar remains cacheable.

**Tech Stack:** Python 3.11+, `anthropic>=0.115,<1`, Pydantic v2, FastAPI/MCP, Elasticsearch, React/Zod, pytest, Vitest, DigitalOcean App Platform.

## Global Constraints

- The only reranking model is `claude-sonnet-5`.
- Every rerank request uses effort `low` and `thinking: {"type": "disabled"}`.
- Search correctness remains in the shared MCP/Elasticsearch layer.
- Exact GSE queries bypass embeddings and reranking.
- Natural search retrieves at most 100 Elasticsearch plus 100 NCBI candidates and reranks the complete filtered, deduplicated union before slicing.
- Public search defaults to 10 and callers may request 1 through 50.
- A provider failure must return deterministic source ordering without exposing provider text or secrets.
- Default tests make no external provider calls; paid tests require `GEO_TEST_ANTHROPIC=1`.
- Do not keep an OpenAI runtime fallback or caller-selectable provider.
- Do not enable sampling parameters, adaptive/manual thinking, tools, citations, or message prefilling.
- Preserve current-main MCP install tabs and all unrelated user work.

---

### Task 1: Migrate configuration, dependency, and provenance contracts

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/geo_index/mcp_settings.py`
- Modify: `tests/test_mcp_settings.py`

**Interfaces:**
- Produces: `SearchQualitySettings.anthropic_api_key: str | None`
- Produces: fixed settings `rerank_model="claude-sonnet-5"`, `reasoning_effort="low"`, `thinking="disabled"`

- [ ] **Step 1: Write failing settings tests**

Add tests that pin the approved environment and redaction contract:

```python
def test_enabled_reranker_requires_anthropic_key() -> None:
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        SearchQualitySettings.from_env({"GEO_RERANK_ENABLED": "true"})


def test_enabled_sonnet_settings_are_fixed_and_secret_is_redacted() -> None:
    quality = SearchQualitySettings.from_env(
        {
            "GEO_RERANK_ENABLED": "true",
            "ANTHROPIC_API_KEY": " secret ",
            "GEO_RERANK_MODEL": "claude-sonnet-5",
            "GEO_RERANK_EFFORT": "low",
            "GEO_RERANK_THINKING": "disabled",
        }
    )
    assert quality.anthropic_api_key == "secret"
    assert quality.rerank_model == "claude-sonnet-5"
    assert quality.reasoning_effort == "low"
    assert quality.thinking == "disabled"
    assert "secret" not in repr(quality)
```

Add parameterized rejection tests for every unapproved model, effort, and
thinking value.

- [ ] **Step 2: Run the focused tests and confirm the old OpenAI contract fails**

Run:

```bash
.venv/bin/pytest -q tests/test_mcp_settings.py
```

Expected: failures mention missing `anthropic_api_key`/`thinking`, the old
`OPENAI_API_KEY`, and the old Luna model.

- [ ] **Step 3: Implement the settings and provenance contract**

Change the settings dataclass and loader to this shape:

```python
@dataclass(frozen=True)
class SearchQualitySettings:
    anthropic_api_key: str | None = field(default=None, repr=False)
    rerank_enabled: bool = False
    rerank_model: str = "claude-sonnet-5"
    reasoning_effort: str = "low"
    thinking: str = "disabled"
    candidate_limit: int = 40
    rerank_timeout_seconds: float = 8.0
    ncbi_timeout_seconds: float = 5.0
```

`from_env` must read `ANTHROPIC_API_KEY`, `GEO_RERANK_MODEL`,
`GEO_RERANK_EFFORT`, and `GEO_RERANK_THINKING`, strip them, require the key only
when enabled, and accept only the fixed approved values.

- [ ] **Step 4: Replace the runtime dependency and refresh the lock**

Replace `"openai>=2,<3"` with `"anthropic>=0.115,<1"` in `pyproject.toml` and
run:

```bash
env UV_CACHE_DIR=/private/tmp/geo-metadata-index-uv-cache uv lock
env UV_CACHE_DIR=/private/tmp/geo-metadata-index-uv-cache uv sync
```

Expected: `uv.lock` contains Anthropic 0.115 or newer and the environment can
import `anthropic`; no runtime `openai` package is required by the project.

- [ ] **Step 5: Run focused tests and commit**

Run the commands from Step 2 plus:

```bash
.venv/bin/python -c 'import anthropic; print(anthropic.__version__)'
git diff --check
```

Expected: all focused tests pass, an Anthropic SDK version is printed, and the
diff check is clean.

Commit:

```bash
git add pyproject.toml uv.lock src/geo_index/mcp_settings.py tests/test_mcp_settings.py
git commit -m "refactor: define Sonnet reranker contracts"
```

---

### Task 2: Replace the OpenAI adapter with Anthropic Messages

**Files:**
- Modify: `src/geo_index/reranker.py`
- Modify: `tests/test_reranker.py`

**Interfaces:**
- Consumes: approved `SearchQualitySettings` values from Task 1
- Produces: `AnthropicReranker(api_key, model, reasoning_effort, thinking, timeout_seconds, client=None)`
- Preserves: `RerankResult`, `RerankUsage`, `RerankRefusalError`, `InvalidRerankOutputError`, `rank_candidates`

- [ ] **Step 1: Replace fake Responses tests with failing Messages request tests**

Use a fake client whose `messages.create(**kwargs)` records its request and
returns a message-shaped object. Pin the complete request contract:

```python
assert request["model"] == "claude-sonnet-5"
assert request["thinking"] == {"type": "disabled"}
assert request["output_config"]["effort"] == "low"
assert request["output_config"]["format"] == {
    "type": "json_schema",
    "schema": STATIC_RANKING_SCHEMA,
}
assert "temperature" not in request
assert "top_p" not in request
assert "top_k" not in request
assert request["max_tokens"] <= 8_000
```

Assert the JSON user message contains every candidate exactly once and that the
schema contains no query-specific enum or candidate identifier.

- [ ] **Step 2: Add failing response/failure tests**

Cover these concrete message shapes:

```python
valid = SimpleNamespace(
    stop_reason="end_turn",
    content=[SimpleNamespace(type="text", text='{"rankings":[{"gse":"GSE1","relevance_score":91}]}')],
    usage=SimpleNamespace(input_tokens=120, output_tokens=20),
)
refusal = SimpleNamespace(
    stop_reason="refusal",
    content=[SimpleNamespace(type="text", text="refused")],
    usage=SimpleNamespace(input_tokens=12, output_tokens=3),
)
truncated = SimpleNamespace(
    stop_reason="max_tokens",
    content=[SimpleNamespace(type="text", text='{"rankings":[')],
    usage=SimpleNamespace(input_tokens=120, output_tokens=8_000),
)
```

Assert valid output produces scores and usage; refusal and truncation preserve
usage in typed errors; non-text/multiple blocks, malformed JSON, duplicate,
missing, invented, or modified IDs, booleans/floats/out-of-range scores, and
unexpected stop reasons all fail closed. Add a fake that raises
`anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com"))`
and assert the adapter raises built-in `TimeoutError` without provider text.

- [ ] **Step 3: Run the adapter tests and confirm they fail against OpenAI**

Run:

```bash
.venv/bin/pytest -q tests/test_reranker.py
```

Expected: import/request-shape failures reference `OpenAIReranker` and the
OpenAI Responses request.

- [ ] **Step 4: Implement the Anthropic adapter**

Use the official client:

```python
from anthropic import APITimeoutError, Anthropic

class AnthropicReranker:
    def __init__(self, *, api_key: str, model: str, reasoning_effort: str,
                 thinking: str, timeout_seconds: float, client: Any | None = None) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.thinking = thinking
        self._client = client or Anthropic(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=1,
        )
```

Call `messages.create` with the request pinned in Step 1. Use a static schema
with required object properties and no dynamic enum, min/max, or query values.
Extract exactly one text block, validate with `RankingEnvelope`, then validate
the complete candidate identifier set. Detect `refusal` and `max_tokens` before
parsing. Convert `APITimeoutError` to `TimeoutError("reranker request timed out")`
and chain from the SDK exception without returning its text. Keep `close()` and
all deterministic ranking behavior.

- [ ] **Step 5: Prove zero, small, and 200-candidate behavior**

Assert an empty candidate tuple makes no API call. Generate 200 unique
`SearchCandidate` objects, return 200 unique rankings, and assert all are
accepted and the output budget is exactly the bounded formula. Run:

```bash
.venv/bin/pytest -q tests/test_reranker.py
git diff --check
```

Expected: all adapter tests pass and the diff is clean.

- [ ] **Step 6: Commit**

```bash
git add src/geo_index/reranker.py tests/test_reranker.py
git commit -m "feat: rerank GEO candidates with Sonnet 5"
```

---

### Task 3: Wire Sonnet through shared search, evaluation, and live tests

**Files:**
- Modify: `src/geo_index/mcp_models.py`
- Modify: `src/geo_index/mcp_search_service.py`
- Modify: `src/geo_index/search_eval.py`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/api.test.ts`
- Modify: `frontend/src/App.test.tsx`
- Modify: `tests/test_mcp_models.py`
- Modify: `tests/test_mcp_search_service.py`
- Modify: `tests/test_mcp_server.py`
- Modify: `tests/test_marketing_api.py`
- Modify: `tests/test_production_app.py`
- Modify: `tests/test_search_eval.py`
- Modify: `tests/test_reranker_live.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `AnthropicReranker` and Task 1 settings
- Produces: shared provenance model `claude-sonnet-5`, effort `low`, thinking `disabled`
- Produces: evaluation run keys `baseline` and `sonnet`
- Produces: provider marker requiring `GEO_TEST_ANTHROPIC=1`

- [ ] **Step 1: Write failing provenance and shared-service integration tests**

Add `rerank_thinking: Literal["disabled"] | None` to the intended output
contract tests. Require it to agree with `rerank_attempted` in the same way as
model and effort. Update every MCP, marketing, production, evaluator, and
frontend fixture with `"disabled"` for attempted reranks and `None` otherwise.
Assert the frontend accepts only the bounded approved value.

Rename fake reranker model values to `claude-sonnet-5`, add
`thinking="disabled"`, and assert natural searches expose:

```python
assert provenance.rerank_attempted is True
assert provenance.rerank_applied is True
assert provenance.rerank_model == "claude-sonnet-5"
assert provenance.rerank_reasoning_effort == "low"
assert provenance.rerank_thinking == "disabled"
```

Keep explicit tests that exact `GSE310900` never calls the reranker and returns
`rerank_model is None`/`rerank_thinking is None`, and that provider failures
retain deterministic order and bounded usage/degradation.

- [ ] **Step 2: Write failing evaluator migration tests**

Change injected service keys and assertions from `luna` to `sonnet`. Require the
default factory to reject disabled/missing-key/wrong-model/wrong-effort/wrong-
thinking configurations. Assert report configuration equals:

```python
{
    "rerank_enabled": True,
    "model": "claude-sonnet-5",
    "reasoning_effort": "low",
    "thinking": "disabled",
}
```

Preserve candidate/full-pool/final metrics, latency math, atomic output,
close-all behavior, attempted/applied counts, and caller-priced cost tests.

- [ ] **Step 3: Run the focused tests and observe old-provider failures**

Run:

```bash
.venv/bin/pytest -q tests/test_mcp_models.py tests/test_mcp_search_service.py tests/test_mcp_server.py tests/test_marketing_api.py tests/test_production_app.py tests/test_search_eval.py tests/test_reranker_live.py
pnpm --dir frontend test
```

Expected: failures reference the old factory, settings secret, Luna report key,
and missing thinking provenance.

- [ ] **Step 4: Wire the shared service and evaluator**

Change the default reranker factory to construct:

```python
AnthropicReranker(
    api_key=settings.anthropic_api_key,
    model=settings.rerank_model,
    reasoning_effort=settings.reasoning_effort,
    thinking=settings.thinking,
    timeout_seconds=settings.rerank_timeout_seconds,
)
```

Populate `rerank_thinking` from the live reranker on attempted natural searches.
Add the field to `SearchProvenanceOutput`, validate attempted-state consistency,
and add the corresponding nullable literal to the frontend Zod schema.
Rename evaluator factories/report keys/config validation from Luna to Sonnet and
include thinking. Do not rename generic CLI price flags or metrics.

- [ ] **Step 5: Migrate the provider-gated live tests**

Change the marker description to:

```toml
"provider_integration: requires GEO_TEST_ANTHROPIC=1 and live provider credentials",
```

The live tests must skip unless `GEO_TEST_ANTHROPIC=1` and
`ANTHROPIC_API_KEY` are both present. Construct `AnthropicReranker` with the
approved configuration. Preserve the two-candidate strict-schema smoke and the
200-candidate latency/usage benchmark without printing prompts or the key.

- [ ] **Step 6: Run focused and cross-transport tests and commit**

Run:

```bash
.venv/bin/pytest -q tests/test_mcp_models.py tests/test_mcp_search_service.py tests/test_marketing_api.py tests/test_production_app.py tests/test_search_eval.py tests/test_reranker_live.py tests/test_mcp_server.py
pnpm --dir frontend test
git diff --check
```

Expected: Python and frontend tests pass; live provider tests skip without the
explicit flag; the diff is clean.

Commit:

```bash
git add src/geo_index/mcp_models.py src/geo_index/mcp_search_service.py src/geo_index/search_eval.py frontend/src/api.ts frontend/src/api.test.ts frontend/src/App.test.tsx tests/test_mcp_models.py tests/test_mcp_search_service.py tests/test_mcp_server.py tests/test_marketing_api.py tests/test_production_app.py tests/test_search_eval.py tests/test_reranker_live.py pyproject.toml
git commit -m "refactor: expose Sonnet reranking everywhere"
```

---

### Task 4: Update rollout surfaces and verify the real provider locally

**Files:**
- Modify: `.do/app.yaml.tmpl`
- Modify: `deploy/app-platform.env.example`
- Modify: `deploy/geo-mcp.env.example`
- Modify: `docs/deployment/digitalocean.md`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-13-unified-ncbi-reranking-design.md`
- Modify: `tests/test_primary_path_docs.py`
- Modify: `tests/test_mcp_packaging.py`

**Interfaces:**
- Produces: deploy/runtime variables `ANTHROPIC_API_KEY`, `GEO_RERANK_MODEL=claude-sonnet-5`, `GEO_RERANK_EFFORT=low`, `GEO_RERANK_THINKING=disabled`
- Produces: current documentation and commands using `GEO_TEST_ANTHROPIC=1`

- [ ] **Step 1: Write failing deployment/documentation contract tests**

Assert both environment examples and `.do/app.yaml.tmpl` contain Anthropic's
secret and the approved model/effort/thinking values, and contain no
`OPENAI_API_KEY`. Assert current README and deployment runbook name Sonnet 5,
Anthropic Structured Outputs, the Anthropic opt-in flag, and the three required
smoke queries. Add a prominent supersession note to the prior unified reranking
design instead of rewriting its historical Luna design text.

- [ ] **Step 2: Run docs tests and confirm stale Luna/OpenAI failures**

Run:

```bash
.venv/bin/pytest -q tests/test_primary_path_docs.py tests/test_mcp_packaging.py
```

Expected: failures list old OpenAI/Luna configuration and missing Sonnet
thinking settings.

- [ ] **Step 3: Update deployment and current documentation**

Replace the App Platform secret with `ANTHROPIC_API_KEY`; replace reasoning
configuration with `GEO_RERANK_EFFORT` and `GEO_RERANK_THINKING`; update the
`envsubst` allow-list; update provider smoke and evaluator commands; link to
Anthropic's Sonnet 5, effort, Structured Outputs, and SDK documentation. State
that the production source deploy is incomplete until public provenance shows
Sonnet applied. Preserve unrelated hackathon narrative and MCP-install copy.

- [ ] **Step 4: Run complete offline verification**

Run:

```bash
.venv/bin/pytest -q
pnpm --dir frontend test
pnpm --dir frontend build
.venv/bin/python -m compileall -q src tests
env UV_CACHE_DIR=/private/tmp/geo-metadata-index-uv-cache uv lock --check
git diff --check
```

Expected: all offline tests/build/checks pass; only explicit live Elasticsearch,
Postgres, and Anthropic tests skip.

- [ ] **Step 5: Run the live Anthropic provider tests**

Source the ignored root `.env` without printing it, then run:

```bash
set -a
. /Users/kwu/projects/geo-metadata-index/.env
set +a
GEO_TEST_ANTHROPIC=1 .venv/bin/pytest -q tests/test_reranker_live.py -m provider_integration
```

Expected: the small and 200-candidate Sonnet tests pass with nonzero input and
output usage. Record only timings and token counts.

- [ ] **Step 6: Run representative local shared-search queries**

Source the ignored root `.env` and `.env.elasticsearch`, set the approved
runtime values, and run `geo-search-eval` over the versioned corpus with current
caller-supplied Sonnet pricing. Inspect the cases named
`mouse_endurance_insulin`, `human_breast_neoadjuvant`, and
`exact_gse_310900` in the uncommitted JSON report. Require Sonnet attempted and
applied for the two natural-language cases, no organism constraint violation,
and exact GSE returned with no rerank attempt. Delete or leave the report
untracked; never commit it.

- [ ] **Step 7: Commit documentation and rollout changes**

```bash
git add .do/app.yaml.tmpl deploy/app-platform.env.example deploy/geo-mcp.env.example docs/deployment/digitalocean.md README.md docs/superpowers/specs/2026-07-13-unified-ncbi-reranking-design.md tests/test_primary_path_docs.py tests/test_mcp_packaging.py
git commit -m "docs: migrate reranking rollout to Sonnet 5"
```

---

### Task 5: Integrate, deploy, and verify production

**Files:**
- No committed file is created by deployment.
- Never commit: `.do/app.yaml`, `deploy/app-platform.env`, `.env*`, or live evaluation reports.

**Interfaces:**
- Consumes: tested `feature/unified-ncbi-reranking` HEAD and existing DigitalOcean `geoscope` app
- Produces: production `https://geoscope.kevinformatics.com` using Claude Sonnet 5

- [ ] **Step 1: Review and verify the complete feature diff**

Compare current `main` to feature HEAD, run the complete verification from Task
4 again, and scan tracked files for secret-shaped Anthropic values. Require a
clean independent review with no Critical or Important findings before merge.

- [ ] **Step 2: Integrate current main without losing concurrent work**

Fetch current origin, merge current `main` into the feature branch if it
advanced, resolve semantically, and rerun verification. Then merge the feature
branch into the main checkout using a non-destructive merge. Do not reset or
discard unrelated changes.

- [ ] **Step 3: Verify the merged main checkout**

Run the complete Python suite, frontend tests/build, compilation, lock check,
diff check, and a short live Sonnet schema smoke from merged `main`. Expected:
all offline checks pass and the opted-in provider call succeeds.

- [ ] **Step 4: Push main and observe source deployment**

Push `main` to `origin`. Confirm the remote SHA equals local merged HEAD. Poll
`/healthz` and `/readyz` through the deployment window. A push or health 200 is
not sufficient evidence of Sonnet activation.

- [ ] **Step 5: Verify production provider configuration and queries**

Call `/api/demo/search?limit=10` for the mouse and human natural-language
queries and `/api/demo/search?limit=10&q=GSE310900` for exact routing. Require:

- both natural responses have `rerank_attempted=true`,
  `rerank_applied=true`, `rerank_model="claude-sonnet-5"`,
  `rerank_reasoning_effort="low"`, `rerank_thinking="disabled"`, and no
  reranker degradation;
- mouse results satisfy the mouse organism intent ahead of human-only studies;
- human results satisfy the human intent;
- exact results contain `GSE310900`, have `exact_accession=true`, and show no
  rerank attempt/model/thinking value.

If production is healthy but reranking is disabled, inspect the existing App
Platform component through its authenticated control plane and set only the
approved non-secret values while preserving the existing secret. Trigger a new
deployment and repeat the evidence checks.

- [ ] **Step 6: Record the deployment result**

Report the merged/pushed SHA, offline counts, provider-test counts, production
health/readiness, result accessions/source labels, model/effort/thinking,
latency and token usage, and any gated tests not run. Do not record secrets,
raw prompts, or unbounded provider responses.

---

## Completion checklist

- [ ] No runtime OpenAI import, key, or fallback remains in the search path.
- [ ] Every natural rerank request uses Sonnet 5, low effort, disabled thinking, and static Structured Outputs.
- [ ] Every candidate appears exactly once or the service falls back deterministically.
- [ ] Exact GSE lookup still bypasses the reranker.
- [ ] MCP and marketing share the same ranking and admission behavior.
- [ ] Local paid provider tests and representative shared-search queries pass.
- [ ] Complete offline Python/frontend/build/lock/diff verification passes.
- [ ] The migration is committed, integrated into current main, and pushed.
- [ ] Production health and representative provider-backed query evidence pass.
