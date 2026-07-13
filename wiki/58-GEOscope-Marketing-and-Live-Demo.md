---
title: GEOscope Marketing Page and Live Demo
tags: [geoscope, marketing, frontend, react, fastapi, mcp, demo]
status: implemented
created: 2026-07-12
---

# 58 · GEOscope Marketing Page and Live Demo

← [[Home]] · product context in [[00-Overview]] · backend contract in
[[27-MCP-Interface]] · production data path in
[[57-Canonical-Production-Pipeline]]

## Name and public identity

**GEOscope** is the public name for the GEO Metadata Index prototype. The name
combines the source corpus, [NCBI GEO](https://www.ncbi.nlm.nih.gov/geo/), with
the idea of an instrument that brings difficult-to-see evidence into focus.

The public thesis is:

> **See what GEO search misses.**
>
> GEOscope turns inconsistent genomics metadata into precise,
> ontology-aware discovery.

The name does not change the v1 technical scope. GEOscope indexes **series-level
(GSE) metadata**, not expression matrices, and currently normalizes organism,
sex, and assay. Tissue and the other complex ontology fields remain bounded
experiments or **v2+** work. → [[00-Overview]], [[22-Ontology-Normalization]]

## Audience and page job

The marketing page is ordered for a hackathon judge who needs to understand the
novelty quickly. Genomics researchers are the secondary audience, so the page
uses recognizable GEO accessions, realistic queries, controlled identifiers,
and explicit evidence instead of generic AI claims.

The page has one job: move a visitor from **“literal keyword search misses
relevant studies”** to **“I want to try the live comparison.”**

## Implemented experience **(v1)**

The Vite + React + TypeScript application lives under `frontend/`. Its visual
identity is derived from GEO metadata rather than a generic product template:

- the hero uses an animated **accession scope** that resolves raw phrases into
  assay concepts and ranked GSE accessions;
- the primary proof places native GEO keyword results beside GEOscope results
  for the same query;
- result labels distinguish a checked native-keyword miss from ordinary page
  overlap;
- normalization rows show raw organism, sex, and assay values collapsing into
  controlled values such as `NCBITaxon:9606` and `PATO:0000384`;
- a researcher example separates fuzzy semantic intent from exact filters; and
- the final section explains that the same bounded retrieval operations are
  available to an LLM client through the
  [Model Context Protocol](https://modelcontextprotocol.io/).

The design uses a cool laboratory-paper ground, carbon text, cobalt for
GEOscope retrieval, amber for native GEO, and ontology lime for normalized
evidence. Bricolage Grotesque, Atkinson Hyperlegible, and IBM Plex Mono are
bundled with the frontend rather than loaded from a font CDN.

## Live-demo architecture

The browser does not receive Elasticsearch credentials, MCP access tokens, or
NCBI configuration. `src/geo_index/marketing_api.py` provides a narrow FastAPI
adapter:

```text
React search form
    ↓ GET /api/demo/search
FastAPI browser-safe adapter
    ├─ McpSearchService.search_datasets(...) → GEOscope results + facets
    └─ EutilsGeoComparison                  → native GEO results + membership
```

The adapter calls the same `McpSearchService` used by the FastMCP tools, so the
website and MCP surface share bounded output models and retrieval semantics.
It adds only the native GEO comparison required by the marketing proof.

Endpoints:

- `GET /api/health` — frontend-visible readiness without secrets;
- `GET /api/demo/search?q=<query>&mode=<mode>&limit=<n>` — bounded native-GEO
  and GEOscope comparison; and
- compiled Vite assets plus a history fallback for non-API frontend routes.

If NCBI is temporarily unavailable, the endpoint preserves GEOscope results and
marks only the native comparison unavailable. Invalid queries are rejected with
bounded, field-specific responses, and internal exception details are not sent
to the browser.

## Local development

Configure the Elasticsearch environment described in
[[57-Canonical-Production-Pipeline]], then start FastAPI:

```bash
uv run geoscope-web
```

In a second terminal, install and start the frontend:

```bash
cd frontend
pnpm install
pnpm dev
```

Vite serves the development page and proxies `/api` to FastAPI on
`127.0.0.1:8000`.

For a local production build:

```bash
cd frontend
pnpm build
cd ..
uv run geoscope-web
```

FastAPI detects `frontend/dist` when the process starts and serves the compiled
single-page application.

## Responsive and accessibility contract

The layout is mobile-first down to a 320-pixel CSS viewport. At narrow widths,
the hero becomes a vertical composition, the result comparison changes from
two columns to a labeled sequence, and form controls remain touch sized. The
page also provides:

- semantic landmarks and ordered headings;
- a skip link and visible keyboard focus;
- text and shape labels in addition to source colors;
- a live region for search progress and result counts;
- no required hover-only interaction; and
- reduced-motion behavior that removes the orchestrated scope transitions.

## Verification snapshot

The implementation was merged to `main` in commit `0158390` on 2026-07-12.
At that checkpoint:

- the Python suite reported **396 passed** and **9 integration tests skipped**;
- the React behavior test passed;
- the TypeScript + Vite production build passed; and
- browser QA covered the default desktop viewport and a 390 × 844 mobile
  viewport with no horizontal overflow.

These are point-in-time build observations, not permanent quality guarantees;
rerun the suites after later changes.

## Sources

- NCBI GEO — https://www.ncbi.nlm.nih.gov/geo/
- Model Context Protocol — https://modelcontextprotocol.io/
