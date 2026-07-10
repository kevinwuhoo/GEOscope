---
title: Tissue Candidate Generation Plan
tags: [ontology, normalization, tissue, plan, prototype]
status: approved-design
created: 2026-07-10
---

# 43 · Tissue Candidate Generation Plan

← [[Home]] · extends [[22-Ontology-Normalization]] · tracked in [[40-Roadmap]]

> **Scope: v2+ normalization experiment on top of the v1 series-level index.**
> Build a deterministic, ontology-grounded candidate generator for `tissue`, then
> use bounded LLM calls only to validate ambiguous candidates. This is a prototype
> learning step, not a production curation system.

## Why this is next

The current curated `UBERON` dictionary maps about **40% of series that report a
tissue value**, while leaving 52,812 series with a reported-but-unmapped tissue
field. The current status is also series-wide: one successful value can make a
field look `mapped` even when sibling values missed. The next mapper should work
per raw value, preserve partial results, and replace the hand-maintained ontology
head with a complete local vocabulary. → [[22-Ontology-Normalization#Spike results (v1, measured)]]

## Decisions

1. **One canonical anatomy target per biological domain.** Use **UBERON** for
   animal anatomy and **Plant Ontology (PO)** for plant anatomy. UBERON is an
   integrated cross-species animal anatomy ontology; PO covers plant anatomy,
   morphology, growth, and development
   ([UBERON](https://obofoundry.org/ontology/uberon.html),
   [PO](https://obofoundry.org/ontology/po.html)). Other biological domains remain
   unmapped in this slice rather than being forced into UBERON or PO.
2. **No species-specific anatomy layer by default.** Organism remains its own
   facet. Add a species-specific vocabulary only if the tissue eval finds a
   meaningful set of query-relevant concepts genuinely absent from UBERON/PO.
3. **Candidate generation is deterministic and grounded.** Every candidate is
   an active term from a pinned ontology snapshot; scripts never mint IDs.
4. **Start lexical.** Build exact label/synonym lookup plus character/token
   similarity. Defer SapBERT/BioLORD or other semantic term embeddings until the
   lexical baseline shows a measured gap.
5. **Confidence is ordinal evidence, not a fake probability.** Store
   `accepted | pending_validation | predicted | unmapped` and
   `high | medium | low`, plus the raw matcher scores and reasons. Do not train a
   calibration model for the spike.
6. **LLMs validate candidates; they do not generate IDs.** A validator selects
   from real candidates or abstains. This follows the grounded extraction pattern
   used by SPIRES/OntoGPT rather than trusting direct ID generation
   ([SPIRES](https://pmc.ncbi.nlm.nih.gov/articles/PMC10924283/)).
7. **No graph work in this slice.** Do not build a graph database, species bridge,
   hierarchy UI, or ancestor-facet expansion. Revisit hierarchy only if the query
   eval demonstrates that parent-term expansion materially improves discovery.

## Non-goals

- Normalizing disease, cell type, cell line, or developmental stage in the same
  implementation pass.
- Sample-level search or embeddings. The index remains series-level; the existing
  within-sample co-occurrence caveat still applies. → [[24-Faceted-Search]]
- Automatically accepting every reported value. `unmapped` is a valid outcome.
- Using an LLM's self-reported confidence.
- Folding predicted values into strict facets by default.
- Selecting a production LLM provider or long-term serving architecture.

## Data contract: map values, then aggregate

Candidate generation is a pure operation over one reported value:

```json
{
  "field": "tissue",
  "raw_value": "Whole Blood (WB)",
  "organism_ids": ["NCBITaxon:9606"],
  "source_key": "tissue",
  "context": null
}
```

It returns evidence and candidates before making a final decision:

```json
{
  "normalized_value": "whole blood",
  "shape": "single_concept",
  "candidates": [
    {
      "ontology_id": "UBERON:0000178",
      "label": "blood",
      "matched_surface": "whole blood",
      "match_method": "exact_synonym",
      "lexical_score": 1.0,
      "token_coverage": 1.0,
      "field_valid": true,
      "taxon_valid": true
    }
  ],
  "selected_id": "UBERON:0000178",
  "status": "accepted",
  "evidence_strength": "high",
  "reason": "unique exact synonym"
}
```

Persist the per-value result, including the ontology and mapper versions. Only
then aggregate IDs to the GSE. Series status becomes:

| Per-value outcome | Series field status |
|---|---|
| No tissue value reported | `absent` |
| Every reportable value accepted | `mapped` |
| At least one accepted and at least one predicted/pending/unmapped | `partial` |
| Predicted values only | `predicted` |
| Validation is pending and nothing is accepted | `pending_validation` |
| Values reported but no candidate retained | `unmapped` |
| Explicit unknown tokens only | `unknown` |

This prevents one successful value from hiding sibling misses.

## Deterministic pipeline

### 1. Build a pinned ontology catalog

Download and pin one version each of UBERON and PO. Record the source URL,
release/version, and file hash. Extract:

```text
ontology_id
preferred_label
synonym text + synonym type
definition
obsolete flag
replacement ID
taxon constraints, when present
```

Create a surface index from preferred labels and synonyms. Only a unique active
preferred-label or exact-synonym match may enter the automatic high-evidence path.
Broad, narrow, and related synonyms may generate candidates but cannot auto-accept.

The batch path must use the pinned local catalog, not a live OLS request, so one
run is reproducible and an ontology update is an explicit event.

### 2. Normalize conservatively

Safe transformations:

- Unicode, case, and whitespace normalization.
- Consistent hyphen and punctuation handling.
- Quote and harmless trailing-period removal.
- Parenthetical abbreviation variants: `whole blood (WB)` → `whole blood`, `WB`.
- Carefully bounded singular/plural variants.

Always retain the raw value. Do **not** blindly remove disease, stage, cell-type,
or treatment words: `breast cancer` must not silently become `breast`.

### 3. Classify the value shape

Before matching, assign one deterministic shape:

```text
single_concept         liver
explicit_multi_value  liver; spleen
composite_concepts     breast tumor tissue
wrong_field_candidate PBMC
unknown_or_noise       not applicable
prose                  tissue obtained from the left lung
```

Split only explicit, unambiguous multi-value delimiters. Composite values and
prose remain intact and route to candidate validation/extraction.

A small cross-field sentinel vocabulary can identify likely contamination. For
example, a strong CL match for `PBMC` in the tissue slot should emit
`wrong_field_candidate`, not a weak UBERON guess.

### 4. Generate candidates in ordered passes

#### Pass A — exact

1. Unique preferred label.
2. Unique exact synonym.
3. Unique normalized variant.
4. Small curated project alias with a regression test.

#### Pass B — lexical

For exact misses, union the top candidates from:

- Character n-gram TF-IDF.
- Token overlap/coverage.
- Edit similarity for bounded spelling variants.
- Explicit abbreviation expansion.

Return at most **10** candidates after deduplication by ontology ID. Preserve every
channel's score; do not collapse them into a claimed probability. text2term is a
reference implementation for deterministic ontology candidate generation and a
possible implementation shortcut
([text2term](https://github.com/ccb-hms/ontology-mapper)).

#### Pass C — semantic, deferred

Do not add a biomedical term-embedding model initially. Add one only if reviewed
lexical misses contain true semantic equivalents rather than wrong-field values,
composites, absent ontology concepts, or noise.

### 5. Apply hard validity checks

Reject or flag candidates that are:

- Obsolete without a valid replacement.
- From the wrong ontology for the organism/domain.
- Taxon-incompatible when the ontology provides a constraint.
- Extremely broad roots that add no useful facet value.
- Supported only by an ambiguous abbreviation.
- Contradicted by another reported field or the source key.

These checks decide validity, not confidence.

### 6. Rank with stable evidence priorities

Use a stable route order rather than a hand-wavy blended confidence number:

```text
unique preferred-label match
unique exact-synonym match
curated tested alias
unique normalized exact match
lexical candidate ordered by score, margin, then ontology ID
```

`ontology_id` is the final deterministic tie-breaker so repeated runs have stable
ordering.

## Evidence and decision policy

| Evidence | Typical route | Result |
|---|---|---|
| High | Unique exact active label/synonym; all hard checks pass | `accepted` |
| Medium | Strong lexical candidate, multiple exact matches, or composite | `pending_validation` |
| Medium, validated | LLM selects a grounded candidate | `predicted` |
| Low | Weak/ambiguous candidates, invalid context, or validator rejection | `unmapped` |

Store `match_method`, raw scores, top-1/top-2 margin, token coverage, validity
flags, and the decision reason. The evidence band is an inspectable prototype
policy, not a calibrated probability.

Strict facets use `accepted` values only. An exploratory mode may include visibly
tagged `predicted` values, primarily for non-model organisms where the project
intentionally permits greater exploratory tolerance.

## LLM validation

Only medium-evidence, composite, or prose cases reach an LLM. Deduplicate and
cache by:

```text
field + normalized raw value + organism set + context hash
+ ontology version + model version + prompt version
```

The validator receives the raw value, organism, limited context, and up to 10
real candidates with labels/definitions. It must return one structured decision:

```text
select_candidate(candidate_index, evidence_span)
wrong_field
multiple_concepts
insufficient_context
none_of_the_candidates
```

It cannot return an arbitrary ontology ID. Agreement may retain a mapping as
`predicted/medium`; it never promotes an LLM-backed result to deterministic
`high`. Rejection or malformed output leaves the value unmapped.

During development, independent subagents audit samples and challenge systematic
rules. The repeatable batch path uses fixed-schema API/local-model calls with a
versioned prompt and cache. Subagent consensus is QA evidence, not production
provenance.

If prose produces no usable candidate, the LLM may extract an anatomical span;
the deterministic generator then runs again on that span before validation.

## Failure handling

- Missing or hash-mismatched ontology snapshot: fail the run before mapping.
- Unknown/obsolete candidate ID: reject; never write it to normalized columns.
- LLM timeout/provider error: retain `pending_validation` for retry; do not accept.
- Invalid LLM schema or out-of-range candidate index: reject the response.
- Multiple accepted raw values: retain all IDs and all per-value evidence.
- Partial failures: write successful values and set the series field to `partial`.

## Evaluation

Review **100–200 distinct tissue values**; no regression model is required. Sample
across:

- Human/mouse head values.
- Human/mouse long-tail values.
- Non-model animals.
- Plants.
- Composite and wrong-field values.
- Unknown, malformed, and adversarial values.

Label each candidate outcome as `correct exact`, `acceptable broader`, `wrong`,
or `should abstain`. Split repeated normalized values together so duplicates do
not leak across evaluation slices.

Report:

- Accepted precision from the reviewed sample.
- Accepted, predicted, and unmapped coverage separately.
- Pending-validation volume separately from mapped coverage.
- Value-level and series-weighted coverage.
- Wrong-field/composite detection rate.
- LLM validation call rate and cache hit rate.
- Error buckets: parser, synonym, lexical ranking, ontology gap, wrong field,
  insufficient context, validator disagreement.

The eval decides whether to add semantic term embeddings, species-specific input
vocabularies, or hierarchy expansion. It does **not** need to produce a trained
calibrator.

## Implementation slices

### Slice 1 — catalog + exact candidates

- Add versioned UBERON/PO catalog loading.
- Build preferred-label and synonym indexes.
- Implement conservative normalization and value-shape classification.
- Return stable, structured exact candidate sets.
- Add unit fixtures for `whole blood`, `brain`, `breast cancer`, `PBMC`, `leaf`,
  explicit unknowns, and multi-value inputs.

### Slice 2 — lexical tail

- Add character n-gram TF-IDF and token/edit evidence.
- Union, deduplicate, hard-filter, and rank the top 10 candidates.
- Add a candidate-report command for distinct tissue values and error buckets.

### Slice 3 — persistence + aggregation

- Persist per-value evidence and ontology/mapper versions.
- Add `partial` and `predicted` without losing `absent`, `unknown`, or `unmapped`.
- Aggregate accepted IDs back to the existing series-level columns.

### Slice 4 — bounded LLM validator

- Add the fixed input/output schema and provider-neutral adapter.
- Add cache keys, retries, `pending_validation`, and malformed-output rejection.
- Run only on deduplicated medium/composite/prose cases.

### Slice 5 — small eval and decision gate

- Review the 100–200-value stratified sample with independent audit on ambiguous
  cases.
- Measure precision, coverage, error buckets, and validator volume.
- Decide whether semantic candidates, species-specific vocabularies, or hierarchy
  behavior earn another slice.

## Suggested module boundaries

Keep `normalize.py` as orchestration/database glue and move focused behavior into:

```text
ontology_catalog.py   pinned term/synonym catalog and version metadata
tissue_candidates.py normalization, shape detection, retrieval, filtering, ranking
llm_validation.py     structured validator adapter and cache contract
```

Each module exposes a small pure-data interface so it can be tested without
Postgres, ontology downloads, or an LLM service running.

## Definition of done

- Every emitted candidate ID exists in the pinned ontology snapshot.
- Exact mappings and candidate ordering are deterministic across repeated runs.
- The mapper preserves raw values, reasons, methods, and ontology versions.
- Partial series mappings cannot be reported as fully mapped.
- LLM output is restricted to candidate selection or abstention.
- Strict facets use accepted mappings only; predicted mappings remain distinguishable.
- The reviewed sample and report make the next investment—semantic linking,
  species-specific vocabulary, hierarchy, or stopping—an evidence-based decision.

## Sources

- UBERON — https://obofoundry.org/ontology/uberon.html
- Plant Ontology — https://obofoundry.org/ontology/po.html
- text2term — https://github.com/ccb-hms/ontology-mapper · https://academic.oup.com/database/article/doi/10.1093/database/baae119/7912353
- SPIRES / OntoGPT grounding — https://pmc.ncbi.nlm.nih.gov/articles/PMC10924283/
