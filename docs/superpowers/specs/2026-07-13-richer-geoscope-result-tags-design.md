# Richer GEOscope Result Tags Design

## Purpose

Make the GEOscope cards in the live comparison as easy to scan as the NCBI GEO
cards while preserving GEOscope's stronger visual hierarchy. Each GEOscope card
will show a compact, human-readable summary of its organism, normalized assay,
and broader GEO study type instead of often showing only a raw `NCBITaxon` ID.

This is a result-metadata and presentation change. It does not alter candidate
retrieval, ranking, filtering, or relevance behavior.

## Shared metadata contract

Add `organism_labels` to the shared MCP dataset metadata returned for both
search summaries and dataset details. Keep `organism_ids` unchanged so every
consumer retains the stable ontology identifiers used for exact filtering.

The MCP search service will derive each label from its corresponding organism
ID through the existing centralized facet-label resolver. The two arrays will
have the same order and cardinality. A known ID such as `NCBITaxon:10090` will
therefore produce `Mus musculus`; an unknown ID will use the raw ID as its label.
Blank values are already excluded by the bounded MCP result model and will not
be introduced by this change.

Centralizing label resolution avoids maintaining a second taxonomy table in the
React application and makes the readable organism names available to all MCP
consumers, not only the marketing page.

## GEOscope card composition

The frontend API schema will accept `organism_labels`, and the GEOscope result
card will construct at most three chips in this stable order:

1. **Organism:** one chip containing up to two readable organism labels joined
   with a centered dot. If more than two organisms are present, append `+N` for
   the remaining count. If readable labels are unexpectedly absent, fall back
   to the corresponding raw organism IDs.
2. **Normalized assay:** one chip containing the first normalized assay label.
3. **Study type:** one chip containing the existing GEO study type.

Missing categories are omitted without leaving empty chip elements or spacing.
Exact duplicate chip text is removed case-insensitively while retaining the
priority order above. One chip per category guarantees the three-chip cap even
for multi-organism or multi-assay records.

For example, a typical result can display:

```text
Mus musculus | RNA-seq | Expression profiling by high throughput sequencing
```

## Visual treatment

Keep the existing compact boxed layout and wrapping behavior at the bottom of
each card. Organism and assay chips represent normalized metadata and retain the
current lime fill, cobalt border, and small cobalt shadow. The broader source
study type uses a quieter paper-colored fill with a cobalt border and no shadow.

Use semantic `span` elements with category-specific classes rather than treating
metadata as source code. Long values may wrap inside their chip; they must not
overflow the card or cause horizontal page scrolling. Paired result rows will
continue stretching both source cards to the same row height.

The NCBI GEO cards and their muted tag treatment remain unchanged.

## Component and data flow

- `facets.facet_label` remains the single organism ID-to-label resolver.
- `McpSearchService` adds the resolved labels when it constructs shared dataset
  metadata.
- MCP output models expose bounded `organism_labels` alongside
  `organism_ids`.
- The frontend response schema validates the new field.
- `GEOscopeCard` uses a small deterministic helper to assemble, combine,
  de-duplicate, and cap its visible chips.
- `styles.css` distinguishes normalized chips from the source study-type chip.

No additional network requests, browser-side taxonomy map, or dependency is
required.

## Fallbacks and compatibility

- Unknown taxonomy IDs remain visible as their raw `NCBITaxon` value.
- Records without an assay label still show organism and study type when those
  values exist, fixing the one-chip appearance shown in the current comparison.
- Records missing all three categories omit the tag container entirely.
- The new shared output field is additive; current identifiers and filter inputs
  keep their existing meaning.

## Testing and verification

Backend tests will verify that:

- known organism IDs resolve to readable labels;
- unknown IDs fall back to the raw value;
- search summaries and dataset details satisfy the expanded strict output
  contract; and
- IDs and labels remain aligned.

Frontend tests will verify that:

- a GEOscope card renders organism, assay, and study-type chips;
- missing assay metadata still leaves organism and study type visible;
- multi-organism values are combined and summarized without exceeding three
  chips;
- duplicate or empty values do not create redundant chips; and
- cards with no tag metadata do not render an empty tag container.

Run the focused Python MCP and marketing API tests, the frontend Vitest suite,
and the TypeScript/Vite production build. Visually inspect at desktop and mobile
widths for chip wrapping, paired-row alignment, and horizontal overflow.

## Scope boundaries

- Do not change Elasticsearch scoring, query construction, rank ordering, or
  NCBI GEO comparison behavior.
- Do not add sample count, publication, sex, or other new card chips in this
  iteration.
- Do not change the NCBI GEO card design.
- Do not add a frontend taxonomy lookup table or another label source.
