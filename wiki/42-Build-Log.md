---
title: Build Log & Findings
tags: [progress, build-log, findings, spike]
created: 2026-07-08
---

# 42 · Build Log & Findings

← [[Home]] · plan in [[40-Roadmap]] · decisions in [[41-Open-Questions]]

A running record of what we've actually built, what we tried, and what the data
told us — so assumptions in the design notes get corrected by evidence.

## Status — 2026-07-08 (hackathon spike, day 1)

**A full end-to-end retrieval thread works in-memory**, on the whole corpus, at
zero API cost. We can embed all ~223k series and semantically search them. Next
decision open (see bottom): what narrative/demo to build on top.

Pipeline built so far (all in `src/geo_index/`, driven by `uv run …`):

| Stage | Command | Output | Source |
|---|---|---|---|
| 1 · catalog **(v1)** | `geo-fetch-summaries` | `data/raw/geo_series_summaries.jsonl` | E-utilities `esearch`/`esummary` |
| 2 · metadata SOFT **(v1)** | `geo-fetch-soft` | `data/raw/soft/…soft.gz` | `acc.cgi` brief (metadata-only) |
| — · **bulk metadata** | — | `data/external/GEOmetadb.sqlite` (18 GB) | **GEOmetadb dump** |
| 3 · series docs **(v1)** | `geo-build-series-docs` | `data/processed/geo_series.jsonl` (222,961) | GEOmetadb |
| 4 · embeddings **(v1)** | `geo-embed` | `embeddings.npy` (223k × 384) | `bge-small-en-v1.5`, local/MPS |
| 5 · search test | `geo-search "<query>"` | ranked results | brute-force cosine (in-memory) |

Corpus health: 100% have a summary, 99% `overall_design`, 97% aggregated
sample characteristics. 8.47M samples rolled up to their series.

## What we tried (and what we chose)

### Metadata source — crawl vs. bulk
We evaluated three ways to get the actual metadata (not just the light
esummary, which omits every per-sample field):
- **`acc.cgi` brief SOFT** — metadata-complete, data-free, works for brand-new
  series. But **slow**: measured ~2.3 req/s, **no** NCBI throttling even at
  concurrency 40 (payload-bound, not rate-capped) → ~26 h for the full slice.
- **FTP `*_family.soft.gz`** — bundles the full expression matrix (~9 MB+ per
  modest series → TB-scale) and **404s for freshly-released series**. Rejected.
- **GEOmetadb** (pre-parsed SQLite) — one 1.1 GB download = the whole corpus,
  already parsed from the same SOFT files. **Chosen** for the spike.

**Decision:** build on GEOmetadb now; keep `geo-fetch-soft` as the **top-up**
path for the ~66k series added since GEOmetadb's cutoff. See [[21-Ingestion-Pipeline]].

### Correction: GEOmetadb is *not* stale to 2021
[[10-GEO-Data-Model]] claims GEOmetadb was "last rebuilt ~2021-11-03." The live
mirror we pulled is **current through 2024-02-29** (222,961 GSE, 7.0M GSM,
25,880 GPL). The only gap is ~Mar 2024 → now. *(That note should be updated.)*

### Embedding
`bge-small-en-v1.5` (384-dim), local on Apple-Silicon MPS: ~140 docs/s → full
corpus in ~22 min, **$0**. This is a *test baseline* — the model pick is still
an eval decision (MedCPT / OpenAI to A/B). See [[25-Embeddings-and-Cost]].

## Findings that revise earlier assumptions

### The single-cell worked example is weak on real data
The project's headline motivation ([[11-The-Metadata-Problem]]) is that a search
for "single cell RNA" misses studies whose metadata only says
10x/Chromium/Drop-seq/Smart-seq2/SPLiT-seq. **Measured on the corpus:**
- 7.8% of series say "single cell/nucleus" literally; 2.1% mention an sc-tech term.
- **85% of tech-mentioning series also say "single cell"** somewhere.
- The "hidden" set (tech term, never "single cell") = **693 (0.31%)**, and most
  are keyword **false positives** ("10X FCS", "10X" magnification, "chromium"
  the *metal* in fish-toxicology studies).

→ Plain keyword search already catches ~85% of sc studies. The dramatic
keyword-miss story doesn't carry the demo. **(This should soften the framing in
[[11-The-Metadata-Problem]] and [[Home]].)**

### What semantic search *did* prove valuable for
Paraphrased / conceptual queries with **zero keyword overlap** retrieve the
right studies at 0.82–0.85 cosine:
- *"transcriptomes of individual cells"* → single-cell studies
- *"spatial location of gene expression in tissue sections"* → Visium / Slide-seq
  / Sci-Space / spatial-array studies (none of which the query names)

