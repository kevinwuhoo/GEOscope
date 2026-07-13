# Hackathon README Rewrite Design

## Purpose

Replace the current developer/operator runbook with a concise, judge-facing
account of what GEOscope accomplished during the hackathon. The README should
make the project goal, engineering effort, measured scale, search methods, and
live deliverables understandable without requiring setup instructions.

## Audience and tone

The primary audience is hackathon judges; genomics researchers and engineers
are secondary audiences. Lead with the outcome and live demo, use concrete
evidence instead of broad AI claims, and distinguish completed work from
experiments and future scope.

## Structure

1. **Hero** — GEOscope name, one-sentence pitch, and a prominent verified link
   to `https://geoscope.kevinformatics.com`.
2. **Goal** — improve conceptual and cross-vocabulary discovery in NCBI GEO
   through hybrid search over sample metadata aggregated into series-level GSE
   documents.
3. **Overview** — explain the complete path from metadata-only GEO SOFT through
   Prefect, canonical records, normalization, embeddings, Elasticsearch, and
   the website/MCP consumers.
4. **What we accomplished** — foreground the completed 288,904-record corpus,
   complete Gemini vector coverage, fail-closed/resumable pipeline, hybrid
   retrieval and facets, public website, and three-tool MCP service.
5. **Methods** — describe acquisition and canonicalization, deterministic
   normalization, ontology-aware controlled values, embedding construction,
   BM25 plus dense retrieval fused with RRF, the unified NCBI candidate merge
   and Sonnet 5 reranking stage, and shared delivery surfaces.
6. **Experiments** — summarize metadata-source comparisons, local and hosted
   embedding-model work, datastore evaluation, ontology-normalization findings,
   and structured metadata extraction.
7. **Current scope** — state that GEOscope indexes GSE-level metadata rather
   than expression matrices or individual GSM documents, and that production
   filters currently cover organism, sex, and assay while more complex
   ontology mapping remains experimental.
8. **Documentation** — link to the wiki pages that contain detailed methods,
   architecture, experiments, and the build log.

## Structured-extraction experiment

Present structured extraction as a real experiment rather than production
behavior. The project built evidence-backed structured-output prototypes for
extracting biological condition, biospecimen, intervention, demographic,
assay, geography, technology, and study-design claims from messy metadata.
It evaluated multiple OpenAI profiles on a selected pilot and then completed a
10,000-GSE Gemini Flash-Lite Batch run: all 15 jobs succeeded, with 9,439
validated outputs and 561 recorded failures.

The structured-extraction experiments cost $121.61 in total: $47.52 for the
OpenAI model pilot and $74.10 for the 10,000-record Gemini run, calculated from
recorded token usage at the frozen experiment rates. The Gemini request
manifest's pre-run estimate was $57.66, with a conservative maximum of $110.10.
Extrapolating the measured Gemini run to all 288,904 public records gives an
estimated full-corpus cost of approximately $2,141; the corresponding
conservative ceiling remains approximately $3,181. The README should describe
that as too expensive for the hackathon's full-corpus production path, which is
why deterministic normalization plus embeddings remained the deployed
approach. Do not imply that the calculated costs are provider invoices or that
extracted claims are present in the production Elasticsearch index. Embedding
generation and reranking costs are outside this structured-extraction total.

## Unified retrieval and LLM reranking

Show the soon-to-be-merged reranking path as part of the primary workflow with
solid lines. Exact GSE accessions take the deterministic direct lookup path and
bypass semantic ranking. Natural-language queries retrieve a deeper candidate
set from Elasticsearch and NCBI GEO concurrently, merge and deduplicate by GSE
accession while preserving provenance, and send the union to Sonnet 5 for final
reranking. The target defaults are 40 Elasticsearch candidates, 20 NCBI
candidates, and 10 final results.

This logic belongs in the shared MCP/search service so the website and MCP
clients receive the same ordering. Elasticsearch remains required; optional
NCBI or reranker failures fall back to deterministic Elasticsearch ordering.
Describe Sonnet 5 as the target model for the branch being merged, without
claiming that the currently deployed public site already uses it.

## Content boundaries

- Do not include dependency installation or service setup; those belong in a
  later `DEVELOPMENT.md`. Preserve only the compact canonical command handoff
  required by the repository's primary-path documentation contract.
- Do not include the retrospective about softening the original single-cell
  keyword thesis.
- Do not claim comprehensive ontology mapping. Organism and sex are grounded
  to controlled ontology identifiers; assay uses controlled category/detail
  labels, while tissue and other heavy-tailed fields remain experimental.
- Do not describe the website as a separate search implementation. Search
  behavior lives in the shared Elasticsearch/MCP service layer used by both
  the website and MCP server.
- Do not draw the reranking path with dashed or future-state edges; it is being
  merged before this README is used. Keep the deployment wording precise.
- Preserve links to implementation evidence and deeper wiki documentation.

## Verification

Before completion, check every quantitative claim against the repository wiki,
local artifact reports, and current deployment health. Review the final diff to
ensure no developer runbook remains and no unrelated dirty files are modified.
