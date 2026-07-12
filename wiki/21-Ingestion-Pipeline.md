---
title: Ingestion Pipeline
tags: [ingestion, pipeline, etl]
---

# 21 · Ingestion Pipeline

← [[Home]] · uses [[10-GEO-Data-Model]] · feeds [[22-Ontology-Normalization]]

## Goal

> **Current primary path (2026-07-12):** `geo-soft-etl` is a required Prefect
> chain from stripped SOFT through canonical records and the complete
> `gemini_embedding_2_3072_v1` artifact into Elasticsearch. It succeeds only
> after the `geo-series` index and `embedding_gemini_3072` coverage are audited.
> Existing canonical records and provider state remain resumable after failure.

Maintain a reproducible series-level metadata corpus. For the fixed v1 spike,
the chosen source is the 222,961-GSE GEOmetadb snapshot already materialized as
`data/processed/geo_series.jsonl`. A metadata-only top-up from its 2024-02-29
cutoff to current GEO is a later freshness release.

> **Pipeline update (2026-07-11):** The GEOmetadb JSONL remains the implemented
> search/embedding baseline. The new current ingestion task uses the much larger
> downloaded stripped-family-SOFT collection directly and writes one canonical
> JSON record per GSE through Prefect. See
> [[53-Prefect-SOFT-ETL-and-Embedding-Prototype-Plan]]. The prototype keeps one
> canonical version and uses output existence as completion; daily manifests and
> source-change detection are deferred to [[54-Incremental-Corpus-Future-State]].

## Historical GEOmetadb baseline path

1. Read `data/external/GEOmetadb.sqlite`.
2. Aggregate GSE rows plus distinct GSM organism/molecule/source/characteristic
   values with `geo-build-series-docs`.
3. Write the fixed `geo_series.jsonl` corpus used by BGE and the alternate-model
   bake-off.
4. Keep `geo-fetch-summaries` and metadata-only `geo-fetch-soft` as top-up
   tooling; do not mix new rows into the fixed evaluation corpus mid-bake-off.

This decision and its measured source tradeoff are recorded in
[[42-Build-Log#What we tried (and what we chose)]].

## Current SOFT → canonical-record path

The operator entry point owns the complete primary chain:

```bash
set -a
source .env
source .env.elasticsearch
set +a
uv run geo-soft-etl \
  --allow-paid-gemini \
  --gemini-concurrency 4
```

After materialization, the flow builds or resumes
`embedding_artifacts/gemini_embedding_2_3072_v1`, loads every available
registered embedding artifact into Elasticsearch `geo-series`, refreshes once,
and audits document count plus full `embedding_gemini_3072` coverage. Gemini
embedding and Elasticsearch loading are required primary stages. Any parse,
embedding, connection, bulk-item, or audit failure makes the run unsuccessful.
Stable GSE `_id` values and durable local artifacts make the complete operation
safe to retry.

The crawler and table stripper already produce metadata-only family SOFT files
under `data/processed/soft_meta/`. At the 2026-07-11 checkpoint there were
244,186 such files (about 1.6 GB), enough to start ETL before the crawl finishes.

The prototype flow:

1. inventories stripped SOFT files once per run;
2. skips any GSE whose canonical
   `data/processed/series_records/<bucket>/<GSE>.json` exists, without reopening
   its source;
3. parses only missing outputs and publishes each JSON atomically;
4. builds or resumes the registered Gemini NumPy matrix/ID/metadata artifact
   from the completed canonical JSON tree;
5. loads every available registered embedding artifact into local Elasticsearch
   and audits document count plus full Gemini coverage before reporting success.

Deleting one derived record is the explicit v1 invalidation mechanism. The next
run rebuilds it and replaces that GSE's configured embeddings. This intentionally
does not detect an updated upstream SOFT file automatically.

## Deferred living-corpus path (v2+)

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

## Operational notes for the deferred top-up

- **Politeness:** cap at NCBI's documented API-key rate, back off on 429/5xx,
  and checkpoint the last UID
  ([usage guide](https://www.ncbi.nlm.nih.gov/books/NBK25497/)).
- **Throughput:** the existing metadata-only experiment measured about 2.3
  requests/s and roughly 26 hours for the gap; remeasure before scheduling a
  freshness run. → [[42-Build-Log#Metadata source — crawl vs. bulk]]
- **Storage/refresh cadence:** measure the top-up artifact and choose a cadence
  only if the spike becomes a maintained service.
- The selected GEOmetadb mirror was measured through 2024-02-29; it is the v1
  bulk source, not a claim of current completeness. Use top-up tooling for newer
  releases rather than silently relabeling the snapshot as current GEO.

### Current v1 index rebuild and resume contract

The current Postgres materialization path is a full rebuild, not the future
incremental upsert design. `pg_hybrid init` deliberately drops `series`, so a
**(v1)** rebuild must run all four stages in order:

```bash
uv run python -m geo_index.pg_hybrid init
uv run python -m geo_index.pg_hybrid load
uv run geo-normalize run
uv run python -m geo_index.pg_hybrid index
```

`geo-normalize run` uses the same shared assay detector as targeted refreshes,
so a full ETL rerun automatically gets the hardened assay values. For an
assay-rule-only release, do not reload raw rows or embeddings; update only the
three persisted assay columns:

```bash
uv run geo-normalize assay-refresh
```

Both normalization commands commit deterministic `UPDATE`s in batches. They
are idempotent and safe to rerun after interruption, but do not skip already
committed IDs: resume by running the same command again from the beginning.
If `load` fails before its final commit, rerun `load`; if normalization fails,
rerun only the applicable normalization command. Do not rerun `init` unless the
intent is to replace the table. A checkpointed incremental Postgres loader and
orchestrator remain **(v2+)** work.

## Sources

- E-utilities — https://www.ncbi.nlm.nih.gov/books/NBK25501/ · usage / rate limits — https://www.ncbi.nlm.nih.gov/books/NBK25497/
- Download / FTP layout — https://www.ncbi.nlm.nih.gov/geo/info/download.html · programmatic access — https://www.ncbi.nlm.nih.gov/geo/info/geo_paccess.html
- HTS→SRA linkage — https://www.ncbi.nlm.nih.gov/geo/info/seq.html
- GEOparse — https://github.com/guma44/GEOparse · pysradb — https://github.com/saketkc/pysradb
- GEOmetadb package/schema — https://www.bioconductor.org/packages/release/bioc/html/GEOmetadb.html

## Handoff

The fixed JSONL → [[25-Embeddings-and-Cost|embedding]] and the implemented
`series` table; [[22-Ontology-Normalization]] then populates the current flat
filter/facet columns.

## Open questions

- Series-level only, or also persist per-sample rows now for a later v2? (Cheap to store raw either way.)
