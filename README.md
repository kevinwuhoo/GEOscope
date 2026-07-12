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

## Canonical SOFT records and embedding artifacts

Materialize one complete canonical JSON record per stripped family SOFT file:

```bash
uv run geo-soft-etl
```

Existing records under `data/processed/series_records/<bucket>/` are terminal
and skipped without reading their source. Delete one record explicitly to
recompute it. The flow runs directly with a bounded local Prefect thread pool;
the Prefect server/UI is optional.

Build complete aligned matrix artifacts from the canonical record tree:

```bash
uv run python -m geo_index.build_embedding_artifact --model-key bge_small_v15
uv run python -m geo_index.build_embedding_artifact --model-key medcpt_v1
uv run python -m geo_index.build_embedding_artifact --model-key qwen3_06b_1024_v1
```

Each model publishes `vectors.npy`, `ids.json`, and `metadata.json` under
`data/processed/embedding_artifacts/<model-key>/`. A valid existing directory
skips all encoder work. Delete that one directory explicitly when the canonical
record inventory changes and a complete rebuild is intended.

The existing aligned BGE baseline can be copied into the canonical contract
without changing its source files:

```bash
uv run python -m geo_index.adopt_embeddings --model-key bge_small_v15
```

Gemini corpus embeddings use only bounded asynchronous Google batch/file API
shards. The 1,000-request and 100 MiB limits bound transport files; they do not
truncate documents. Every request preserves the complete formatted title and
`embed_text`, including multibyte Unicode. Provider token-limit or per-row
errors fail the build while preserving request, state, and result files for
diagnosis and resume. Per-shard state makes uploads, jobs, and downloads
resumable.

The default corpus build intentionally does not call exact `count_tokens`,
which would add a separate synchronous provider request for every document.
Its byte-derived token and cost upper bounds are informational only: they are
neither provider token counts nor proof that the provider will accept an
input. The command cannot submit unless both `GEMINI_API_KEY` and
`--allow-paid-gemini` are present:

```bash
set -a
source .env
set +a
uv run python -m geo_index.build_embedding_artifact \
  --model-key gemini_embedding_2_3072_v1 \
  --gemini-concurrency 4 \
  --allow-paid-gemini
```

`--gemini-concurrency` controls provider-side active batch jobs while one local
coordinator remains the sole state writer. Its default is `1`. Do not launch
multiple builder processes against the same temporary state directory. A rerun
resumes persisted uploads and jobs and assembles results in canonical order.

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
