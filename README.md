# GEO Metadata Index

Metadata index + search service over [NCBI GEO](https://www.ncbi.nlm.nih.gov/geo/) — **v1 spike**.

The design lives in the planning vault under [`wiki/`](wiki/Home.md) (Obsidian). Start at [`wiki/Home.md`](wiki/Home.md); the roadmap is [`wiki/40-Roadmap.md`](wiki/40-Roadmap.md).

## Setup

Requires Python ≥3.11 and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
```

For a polite, faster crawl (10 req/s instead of 3), set an [NCBI API key](https://www.ncbi.nlm.nih.gov/account/):

```bash
export NCBI_API_KEY=...        # optional but recommended
export NCBI_EMAIL=you@example.com
```

## Stage 1 — download series metadata (`esummary`)

Enumerates GEO **Series (GSE)** and lands each series' `esummary` record as one
line of JSONL. Series-level only (v1 scope). Idempotent and resumable — re-run
to continue, not duplicate.

```bash
# Small slice to iterate on (default term = human + mouse):
uv run geo-fetch-summaries --limit 2000

# A specific scope:
uv run geo-fetch-summaries --term 'GSE[ETYP] AND "Homo sapiens"[Organism]'

# Full corpus for the current term (drop --limit):
uv run geo-fetch-summaries
```

Output: `data/raw/geo_series_summaries.jsonl` (+ a `.progress.json` checkpoint).
`data/` is git-ignored. This is the **catalog**: `title`, `summary`, `taxon`,
`gdstype`, `n_samples`, platform IDs, PubMed IDs, FTP link. It does **not**
include the per-sample metadata the index is built from — that's stage 2.

## Stage 2 — download full metadata (brief SOFT)

For each series in the catalog, downloads the **metadata-only** SOFT via GEO's
`acc.cgi` (`view=brief`): every `!Series_*` and `!Sample_*` attribute —
including the `!Sample_characteristics_ch1` goldmine (`tissue:`, `sex:`,
`cell line:`…), `!Series_overall_design`, and `!Sample_library_strategy` — but
**no expression data tables**. Each series is gzipped to disk once, mirroring
the GEO FTP bucket layout, so parsing later never re-downloads.

```bash
uv run geo-fetch-soft --limit 50      # iterate on a slice
uv run geo-fetch-soft                 # everything in the catalog
```

Output: `data/raw/soft/GSE<nnn>nnn/GSE<n>.soft.gz`. Idempotent — existing files
are skipped; failures are logged to `data/raw/soft/_failures.log` for re-run.

## Browse local SOFT files

Open a lightweight local browser over the downloaded `.soft.gz` files:

```bash
uv run geo-soft-browser
```

The browser expects [`rg`](https://github.com/BurntSushi/ripgrep) on `PATH` and
uses its compressed-file search mode. Run `uv run geo-strip-soft` first to
populate the default `data/processed/soft_meta` search tree, or check
**Search raw files** in the UI to search `data/raw/soft` directly.

It listens on [http://127.0.0.1:8001](http://127.0.0.1:8001) by default.
Searches use stripped metadata under `data/processed/soft_meta`; use the
left-side **Search raw files** checkbox to search the original files under
`data/raw/soft`. Selecting a GSE opens its metadata SOFT, and the right-side
**Show original raw file** checkbox switches the viewer to its full raw family
file. Pass `--port`, `--raw-dir`, or `--metadata-dir` to use another local
snapshot. Each search stops after the first 10 matching GSEs, so refine the
words if the desired series is not shown.

> **Why not FTP family files / esummary JSON?** The FTP `*_family.soft.gz`
> bundles the full expression matrix (~9 MB+ per series, TB-scale for the
> corpus) and 404s for freshly-released series. The esummary JSON omits all
> per-sample metadata. `acc.cgi view=brief` is the only source that is
> metadata-complete, data-free, and available for new series.

## Rebuild the Postgres search database

The **v1** search database is reproducible from the GEOmetadb SQLite file and
the generated JSONL/embedding artifacts. Build those artifacts first:

```bash
uv run geo-build-series-docs
uv run geo-embed
```

Then rebuild Postgres in this order:

```bash
# Destructive: drops and recreates `series`. Use only on an isolated/local DB.
uv run python -m geo_index.pg_hybrid init
uv run python -m geo_index.pg_hybrid load

# `migrate` is idempotent and ensures every normalization column exists.
uv run geo-normalize migrate
uv run geo-normalize run

# Builds BM25, HNSW, and the four normalized-array GIN indexes.
uv run python -m geo_index.pg_hybrid index
uv run geo-normalize report
```

`load` checks that `geo_series.jsonl`, `embeddings.npy`, and
`embeddings.ids.json` have identical GSE ordering before inserting anything.
Running normalization before index creation avoids maintaining the new GIN
indexes during the full normalization update.

To upgrade an already-populated database without rebuilding BM25, HNSW,
normalization, or embeddings, run only this command after receiving database
change approval:

```bash
uv run python -m geo_index.pg_hybrid filter-index
```

Filters are series-level: selecting values across fields means the GSE contains
each value somewhere, not that one GSM sample contains all of them.

## Local Elasticsearch foundation

The prototype Elasticsearch service is pinned to 9.4.2, binds only to
`127.0.0.1:9200`, persists its data in a named Docker volume, and enables a
local trial license so native RRF can be verified. Create the ignored local
environment file and choose a local-only password:

```bash
cp .env.elasticsearch.example .env.elasticsearch
# Edit ELASTICSEARCH_PASSWORD before starting.
docker compose --env-file .env.elasticsearch \
  -f docker-compose.elasticsearch.yml up -d
docker compose --env-file .env.elasticsearch \
  -f docker-compose.elasticsearch.yml ps
```

The Compose file preserves disk protection with local absolute watermarks of
1 GB free (low), 750 MB (high), and 500 MB (flood stage). Expand Docker's disk
allocation before loading the real corpus; the tiny synthetic test can run
within those bounds, but the full metadata and vector index cannot fit in a
nearly-full Docker VM.

Export the same URL and credentials for the Python client, then run the
synthetic container tests:

```bash
set -a
source .env.elasticsearch
set +a
GEO_TEST_ELASTIC=1 uv run pytest tests/test_elasticsearch_live.py -v
```

The loader is independent of Prefect and model execution. After the canonical
record tree and at least one canonical model artifact are complete, load them
with:

```bash
uv run geo-elasticsearch-load \
  --records-root data/processed/series_records \
  --artifacts-root data/processed/embedding_artifacts \
  --report data/processed/elasticsearch_load_report.json
```

Run the identical command a second time to prove the document count is
unchanged: bulk actions use GSE as `_id` and `index` as the operation, so the
second load replaces the same documents. Real-corpus ingestion is intentionally
deferred until the ETL and per-model artifacts are complete.

For a later managed deployment, configure only `ELASTICSEARCH_URL` and either
`ELASTICSEARCH_USERNAME` plus `ELASTICSEARCH_PASSWORD`, or
`ELASTICSEARCH_API_KEY`. The loader and search code do not depend on Docker or
localhost.
