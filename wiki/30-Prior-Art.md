---
title: Prior Art
tags: [prior-art, landscape]
---

# 30 · Prior Art

← [[Home]]

The landscape, and the gap we're aiming at.

## Access / re-indexing (no ontology normalization)

- **GEOmetadb** — SQLite dump of parsed GEO metadata for arbitrary SQL. The
  client repository downloads a pre-built database rather than publishing the
  upstream ETL. Although older community reports described stale releases, the
  mirror measured for this project runs through 2024-02-29 (222,961 GSEs). We
  use that fixed v1 snapshot with an explicit cutoff, not as a live source.
  ([paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC2639278/),
  [client repo](https://github.com/zhujack/GEOmetadb),
  [[42-Build-Log#Metadata source — crawl vs. bulk]])
- **OmicIDX** (Sean Davis) — treats repo metadata "as data": ingests SRA + BioSample, serves GraphQL/OpenAPI + a public **BigQuery** dataset, adds heuristic MeSH/ontology hints. Access-first. ([github](https://github.com/omicidx/omicidx-api))
- **ARCHS4** — uniformly *re-aligned* ~188k human+mouse RNA-seq samples; tissue/cell-line are manually curated, **not** ontology-mapped. ([paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC5893633/))
- **recount3** — ~750k uniformly processed samples; ships **raw** metadata and *delegates* ontology normalization to MetaSRA. ([paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC8628444/))

## Ontology normalization of existing free text (closest to our ②)

- **MetaSRA** ⭐ — the reference. Rule-based NLP maps SRA BioSample attributes → **DOID, CL, UBERON, EFO, Cellosaurus**, assigns a sample-type category, extracts real-valued properties. High precision (0.989 on properties), lower recall (0.672) — the classic deterministic signature. Our [[22-Ontology-Normalization|cascade]] extends this with similarity + LLM-grounding tiers. ([paper](https://academic.oup.com/bioinformatics/article/33/18/2914/3848915))
- **STARGEO** — human crowd-curation of GEO series tags, mapped post-hoc to DOID/EFO/SNOMED/MeSH. Proved the value but is **curation-bound** (~32% coverage). We automate instead. ([paper](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5604135/))
- **ALE** — heuristic + ML extraction of **age/sex/tissue** labels from GEO free text. ([paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC5751806/))

## Enforced-schema corpora (ontology mandated at submission)

- **CZ CELLxGENE Discover** ⭐ — curated single-cell corpus, faceted discovery, **schema mandates** organism→NCBITaxon, tissue→UBERON, cell type→CL, disease→MONDO, assay→EFO, sex→PATO, ancestry→HANCESTRO, dev-stage→HsapDv/MmusDv. **Our field→ontology map is theirs.** But single-cell-only, curated, not full-GEO. ([NAR 2025](https://academic.oup.com/nar/article/53/D1/D886/7912032))
- **HCA metadata standard** — JSON-schema-validated, same ontology stack. Submission-time, not retroactive.
- **Sfaira** — data zoo enforcing CL/UBERON/EFO/MONDO/NCBITaxon for cross-dataset models. ([paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC8386039/))
- **DISCO** — >100M cells, controlled-vocab + ontology faceting; single-cell-only. ([NAR](https://academic.oup.com/nar/article/53/D1/D932/7899529))

## LLM / RAG-over-GEO (emerging, not mature)

- **CompBioAgent** — NL querying of single-cell expression DBs.
- General biomedical RAG: **BioRAG, MedBioRAG, GENEVIC**.
- New LLM harmonization efforts: **MetaMuse**, **Metappuccino** (2025 bioRxiv preprints).
- No dominant production "RAG over full GEO" exists yet.

## The gap → our thesis

> There is **no widely-adopted, ontology-faceted, natural-language semantic search engine over the full ~289k-series GEO corpus** (bulk + single-cell + array). Everything is access-only, SRA-focused, curation-bound, or single-cell-only. Combining **automated cascade normalization** (MetaSRA-style, extended) + **hybrid semantic search** + **MCP-served retrieval** across *all* of GEO is the unfilled niche.

## What we borrow

| From | Take |
|---|---|
| CELLxGENE | the field→ontology schema (verbatim) |
| MetaSRA | the deterministic-first mapping approach + sample-type idea |
| STARGEO | proof that ontology tags make GEO searchable (but automate it) |
| GEOmetadb | fixed v1 bulk snapshot and table-shape inspiration; its unpublished upstream ETL and explicit cutoff argue for our own reproducible top-up path |
| recount3 | "delegate hard bits" mindset — we delegate *generation* to the LLM |

## Sources

- GEOmetadb — https://pmc.ncbi.nlm.nih.gov/articles/PMC2639278/ (verified directly) · GEOmetadb repo (client only, no ETL) — https://github.com/zhujack/GEOmetadb (verified directly) · OmicIDX — https://github.com/omicidx/omicidx-api
- ARCHS4 — https://pmc.ncbi.nlm.nih.gov/articles/PMC5893633/ · recount3 — https://pmc.ncbi.nlm.nih.gov/articles/PMC8628444/
- MetaSRA — https://academic.oup.com/bioinformatics/article/33/18/2914/3848915 · STARGEO — https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5604135/ · ALE — https://pmc.ncbi.nlm.nih.gov/articles/PMC5751806/
- CELLxGENE (NAR 2025) — https://academic.oup.com/nar/article/53/D1/D886/7912032 · HCA — https://ebi-ait.github.io/hca-metadata-community/ontologies/ontologies.html · Sfaira — https://pmc.ncbi.nlm.nih.gov/articles/PMC8386039/ · DISCO — https://academic.oup.com/nar/article/53/D1/D932/7899529