The strength is **conceptual retrieval and cross-vocabulary matching**, not the
narrow single-cell keyword case.

## Candidate narratives to validate (for domain review)

Where similarity search over GEO metadata is genuinely useful — to be vetted
with a domain expert before we pick the demo:

1. **Meta-analysis / systematic-review dataset discovery** — assemble *every*
   dataset on a phenomenon despite heterogeneous wording; recall-critical.
2. **Perturbation / drug-response signature matching** — find studies perturbing
   a gene/pathway/drug described many ways (mTOR: rapamycin/torin/PP242/kd).
3. **Rare disease & rare cell-type recall** — few datasets, high vocabulary
   variance; where keyword search fails hardest → semantic recall matters most.
4. **Assay / method benchmarking** — "all spatial transcriptomics" =
   Visium/Slide-seq/MERFISH/seqFISH/…; *already demonstrable* (see above).
5. **Reference / control dataset finding** — "datasets like mine" for batch
   correction, deconvolution references, single-cell atlas integration.
6. **Non-model-organism / environmental & toxicogenomics** — worst-standardized
   metadata, so semantic search has the most headroom.

## Status — 2026-07-10 (normalized filters and facets)

Track 2's **v1** query layer is implemented on
`codex/track2-normalized-filters` (`98ebee3`; shared contract `792389a`). It
adds exactly four normalized fields: `organism_ids`, `sex_ids`,
`assay_categories`, and `assay_labels`.

Implemented behavior:

- values within one field use array overlap (OR); different fields are ANDed;
- filters run inside BM25, dense, and both hybrid candidate branches before
  their limits;
- filtered dense/HNSW queries enable iterative scanning;
- facets omit their own selected filter, retain every other filter, and count
  distinct series/value pairs;
- blank-query facets are exact over all matching rows; text-query facets are
  labeled as a bounded 1,000-candidate pool;
- `/api/search` accepts repeatable organism, sex, assay-category, and
  assay-label parameters and returns the normalized request plus scoped facets.

Read-only verification against the 222,961-row local database:

| Check | Result |
|---|---:|
| human (`NCBITaxon:9606`) | 97,114 |
| mouse (`NCBITaxon:10090`) | 71,204 |
| female (`PATO:0000383`) | 24,719 |
| male (`PATO:0000384`) | 28,934 |
| offline tests | 33 passed, 4 integration tests deselected |
| selected read-only Postgres tests | 3 passed |
| exact four-facet aggregation | 0.612 s observed |
| BM25 four-facet 1,000-candidate aggregation | 0.308 s observed |

The human-only, mouse-only, OR-within, AND-across, impossible-value, own-facet
alternative, and rare filtered-dense cases all passed. These timings are local
observations, not CI thresholds.

The four GIN indexes are implemented but were deliberately **not** created on
the shared database. After database-change approval, apply only the missing
indexes with:

```bash
uv run python -m geo_index.pg_hybrid filter-index
```

For a complete rebuild, the checked-in ETL order is:

```bash
uv run geo-build-series-docs
uv run geo-embed
uv run python -m geo_index.pg_hybrid init   # destructive; isolated/local only
uv run python -m geo_index.pg_hybrid load
uv run geo-normalize migrate
uv run geo-normalize run
uv run python -m geo_index.pg_hybrid index
uv run geo-normalize report
```

`init` creates the v1 normalized columns, `migrate` idempotently supplies the
complete normalization schema, and `index` builds BM25, HNSW, and all four GIN
indexes. No raw-data reload, normalization run, model load, or embedding rebuild
was performed while implementing Track 2. Final assay-label smoke testing remains
pending Track 1's targeted persisted-value refresh.

The series-aggregation caveat still applies: `human + female` means the GSE
contains each value somewhere, not necessarily on the same GSM sample.

## Sources

- GEOmetadb dump (mirror; currency verified 2026-07-08 from file + `max(submission_date)`) — https://gbnci.cancer.gov/geo/GEOmetadb.sqlite.gz
- GEOmetadb package (schema reference) — https://www.bioconductor.org/packages/release/bioc/html/GEOmetadb.html
- `bge-small-en-v1.5` — https://huggingface.co/BAAI/bge-small-en-v1.5
- E-utilities usage / rate limits — https://www.ncbi.nlm.nih.gov/books/NBK25497/

*All corpus statistics above were measured in this spike against the
GEOmetadb-derived corpus; the Track 2 measurements were collected on 2026-07-10.
They are our own measurements, not external claims.*
