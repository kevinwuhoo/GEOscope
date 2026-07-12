# Gemini Full-Input Error Propagation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for implementation and superpowers:verification-before-completion before committing the completed change.

**Goal:** Send each complete canonical metadata document to Gemini without preliminary truncation and return every provider row error, keyed by GSE, without publishing a partial artifact.

**Architecture:** Request preparation continues to format deterministic JSONL and enforce only the existing 1,000-request and 100 MiB transport-shard limits. Result assembly validates every response identity, collects all provider row failures, and raises one structured exception before the artifact writer can publish. The existing byte-derived token/cost figure remains an informational estimate and never changes input.

**Tech Stack:** Python 3.11+, pytest, NumPy, google-genai batch/file API

## Global Constraints

- Preserve the complete formatted title and `embed_text`, including multibyte Unicode.
- Do not add a synchronous embedding or token-counting call.
- Keep the paid-operation guard, resumable batch state, shard limits, and atomic artifact publication.
- Keep `truncation_count` in the public result/metadata schema, but report zero.
- Preserve request, state, and provider result files on failure.

---

### Task 1: Specify full-input and aggregated-error behavior

**Files:**
- Modify: `tests/test_embedding_gemini.py`

- [ ] Replace `test_request_preflight_uses_token_safe_utf8_byte_bound` with a long multibyte-input test. Construct a record whose `embed_text` exceeds 8,000 UTF-8 bytes, prepare requests, decode the JSONL body, and assert the provider text equals the complete deterministic wrapper exactly and `truncation_count == 0`.
- [ ] Add a result-assembly test with two different error rows. Assert one structured exception exposes both entries in response order as exact `{"gse": ..., "error": ...}` payloads and that no artifact is written.
- [ ] Run the focused test module and confirm the new tests fail for the intended reasons:

```bash
uv run pytest -q tests/test_embedding_gemini.py
```

Expected red state: the long document is shortened by the existing byte cap, and the current implementation raises on only the first provider error without the new structured exception.

---

### Task 2: Preserve complete request text

**Files:**
- Modify: `src/geo_index/embedding_gemini.py`
- Test: `tests/test_embedding_gemini.py`

- [ ] Remove `SAFE_INPUT_UTF8_BYTES` and change `_wrapped_document` to return the complete formatted string rather than a `(text, truncated)` tuple.
- [ ] Keep `_request_line` byte accounting for the informational estimate and JSONL shard sizing, but return `truncated=False` (or simplify its internal shape while preserving public `truncation_count == 0`).
- [ ] Remove cap-specific usage metadata such as `safe_input_utf8_bytes`; retain `estimated_tokens`, request counts, shard counts, and bytes.
- [ ] Run the full-input test alone and confirm it passes:

```bash
uv run pytest -q tests/test_embedding_gemini.py -k "full_input"
```

---

### Task 3: Aggregate provider row failures

**Files:**
- Modify: `src/geo_index/embedding_gemini.py`
- Test: `tests/test_embedding_gemini.py`

- [ ] Add an immutable failure representation and a public `GeminiBatchRowError` carrying all failures. Its message must identify every failed GSE; its structured `failures` field must retain each provider payload unchanged.
- [ ] In `_assemble_results`, validate every row GSE against the expected set, reject duplicates, add both success and error identities to `seen`, collect error rows, and continue scanning.
- [ ] After scanning, report missing identities if present; otherwise raise `GeminiBatchRowError` if failures were collected. Only construct and return the matrix when all rows succeeded.
- [ ] Run the error tests and then the complete Gemini module:

```bash
uv run pytest -q tests/test_embedding_gemini.py -k "row_error or response_identity"
uv run pytest -q tests/test_embedding_gemini.py
```

- [ ] Commit the tested behavior:

```bash
git add src/geo_index/embedding_gemini.py tests/test_embedding_gemini.py
git commit -m "fix: send full metadata to Gemini"
```

---

### Task 4: Update operator documentation and corpus evidence

**Files:**
- Modify: `README.md`
- Modify: `wiki/42-Build-Log.md`
- Modify: `docs/superpowers/plans/2026-07-11-canonical-embedding-artifacts.md`

- [ ] Replace references to the 8,000-byte cap with the full-input/provider-error policy. Explain that the 1,000-request and 100 MiB limits are transport sharding, not document truncation.
- [ ] State that exact `count_tokens` is intentionally not used in the default corpus build because it is a separate synchronous provider request per document. Describe the byte-derived estimate as informational only.
- [ ] Run the full-corpus, no-network request-preparation dry run and record its exact request count, shard count, total bytes, maximum shard bytes, estimated tokens/cost, truncation count, elapsed time, and peak RSS in `wiki/42-Build-Log.md`:

```bash
env -u GEMINI_API_KEY /usr/bin/time -l uv run python -m geo_index.build_embedding_artifact \
  --model-key gemini_embedding_2_3072_v1
```

Expected terminal result: request preparation completes and the command then raises `GeminiAuthorizationError` because `--allow-paid-gemini` was deliberately omitted.
- [ ] Confirm the dry run reports all discovered records, `truncation_count=0`, and no provider submission.

---

### Task 5: Verify and commit the documentation/evidence

**Files:**
- Test: `tests/test_embedding_gemini.py`
- Test: `tests/test_embedding_builder.py`
- Test: full `tests/`

- [ ] Run the focused integration suite:

```bash
uv run pytest -q tests/test_embedding_gemini.py tests/test_embedding_builder.py
```

- [ ] Run the complete repository suite:

```bash
uv run pytest -q
```

- [ ] Inspect the diff and confirm no real Gemini job was submitted, no existing embedding artifact was replaced, and no generated request/result files were staged.
- [ ] Commit the documentation and fresh evidence:

```bash
git add README.md wiki/42-Build-Log.md docs/superpowers/plans/2026-07-11-canonical-embedding-artifacts.md
git commit -m "docs: document full-input Gemini batches"
```

- [ ] Report both commits, exact focused/full results, fresh dry-run metrics, and any deviations.
