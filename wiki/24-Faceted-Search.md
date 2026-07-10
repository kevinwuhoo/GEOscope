---
title: Faceted Search
tags: [facets, search, ontology]
---

# 24 · Faceted Search

← [[Home]] · depends on [[22-Ontology-Normalization]] · served by [[26-Datastore-Postgres]]

> **v1 implementation:** [[45-Normalized-Filters-and-Facets-Plan]] starts with
> the already-populated `organism_ids`, `sex_ids`, `assay_categories`, and
> `assay_labels` arrays. These are flat discovery facets; ontology hierarchy is
> deferred until tissue or an EFO-grounded assay field makes it useful.

## What a facet is here

For a given query result set, each facet returns `(value, count)` buckets — e.g. `Organism: Homo sapiens (1,204) · Mus musculus (612)`. These drive the drill-down UI/agent filters.

> ⚠️ **Series-aggregation caveat (correctness gotcha).** Because v1 indexes at the **series** level, per-sample fields are rolled up to the series as *sets of distinct values* (a series gets `tissue ∈ {liver, spleen}`, `sex ∈ {M, F}`). That means a multi-field series facet query is **"contains these values", not "a sample with all these values"**:
> - `sex=female AND tissue=liver` matches any series that has *some* female sample **and** *some* liver sample — **not necessarily the same sample**. A study with male-liver + female-spleen samples matches spuriously.
> - You also **cannot** answer sample-scoped questions ("find the *samples* that are female-liver-scRNA") at series granularity at all.
>
> This is fine for **discovery** ("which studies are relevant?"), which is the spike's goal — just don't promise per-sample precision from it. Accurate within-sample multi-field filtering is precisely the v2 reason to index GSM (8.6M docs). → [[40-Roadmap]], [[11-The-Metadata-Problem]]
>
> Related: the GSE↔GSM relationship is **many-to-many** — a sample can belong to multiple series (reused controls; a series *and* the SuperSeries that bundles it), and SuperSeries nest sub-series. So "every sample is reachable from a series" is true, but no sample is owned by exactly one series. → [[10-GEO-Data-Model]]

## Proposed facets

| Facet | Backing field | Type | Hierarchical? |
|---|---|---|---|
| Organism | `organism_ids[]` (NCBITaxon) | multi-select | mild |
| Assay category/detail (v1) | `assay_categories[]` / `assay_labels[]` | multi-select | no; controlled labels |
| Assay ontology (later) | future `assay_ids[]` (EFO) | multi-select | **yes** after grounding |
| Tissue | `tissue_id[]` (UBERON) | multi-select | **yes** |
| Cell type | `cell_type_id[]` (CL) | multi-select | **yes** (DAG) |
| Disease | `disease_id[]` (MONDO) | multi-select | **yes** |
| Sex | `sex_ids[]` (PATO) | multi-select | no |
| Sample count | `n_samples` | numeric range buckets | no |
| Year | `submission_year` | range/histogram | no |
| Instrument | `instrument_model[]` (GPL, **organism stripped**) | multi-select | no |
| Platform technology | `platform_technology[]` (GPL `technology` attr) | multi-select | no |
| Superseries / subseries | flag | boolean | no |

> ⚠️ **Do NOT facet on raw `platform_id` (GPL).** GEO clones each sequencer per organism (`NextSeq 500 (human)` vs `(mouse)` are different GPLs — see [[10-GEO-Data-Model#Platforms (GPL) — the organism-cloning gotcha|the GPL gotcha]]), so raw GPL double-encodes organism (already its own facet) and splinters counts. Instead derive **`instrument_model`** (organism stripped, e.g. "Illumina NextSeq 500") and use GEO's **`technology`** attribute as a clean coarse facet. Keep raw `platform_id[]` only for exact lookup / provenance, not as a facet.
>
> **Platform ≠ assay.** GPL gives the *machine*, never "10x 3′ scRNA-seq" — chemistry/assay is normalized to EFO from prose ([[22-Ontology-Normalization]]), a *separate* facet. Instrument and assay are two different columns.

## Two hard parts

### A. Disjunctive (multi-select) facet counts
Convention: **OR within a facet, AND across facets**. The trap: once the user selects `Organism = human`, the Organism facet must **exclude its own filter** when counting — otherwise "mouse (612)" vanishes and can never be added back.
- **Rule:** a facet's own counts reflect *every other* filter but **not its own**.
- **Implementation:** one aggregation per disjunctive facet where that facet's predicate is dropped (Solr's tag/exclude; ES's per-facet `filters` agg; in Postgres, one `COUNT(*) … GROUP BY` per facet with its own `WHERE` clause omitted). → [[26-Datastore-Postgres#Facet queries]]

### B. Ontology-hierarchy facets ("T cell" → all subtypes)
Selecting a parent term should match all descendants. Two patterns:

1. **Materialized transitive-ancestor arrays (recommended).** At normalization time, store per record the *full ancestor closure* of every assigned term (multi-valued, because CL/EFO are **DAGs** with multiple parents). Then:
   - Filter "T cell" = `ancestors @> ARRAY['CL:0000084']` — matches every descendant automatically.
   - Roll-up counts are **free**: a `GROUP BY` over the ancestor array makes each record contribute to *all* its ancestors' buckets.
   - Cost: bigger index; re-materialize when the ontology version changes.
2. **Query-time descendant expansion.** Keep only the leaf term; at query time look up all descendants and expand into a big `IN (…)`. Simpler indexing, always current, but expansion sets can be huge and slow.

> **Decision:** materialized ancestor arrays. Ontologies change slowly; queries are frequent; and it turns hierarchy into ordinary array containment + `GROUP BY`. This is the standard biomedical-search approach (path-hierarchy tokenizer in ES, `lvl0/1/2` in Algolia — same idea, ancestor-set variant for DAGs).

## Engine reality

- **Postgres native:** facets = `GROUP BY value, COUNT(*)` (+ `GROUPING SETS`), ancestor arrays via `@>`/`unnest`. Works fine at 289k rows; disjunctive facets = a handful of small queries.
- **ParadeDB `pg_search`:** first-class faceting via `pdb.agg` over a columnar (Tantivy) index — sub-100ms facets over tens of millions of rows, single pass. Use this if native `GROUP BY` gets slow (mainly a v2 / sample-level concern).

Both live in the **same Postgres**; `pg_search`'s `pdb.agg` is our primary facet path (committed), with `GROUP BY` as the portable fallback. → [[26-Datastore-Postgres]]

## Ties back to search

Facets aren't separate from [[23-Search-and-Retrieval|retrieval]] — they're aggregations **over the current (filtered, hybrid-retrieved) result set**. The MCP `search` tool returns both the ranked list and the facet counts in one response. → [[27-MCP-Interface]]

## Sources

- Disjunctive multi-select faceting (tag/exclude) — https://yonik.com/multi-select-faceting/
- Hierarchical facets via `path_hierarchy` tokenizer — https://www.elastic.co/docs/reference/text-analysis/analysis-pathhierarchy-tokenizer
- Ontology-enhanced faceted search (VLDB) — https://link.springer.com/article/10.1007/s00778-022-00735-3
- Materialized hierarchy paths (Algolia hierarchicalMenu) — https://www.algolia.com/doc/api-reference/widgets/hierarchical-menu/js
- Postgres columnar faceting fast path (ParadeDB) — https://www.paradedb.com/blog/faceting
