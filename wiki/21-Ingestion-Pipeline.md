---
title: Ingestion Pipeline
tags: [ingestion, pipeline, etl]
---

# 21 · Ingestion Pipeline

← [[Home]] · uses [[10-GEO-Data-Model]] · feeds [[22-Ontology-Normalization]]

## Goal

Get all ~289k GSE series (metadata only) into `geo_series_raw`, idempotently and re-runnably, respecting NCBI rate limits.

## Stages

### 1. Enumerate the universe
- `esearch.fcgi?db=gds&term=GSE[ETYP]&retmax=…` (paginate) to list all Series UIDs. Optionally scope by organism/date for the spike.
- Get a free **NCBI API key** → 10 req/s. Add `&api_key=` and a real `&tool=`/`&email=`. ([usage guide](https://www.ncbi.nlm.nih.gov/books/NBK25497/))
- `esummary` (JSON) gives cheap structured-ish fields (title, taxon, gdsType, n_samples, PDAT) — good for a first-pass index and for sanity counts.

### 2. Fetch full metadata (FTP, not E-utils)
- Prefer the **MINiML family** (`GSExxx_family.xml.tgz`) or the **series matrix header** (`GSExxx_series_matrix.txt.gz`) — both carry the full `!Series_*` and per-sample `!Sample_*` blocks. FTP is faster and gentler than efetch for bulk.
- Path masking: GSE12345 → `…/geo/series/GSE12nnn/GSE12345/`.

### 3. Parse
- **GEOparse** for SOFT; or stdlib XML for MINiML. Extract:
  - Series: title, summary, overall_design, pubmed_id, submission/update dates, platform ids, sample ids, `gdsType`.
  - Per sample: title, source_name, **characteristics `key:value` list**, molecule, extract/treatment protocol, `library_strategy/source/selection`.
  - Per platform: title, **`technology`** attribute; derive **`instrument_model`** by stripping the organism suffix from the GPL title (GEO clones sequencers per organism — see [[10-GEO-Data-Model#Platforms (GPL) — the organism-cloning gotcha|the GPL gotcha]]). Keep raw `platform_id` for provenance.
- Aggregate sample fields up to the series (distinct values, counts) for series-level facets/embedding.

### 4. SRA enrichment (sequencing series)
- `elink` gds→sra, or **pysradb** GSE→SRX/SRR, to grab `library_*` and confirm platform/instrument. Helps flag single-cell candidates and assay.

### 5. Land raw
- Write `geo_series_raw(gse PK, esummary jsonb, miniml jsonb, samples jsonb, sra jsonb, fetched_at, source_etag)`.
- **Idempotent:** upsert on `gse`; skip if `update_date` unchanged. This makes incremental refresh trivial later.

## Operational notes

- **Politeness:** cap ≤10 req/s, backoff on 429/5xx, resumable checkpoints (last UID processed). One full crawl of 289k series is hours, not days.
- **Storage:** raw JSON for 289k series is a few GB — nothing.
- **Refresh:** GEO grows ~continuously; re-run enumeration weekly, re-fetch only changed `update_date`. Out of scope for the spike but the idempotent design gets it for free.
- **Don't** lean on [[10-GEO-Data-Model#GEOmetadb — tempting but stale|GEOmetadb]] as the source (stale since 2021) — but its SQLite schema is a fine reference for table shapes.

## Sources

- E-utilities — https://www.ncbi.nlm.nih.gov/books/NBK25501/ · usage / rate limits — https://www.ncbi.nlm.nih.gov/books/NBK25497/
- Download / FTP layout — https://www.ncbi.nlm.nih.gov/geo/info/download.html · programmatic access — https://www.ncbi.nlm.nih.gov/geo/info/geo_paccess.html
- HTS→SRA linkage — https://www.ncbi.nlm.nih.gov/geo/info/seq.html
- GEOparse — https://github.com/guma44/GEOparse · pysradb — https://github.com/saketkc/pysradb
- GEOmetadb (stale) — https://www.bioconductor.org/packages/release/bioc/html/GEOmetadb.html · https://support.bioconductor.org/p/9149627/

## Handoff

Raw rows → [[22-Ontology-Normalization]] extracts fields and assigns ontology IDs; then [[25-Embeddings-and-Cost|embedding]] + [[26-Datastore-Postgres|indexing]].

## Open questions

- Full corpus vs. a scoped first slice (e.g. human+mouse) for the very first crawl? → [[41-Open-Questions]]
- Series-level only, or also persist per-sample rows now for a later v2? (Cheap to store raw either way.)
