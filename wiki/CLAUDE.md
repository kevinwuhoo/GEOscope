# CLAUDE.md — working in this vault

This is the planning vault for the **GEO Metadata Index** (see [[Home]]). Obsidian conventions: numbered notes (`NN-Title.md`), `[[wikilinks]]` resolve by note name (not path), [[Home]] is the map of content. When you add or edit notes, keep the two rules below.

## Rule 1 — Cite all sources

Every note that makes a **factual or external claim** must end with a `## Sources` section listing the specific URLs behind those claims (markdown links or `label — url` bullets).

- Add the citation **inline where the claim is made** *and* in the note's `## Sources` section.
- Also add any new URL to the master index **[[99-Sources]]** (grouped by topic) so it stays complete.
- Pure navigation/decision notes ([[Home]], [[40-Roadmap]], [[41-Open-Questions]], [[90-Glossary]]) don't need a `## Sources` section — they cite nothing external.
- **Flag provenance honestly:** some existing URLs came from agent web-search results rather than pages opened directly. If you haven't verified a load-bearing link, say so (e.g. "(unverified)") rather than implying it was checked.
- Don't invent citations. If a claim has no source, mark it as an assumption/design choice, not a fact.

## Rule 2 — Always distinguish v1 vs v2

This project is a **v1 spike**, deliberately scoped. When you describe or add functionality, **label its scope** so the boundary stays legible. Use an explicit tag inline — `**(v1)**` / `**(v2+)**` — and put anything deferred under the "Later / v2" section of [[40-Roadmap]].

**v1 (the spike — build this):**
- **Series-level (GSE)** documents only (current fixed snapshot: 222,961), *not* per-sample.
- One Postgres: `pgvector` + **ParadeDB `pg_search`** for BM25 + disjunctive SQL facets — committed.
- One **whole-document embedding** of the frozen current narrative; normalized
  fields are separate facets/filters. Normalized-label injection is a later
  document ablation, not part of the model bake-off.
- Normalize **3 fields** end-to-end: sex, organism, and assay. Tissue is the next experiment.
- Hybrid retrieval; the LLM client owns v1 query expansion. Deterministic
  ontology expansion is v2+. Facets are closed controlled enums.
- **MCP server** exposes retrieval; the **LLM client** does summary/conversation.
- A small **eval set** decides model/mapper choices.

**v2+ (defer — note it, don't build it yet):**
- **Sample-level (GSM)** indexing (~8.6M) — the real scale step *and* the correctness fix for within-sample multi-field filtering (see the [[24-Faceted-Search|series-aggregation caveat]]).
- More normalized fields (tissue, disease, cell type, dev stage, ethnicity).
- Server-side cross-encoder **reranking**; **per-field / multi-vector** embeddings ([[28-Embedding-Granularity]]).
- Incremental refresh cron; human UI; gene-signature search.

When in doubt about scope, ask before promoting a v2 idea into the v1 plan.

## Committed decisions (don't re-litigate without reason)

Postgres-only · `pg_search` for BM25 + disjunctive SQL facets · series-level v1 · one doc embedding · retrieval-in-service / generation-in-LLM (MCP) · **CELLxGENE field→ontology schema** (organism→NCBITaxon, tissue→UBERON, cell type→CL, disease→MONDO, assay→EFO, sex→PATO).

## Gotchas to preserve (don't "simplify" these away)

- **Series-aggregation caveat** — series-level multi-field filters mean "contains these values", not "a sample with all of them". → [[24-Faceted-Search]]
- **GPL organism-cloning** — raw GPL = instrument × species; facet on derived `instrument_model` + `technology`, not raw `platform_id`. Platform ≠ assay. → [[10-GEO-Data-Model]]
- **LLMs don't emit valid ontology IDs** — use the LLM for labels, then *ground* to an ID via lookup. → [[22-Ontology-Normalization]]
- **Normalization ≠ embeddings** — facets need discrete IDs; embeddings give fuzzy recall. Keep both tracks. → [[11-The-Metadata-Problem]]
