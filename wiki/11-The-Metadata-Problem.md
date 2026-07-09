---
title: The Metadata Problem
tags: [geo, problem, motivation]
---

# 11 · The Metadata Problem

← [[Home]] · [[00-Overview]]

This note makes the pain concrete. Everything downstream ([[22-Ontology-Normalization]], [[23-Search-and-Retrieval]], [[24-Faceted-Search]]) exists to solve one of these two failures.

## Failure 1 — "single cell RNA" doesn't find single-cell datasets

**Why the structured fields don't help:** GEO/SRA has no field for single-cell platform or chemistry. For an scRNA-seq study:
- `library_strategy` = `RNA-Seq` (identical to bulk — there is no `scRNA-seq` enum value)
- `library_source` = `TRANSCRIPTOMIC` (identical to bulk)

The single-cell-ness lives **only in free text**: `!Sample_extract_protocol_ch1`, `!Sample_library_construction_protocol`, `!Series_summary`, `!Series_overall_design`, `!Sample_characteristics_ch1`. A published single-cell metadata review states it plainly: *"There is no specific annotation that can be used to identify single-cell datasets in GEO."* ([PMC8121533](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8121533/))

**Why keyword search then fails:** the same concept is spelled many incompatible ways —

> single cell · single-cell · scRNA · scRNA-seq · sc-RNAseq · 10x · 10X Genomics · Chromium · GEM/GEMs · droplet-based · Drop-seq · Smart-seq2 · SPLiT-seq · sci-RNA-seq · CEL-seq2 · inDrop

A literal query for `"single cell RNA"` matches none of the records that only say `10x Chromium 3' v3` or `SPLiT-seq`.

### How we fix it (two complementary mechanisms)
1. **Dense/semantic retrieval** — embed the free text so "single cell RNA" lands near "10x Chromium droplet-based scRNA" in vector space even with zero shared tokens. → [[23-Search-and-Retrieval]]
2. **Ontology-aware query expansion** — expand the query into the known synonym/assay set (grounded in EFO/OBI, not hallucinated) before retrieval. → [[23-Search-and-Retrieval#Query expansion]]
3. **Normalized `assay` facet** — a controlled `assay` field (EFO) so the *result* is filterable to "10x 3′ vs 5′ vs SPLiT-seq". → [[22-Ontology-Normalization]]

## Failure 2 — the same value written many ways

`!Sample_characteristics_ch1` is `key: value`, but keys and values are submitter-invented:

| Concept | Values seen in the wild | Target |
|---|---|---|
| sex | `M`, `F`, `male`, `Female`, `0`, `1`, `XX`, `unknown` | PATO:0000384 / PATO:0000383 |
| organism | `human`, `Homo sapiens`, `H. sapiens`, `hsapiens` | NCBITaxon:9606 |
| tissue | `breast tumor`, `mammary carcinoma`, `breast, cancer` | UBERON + MONDO |
| age | `50`, `50y`, `50 years`, `P50`, `E14.5` | real-valued + units |

You cannot build a facet or a filter on the raw strings. You need to **collapse each onto a canonical ontology ID**. That's [[22-Ontology-Normalization]].

## The scale reality check

- ~289k series, ~8.6M samples. Free text is inconsistent, long-tailed, and never going to be retrofitted upstream.
- This is exactly why every serious downstream resource (MetaSRA, CELLxGENE, DISCO, STARGEO) *rebuilds* the metadata layer. We're doing the same, but corpus-wide and search-first. → [[30-Prior-Art]]

## Design consequence

> **Search and normalization are two jobs, not one.** Embeddings alone give recall but not clean facets; ontology IDs alone give facets but not fuzzy recall. We need both, and they reinforce each other: normalized values get folded back into the embedded document text, improving retrieval too. This split drives the whole [[20-Architecture-Overview|architecture]].

## Sources

- No structured single-cell field; metadata heterogeneity — https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8121533/
- Mining GEO metadata (vocabulary variance) — https://www.elucidata.io/blog/mining-data-and-metadata-from-geo-datasets
