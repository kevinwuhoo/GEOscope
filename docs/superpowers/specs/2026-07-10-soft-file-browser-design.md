# SOFT File Browser Design

## Purpose

Provide a small local web browser for the downloaded GEO SOFT files. It lets a
user find literal words in compressed SOFT files, inspect the matching series,
and switch the selected file between its stripped metadata form and its original
raw form.

The browser is intentionally independent of the semantic-search demo: it must
not load an embedding model or require a Postgres connection.

## Layout and behavior

The single-page UI has a left search sidebar and a right file viewer.

The left sidebar contains:

- a case-insensitive literal-word search input;
- a **Search raw files** checkbox; and
- a scrollable result list.

Searches use metadata-only files under `data/processed/soft_meta` by default.
When **Search raw files** is checked, they instead use the original files under
`data/raw/soft`. Each result is grouped by GSE accession and shows that
accession plus up to two short matching line snippets.

Selecting a result opens the matching metadata-only SOFT file in the right
viewer. A **Show original raw file** checkbox above the viewer switches that
same GSE to the corresponding raw family file. The two checkboxes are
independent: the left checkbox chooses the search corpus; the right checkbox
chooses which version of the selected file is displayed.

The selected file is served as streamed plain text inside an iframe. This keeps
the server from buffering decompressed raw family files, which may be very
large, while preserving the complete requested content in the viewer.

## Package and command

Add a new package at `src/geo_index/soft_browser/` and expose its `main` entry
point as:

```console
uv run geo-soft-browser
```

It starts a local `ThreadingHTTPServer` on `127.0.0.1:8001` by default. The
command accepts `--port`, `--host`, `--raw-dir`, and `--metadata-dir` overrides
for alternate local snapshots or test fixtures.

No new Python dependencies are required. The implementation uses Python's
standard library plus the installed `rg` executable.

## Server API and data flow

`GET /` returns the browser HTML.

`GET /api/search?q=<text>&raw=0|1` runs a bounded `rg` subprocess against the
chosen tree. The command uses `--search-zip` to inspect `.gz` files without
extracting them, `--json` for stable parsing, `--fixed-strings` for literal
searches, and a `*_family.soft.gz` glob. The server groups matches by source
file, extracts the GSE accession from the filename, and returns a compact JSON
array containing the GSE and snippets. Search input is passed as an argument,
never through a shell.

`GET /api/file?gse=<GSE accession>&raw=0|1` validates the accession with a
strict `GSE`-number pattern, reconstructs the expected mirrored family-file
path using the existing `ftp_bucket` and `soft_path` helpers, then streams the
gzipped text response. It never accepts a client-provided filesystem path.

The page changes the iframe source when the selection or the viewer checkbox
changes. A missing raw counterpart produces a readable not-found response;
empty queries, unavailable `rg`, malformed accessions, subprocess failures,
and no-match searches produce concise inline or HTTP errors rather than a
server crash.

## Tests

Add focused tests using temporary raw and metadata directory trees. They cover:

- expected raw and metadata family-file path resolution;
- parsing and grouping `rg --json` match output into accession/snippet results;
- safe rejection of malformed accessions and path traversal attempts;
- search endpoint selection of the raw versus metadata tree; and
- streamed file endpoint selection, content, and missing-file behavior.

The tests mock the `rg` process where appropriate, so they do not depend on a
large local corpus.

## Scope boundaries

This is a local inspection tool, not an indexer or editor. It does not write or
modify SOFT files, add authentication, index results persistently, or attempt
to render expression tables structurally. The browser shows SOFT as complete
plain text.
