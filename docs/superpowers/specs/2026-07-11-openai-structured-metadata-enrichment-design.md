---
title: OpenAI Structured Metadata Enrichment
tags: [prefect, openai, gpt-5.6, structured-output, normalization, facets]
status: awaiting-written-review
created: 2026-07-11
updated: 2026-07-11
---

# OpenAI Structured Metadata Enrichment Design

## Goal

Extend the merged Prefect SOFT pipeline with a paid, resumable enrichment stage
that makes one strict structured-output OpenAI call per ordinary GSE. The stage
extracts evidence-backed biological, experimental, geographic, and technology
claims from the canonical metadata records, while preserving deterministic
normalization and the raw SOFT-derived record as separate sources of truth.

The resulting artifacts support three uses without making the LLM authoritative
by default:

1. richer Elasticsearch text and metadata search;
2. reviewed promotion of selected values into filters and facets; and
3. reproducible study-geography and technology-adoption analyses over time.

## Decisions

1. Keep the existing canonical SOFT materializer deterministic, local, and free.
2. Add a sibling Prefect flow for OpenAI enrichment rather than calling an LLM
   inside canonical parsing tasks.
3. Use one structured-output call per compacted GSE as the normal path, not one
   call per field.
4. Use bounded sample-group shards only when a lossless compacted GSE exceeds the
   configured input budget.
5. Store LLM output in immutable sidecars; never rewrite canonical source records.
6. Use the Responses API with strict Structured Outputs and the Batch API for
   corpus execution.
7. Start with `gpt-5.6-terra` as the working pilot profile, compare
   `gpt-5.6-luna` and `gpt-5.6-sol` on the same reviewed set, and select one
   profile before a full-corpus submission.
8. Treat country and manufacturer as role-specific evidence, never as single
   unqualified fields.
9. Name the complete deterministic geography value
   `submitter_contact_country`; do not call it study location or funding country.
10. Treat actual funding attribution as a later external-enrichment project.

## Existing pipeline boundary

The merged implementation already provides the correct input boundary:

- `soft_records.py` parses each stripped family SOFT file into one canonical JSON
  record containing complete Series, Platform, and Sample attribute maps;
- platform blocks already include the available GPL metadata, so enrichment does
  not fetch GPL records separately;
- deterministic organism, sex, and assay normalizers run during canonical record
  creation;
- raw-field `embed_text` remains independent of normalized and generated values;
- `prefect_etl.py` inventories inputs, materializes missing canonical records in
  bounded tasks, and invokes the embedding artifact builder.

The new stage consumes completed files under
`data/processed/series_records/`. It performs no GEO, GPL, SRA, PubMed, or web
fetch during the initial implementation.

## Target pipeline

After canonical materialization, embeddings and structured enrichment are
independent derived branches. Elasticsearch loading depends on the canonical
record, the selected embedding artifact, and the selected extraction profile:

```text
metadata-only family SOFT
          |
          v
canonical GSE JSON (existing Prefect ETL)
          |
          +----> raw-field embedding artifacts
          |
          +----> deterministic compaction
                    |
                    v
               OpenAI Batch / Responses
                    |
                    v
          validated extraction sidecars
          |
          +----> analysis-ready tables
          |
          +----> Elasticsearch document assembly
```

The first implementation adds a standalone `geo-openai-extract` flow and CLI.
It does not refactor the existing `geo-soft-etl` flow merely to make the two
derived branches concurrent. A later top-level orchestration flow may call both
flows and the Elasticsearch loader after their contracts are stable.

## Scope

### Deterministic enrichment

Programmatic preprocessing owns facts that do not need semantic judgment:

- GSE, GSM, and GPL identifiers and relationships;
- submission, update, and parsed public-release dates;
- sample and channel counts;
- organism names and NCBITaxon IDs;
- atomic GSE study types;
- GPL technology and distribution;
- library strategy, source, and selection;
- molecule/analyte;
- conservative explicit sex normalization;
- instrument model parsed from sample/platform metadata;
- instrument vendor resolved from a reviewed model/vendor alias registry;
- raw GPL platform manufacturer;
- submitter contact country normalized to ISO 3166 country code and label; and
- characteristic-key spelling aliases while retaining every raw key and value.

For longitudinal outputs, `public_release_date` is parsed deterministically from
the raw `Series_status` value and retains that raw source. The default
`study_year` is the public-release year; if release parsing fails, it falls back
to the submission year and records `study_year_source=submission_fallback`. If
neither date parses, `study_year` is null. Last-update date is never used as the
study year because later curation can move it far from the original work.

### LLM claim domains

The OpenAI call extracts only domains that benefit from shared context:

- biospecimen class, anatomy/tissue, cell type, and cell line;
- disease, health state, diagnosis, and other biological condition;
- intervention/exposure, agent, dose, duration, timing, route, and vehicle;
- genotype, genetic perturbation, zygosity, strain, breed, ecotype, or cultivar;
- age, developmental stage, and explicitly reported demographic facts;
- detailed assay, bulk/single-cell/spatial qualification, chemistry, readout,
  and antibody/IP target;
- study design, factors, arms, and explicitly described contrasts;
- sample collection, participant origin, experimental site, and sequencing-site
  geography when explicitly supported; and
- platform, instrument, library kit, assay kit, reagent, and analysis-software
  relationships, including manufacturer/vendor and product when supported.

### Out of scope

- expression, probe, or other stripped data-table content;
- separate GPL network fetches;
- inference of sample origin from submitter contact address;
- inference of nationality or geography from ethnicity or ancestry;
- ontology IDs invented by the model;
- automatic publication of LLM claims as authoritative facets;
- changing `embed_text` to include generated synopsis or extracted labels;
- PubMed affiliation, Crossref funder, grant, acknowledgment, NIH RePORTER, or
  other funding enrichment; and
- full-corpus paid execution before reviewed pilot results and an explicit
  operator authorization.

## Deterministic input compaction

### Source units

Every promptable input value receives a stable reference:

```text
SERIES.summary.0
SERIES.overall_design.0
GPL24676.Platform_title.0
SG12.Sample_characteristics_ch1.2
SG12.Sample_extract_protocol_ch1.0
```

The source-unit registry stores the reference, entity type and accession,
attribute key, occurrence number, exact text, and SHA-256. Evidence returned by
the model must resolve to these units.

### Sample groups

The v1 preprocessor groups only samples whose complete promptable metadata is
identical after exact whitespace normalization. The signature includes sample
title/source, organism, molecule, every characteristic, treatment/growth/extract
protocols, library fields, instrument model, description, and all other fields
selected for extraction. Only the GSM accession and non-semantic storage
plumbing are excluded from the signature.

Study-local donor, participant, batch, replicate, well, and other identifiers
remain promptable and therefore prevent grouping when they differ. Although
that choice produces more groups, their co-occurrence can encode pairing and
study design. Any future compaction that summarizes or removes those identifiers
must be evaluated as a separate, potentially lossy change.

Each prompt group contains:

- stable `group_ref` and signature hash;
- sample count;
- up to five representative GSM accessions;
- complete extraction-relevant source units shared by the group; and
- a manifest reference containing the complete GSM membership.

Grouping reduces repeated metadata without dropping a rare group. It must never
collapse two groups whose extraction-relevant text differs.

### Prompt field selection

The default is to include every non-table textual SOFT field. A versioned field
policy excludes only approved PII, storage plumbing, and content explicitly
outside the extraction contract: contact names, email, phone, street address,
contributor lists, supplementary-file URLs, relation plumbing, and table
metadata. Submitter institution and country may be included; submitter country
is passed as an already-derived deterministic fact rather than raw address text.

Each compacted input manifest records every included and excluded source unit,
the field-policy version, and an exclusion reason. A field intentionally outside
the extraction contract does not make a domain incomplete. If a source unit
eligible for a domain under that contract is omitted because of budget,
preprocessing failure, or another runtime constraint, the affected domain must
be `input_incomplete`.

### Input budget and oversized records

The configured normal-path budget is 250,000 input tokens, leaving at least
22,000 tokens below the current GPT-5.6 long-context pricing boundary. The count
covers the complete serialized request: instructions, schema, source units, and
formatting overhead. Each model profile pins a compatible tokenizer; request
publication fails closed if that tokenizer is unavailable or the fully rendered
request exceeds the budget.

If the compacted GSE exceeds that budget:

1. preserve the series and platform context in every shard;
2. partition complete sample groups without splitting a group;
3. issue one structured extraction call per shard;
4. merge claims, evidence, conflicts, and domain statuses deterministically;
5. ignore shard-local synopses and build a deterministic, bounded synopsis from
   the merged validated claims, recording `synopsis_origin=deterministic_merge`;
   and
6. mark any domain whose source units could not be included as
   `input_incomplete`.

No path silently truncates the source or publishes an apparently complete
extraction from a partial input. Oversized records do not incur an extra synopsis
call.

## Structured output contract

The SDK schema is defined once with Pydantic and generates the strict JSON Schema
used in `text.format`. Every object rejects additional properties.

The LLM-authored object has seven top-level fields:

```json
{
  "synopsis": "Human single-cell RNA sequencing of lung tumors and controls.",
  "synopsis_claim_refs": ["c1", "c2"],
  "domain_status": [
    {"domain": "biospecimen", "status": "reported"}
  ],
  "evidence": [
    {
      "id": "e1",
      "source_ref": "SG12.Sample_characteristics_ch1.0",
      "quote": "lung adenocarcinoma",
      "occurrence": 0
    }
  ],
  "claims": [
    {
      "id": "c1",
      "kind": "biospecimen",
      "scope": {"level": "sample_groups", "group_refs": ["SG12"]},
      "support": "explicit",
      "evidence_refs": ["e1"],
      "value": {
        "material_class": "primary_tissue",
        "biospecimen_label": "lung adenocarcinoma tissue",
        "tissue_label": "lung",
        "cell_type_label": null,
        "cell_line_label": null
      }
    }
  ],
  "study_design": {
    "design_types": [],
    "factors": [],
    "arms": [],
    "contrasts": []
  },
  "conflicts": []
}
```

`claims.value` is a discriminated union keyed by `kind`:

- `biospecimen`
- `condition`
- `intervention`
- `genetic_context`
- `demographic`
- `assay`
- `geography`
- `technology`

Every claim has `id`, `kind`, `scope`, `support`, `evidence_refs`, and the
kind-specific `value`. Scope is one of `series`, `platforms`, `sample_groups`, or
`mixed`, with the corresponding stable references. In v1, `support` is the
literal value `explicit`: the model may normalize an explicitly reported phrase,
but it must omit claims that depend on guessing an unstated fact.

The claim payloads have these responsibilities:

| Kind | Required semantic content |
|---|---|
| `biospecimen` | material class plus the reported biospecimen, tissue/anatomy, cell type, and cell-line labels that apply |
| `condition` | condition type and label, with reported stage, grade, status, or qualifier kept separate |
| `intervention` | intervention type and agent, with separate raw dose, duration, timing, route, and vehicle components when reported |
| `genetic_context` | relationship type plus the reported gene/construct, alteration, zygosity, genotype, strain, breed, ecotype, or cultivar components |
| `demographic` | demographic attribute type and exact reported value, with parsed numeric value/range and unit when present |
| `assay` | assay label, molecular scope, bulk/single-cell/spatial qualification, readout, chemistry, and target when present |
| `geography` | geographic role, exact place text, and parsed city/region/country components when supported |
| `technology` | technology entity type and role, with separate organization, product, model, catalog, and version components when present |

All strict-schema fields are present and nullable where the source need not
report them; empty strings are rejected. Exact raw representations of numeric
facts and units are retained alongside parsed components.

The model emits supported labels and raw numeric components, not ontology IDs or
numeric confidence. In v1, `candidate_ref` is limited to small reviewed
technology registries such as vendor and instrument-model aliases supplied in
the input. Biomedical labels remain evidence-backed free text and searchable
hints; any later ontology candidate list requires its own evaluated design.

The model returns a nonempty exact quote plus its zero-based occurrence within
the referenced source unit; it does not author character offsets. Validation
resolves that occurrence and computes zero-based, end-exclusive Unicode
character offsets deterministically. A missing occurrence or an out-of-range
occurrence is a row-validation failure.

Domain status is one of:

- `reported`
- `not_reported`
- `ambiguous`
- `conflicting`
- `input_incomplete`

Exactly one status is required for each of `biospecimen`, `condition`,
`intervention`, `genetic_context`, `demographic`, `assay`, `geography`,
`technology`, and `study_design`. `not_reported` means the fact was not found in
the supplied metadata, not that it is absent in the real-world study.

Study-design types, factors, arms, and contrasts each carry their own IDs,
evidence references, and applicable group references. A contrast names its
compared arms; it cannot be created from labels that merely appear somewhere in
the same GSE.

Schema limits are 280 characters for the synopsis, 240 characters per evidence
quote, 256 claims, 512 evidence objects, 64 study arms, and 128 contrasts per
request. Hitting a limit is a failed or incomplete extraction, never silent
truncation.

Shard status merges use a fixed precedence. If any domain-eligible input was
omitted, the result is `input_incomplete`; otherwise incompatible claims yield
`conflicting`; otherwise any valid claim yields `reported`; otherwise any
ambiguous shard yields `ambiguous`; and `not_reported` is valid only when every
shard reports `not_reported`. Claims are deduplicated by kind, normalized
payload, and support; identical repeated series/platform claims are collapsed,
while applicable sample-group scopes and resolved evidence are unioned. Before
merging, every model-authored ID is prefixed with its shard ID so
references cannot collide. Incompatibilities are detected across shards as well
as within one response, and conflicting claims are retained rather than silently
choosing one.

## Geography semantics

Geography claims require an explicit role:

- `submitter_contact`
- `contact_affiliation`
- `sample_collection`
- `participant_origin`
- `experimental_site`
- `sequencing_site`

`submitter_contact_country` is deterministic and complete in the current SQLite
snapshot, but it means the country in the GSE submitting contact address. It
does not mean specimen origin, experiment location, collaborator location,
publication affiliation, funder, or funding source.

Participant ancestry, ethnicity, nationality, residence, birthplace, and sample
collection location remain distinct concepts. A geography claim is rejected if
its evidence supports only a different role.

Initial geographic analyses must be described as GEO submission activity by
submitter contact affiliation. Funding analyses require later external grant or
funder data and are not derived from contact country.

## Technology and manufacturer semantics

Technology claims require both an entity type and a role.

Entity types:

- `platform`
- `instrument`
- `library_prep_kit`
- `assay_kit`
- `reagent`
- `antibody`
- `software`
- `other_product`

Roles:

- `measurement`
- `library_preparation`
- `sample_processing`
- `target_enrichment`
- `labeling`
- `analysis`
- `other`

Direct `Platform_manufacturer` is preserved as platform provenance. Sequencing
instrument vendor/model is derived primarily from platform title or sample
instrument model because virtual high-throughput-sequencing GPL records often
omit manufacturer. Kit, reagent, antibody, and software vendor relationships
come from LLM claims only when evidence supports the product/vendor relationship,
not merely two unrelated mentions in one record.

Vendor normalization occurs after extraction through a versioned alias registry.
Raw organization, product, model, and catalog text is always retained.

## OpenAI API and model profiles

The implementation uses:

- Responses API requests;
- strict Structured Outputs through `text.format`;
- OpenAI Python SDK Pydantic parsing for synchronous tests and schema generation;
- Batch API requests targeting `/v1/responses` for corpus work;
- a 24-hour batch completion window; and
- explicit model profile keys rather than the rolling `gpt-5.6` alias.

Pilot profiles:

| Profile key | Model | Reasoning | Purpose |
|---|---|---|---|
| `gpt56_terra_low_v1` | `gpt-5.6-terra` | low | working default |
| `gpt56_terra_medium_v1` | `gpt-5.6-terra` | medium | reasoning comparison |
| `gpt56_luna_low_v1` | `gpt-5.6-luna` | low | high-volume cost challenger |
| `gpt56_sol_low_v1` | `gpt-5.6-sol` | low | quality ceiling |

Pro mode, tool calling, persisted reasoning, programmatic tool calling,
multi-agent mode, and web/file search are disabled. The task is one-shot
extraction over supplied evidence and does not benefit from those surfaces.

Every artifact records the requested model, model returned by the API,
reasoning effort, prompt version, extraction schema version, SDK version, and
usage fields. A full-corpus run uses exactly one promoted profile unless a
separately reviewed retry/adjudication policy says otherwise.

Official references:

- https://developers.openai.com/api/docs/guides/latest-model
- https://developers.openai.com/api/docs/guides/structured-outputs
- https://developers.openai.com/api/docs/guides/batch
- https://developers.openai.com/api/docs/models/gpt-5.6-sol
- https://developers.openai.com/api/docs/models/gpt-5.6-terra
- https://developers.openai.com/api/docs/models/gpt-5.6-luna

## Prefect enrichment flow

Add a standalone `geo-openai-extract` entry point backed by a Prefect flow. Its
stages are:

1. **Inventory** canonical records and existing extraction sidecars.
2. **Compact** only missing or stale records into source-unit/sample-group input
   manifests.
3. **Prepare** deterministic Batch JSONL shards and a dry-run estimate without
   reading credentials or using the network.
4. **Authorize** creation of new paid work only when both `OPENAI_API_KEY` and
   `--allow-paid-openai` are present.
5. **Lock, submit, or reconcile** request shards using persisted provider file
   and batch IDs, with deterministic submission identities.
6. **Download** completed output and error files.
7. **Validate and merge** row outputs, including oversized-record shards.
8. **Publish** per-GSE sidecars atomically.
9. **Report** counts, failures, usage, model/profile identity, and incomplete
   domains.

Batch request shards contain at most 1,000 requests, at most 1,000 distinct GSEs,
and at most 100 MB. One input file targets one extraction contract. Each request
uses a unique custom ID derived from GSE, canonical-record hash, extraction
contract, and shard number.

Before upload or batch creation, the local prototype takes an exclusive POSIX
file lock beside `openai_state.json`, reloads state while holding the lock, and
persists the provider transition before releasing it. Reconciliation and
download require credentials but not `--allow-paid-openai`, because they cannot
create new paid work. The lock is authoritative for the current shared-filesystem
deployment; distributed workers without a shared lock service are out of scope.

The flow does not occupy a Prefect worker while waiting for a provider batch.
One run may prepare/submit and return `in_progress`; a later run reconciles the
same persisted state. This makes ordinary re-execution the polling mechanism for
the prototype.

## Artifact layout

An `extraction_contract_id` is the SHA-256 of canonical JSON containing the
requested model, reasoning settings, prompt/instruction version, output schema,
compactor, field policy, and tokenizer versions. A `derivation_contract_id`
adds the grounding, alias-registry, vocabulary, QC, and facet-promotion-manifest
versions. A `run_id` identifies one operational submission or derivation run;
the canonical-record SHA-256 identifies the exact GSE input.

```text
data/processed/
  extraction_artifacts/
    <extraction_contract_id>/<run_id>/
      request_manifest.json
      requests-00001.jsonl
      openai_state.json
      openai_state.lock
      results-00001.jsonl
      errors-00001.jsonl
      run_report.json
  series_extractions/
    <extraction_contract_id>/<run_id>/GSE123nnn/
      GSE123456.<canonical_sha256>.json
  series_derivations/
    <derivation_contract_id>/<run_id>/GSE123nnn/
      GSE123456.<canonical_sha256>.json
  analytics/
    <derivation_contract_id>/<run_id>/
      study_facts.parquet
      study_geography.parquet
      study_technology.parquet
      study_claims.parquet
  active_enrichment_manifest.json
```

The published per-GSE bundle has four logical layers:

- `meta`: pipeline-authored provenance, hashes, versions, and completeness;
- `source_index`: sample-group and source-unit references used by extraction;
- `extraction`: the exact validated LLM-authored object; and
- `derived`: post-LLM grounding, normalized aliases, QC, and facet eligibility,
  stored separately with a reference to its extraction sidecar.

Final sidecars are immutable. New runs and any change to source content,
preprocessing, prompt, schema, model profile, or grounding vocabulary create a
new path and never silently overwrite provenance. The small active manifest is
published atomically and is the only mutable pointer selecting artifacts for
Elasticsearch assembly.

## Validation and failure behavior

### Structural validation

- Every claim, evidence, arm, contrast, and conflict ID is unique.
- Every reference resolves.
- Every evidence quote and occurrence resolves in the referenced source unit;
  derived offsets reproduce the quote exactly.
- Every claim has evidence.
- Sample-scoped claims reference valid deterministic groups.
- Candidate references belong to the supplied allowlist.
- `reported` requires a claim for claim domains; for `study_design`, it requires
  at least one evidence-backed design type, factor, arm, or contrast.
- `not_reported` forbids claims or study-design objects for the domain.
- `conflicting` requires a conflict object.
- `input_incomplete` is never facet eligible.
- Age, dose, duration, timing, and units retain the exact raw representation.
- Manufacturer/product and geography/role relationships require evidence for
  the relationship.
- Synopsis claim references resolve and support every substantive synopsis fact.

### Provider and state validation

- Dry-run preparation performs no credential lookup or network call.
- Creation of new provider work requires the key plus explicit paid
  authorization; reconciliation never requires paid authorization.
- Submission-state transitions occur under the exclusive run lock and are
  persisted before and after every provider transition.
- Concurrent flows cannot upload or submit the same run simultaneously.
- Resume never uploads or submits a completed shard twice.
- An uploaded file with ambiguous or missing submission identity fails closed
  until reconciliation proves exactly one matching batch.
- Result rows are joined by custom ID, never file order.
- Missing, duplicate, malformed, unexpected, refused, expired, or provider-error
  rows remain explicit failures.
- No partial run becomes active in `active_enrichment_manifest.json`.
- Successful GSE sidecars publish independently and atomically, while the run
  report remains unsuccessful until all expected rows reach an accepted terminal
  state.

## Evaluation and promotion gates

### Reviewed set

Build a 500-GSE reviewed set:

- 100 random GSEs from each of 2000-2005, 2006-2010, 2011-2015, 2016-2020,
  and 2021-2024 using seed `20260711`;
- preserve the already recorded accession selection where available;
- add a separate stress slice of the 50 largest, multi-channel, and
  metadata-diverse GSEs; and
- annotate evidence and absence, not only normalized labels.

### Model comparison

Run all four pilot profiles over the same reviewed inputs. Measure:

- per-domain precision, recall, and F1;
- evidence-reference and deterministic quote-resolution accuracy;
- correct abstention and conflict handling;
- sample-group scope accuracy;
- schema/parser/refusal/error rate;
- synopsis factual support;
- input, output, reasoning, and cached tokens;
- latency and batch completion rate; and
- cost per accepted GSE and per correct claim.

Promote one profile only after a blinded review of disagreements. Luna is chosen
when it preserves the reviewed quality bar at materially lower cost; Terra is
chosen when it provides a meaningful accuracy gain; Sol remains the ceiling
comparison unless its gain justifies corpus-wide cost.

### Operational pilot and paid gate

After profile selection, prepare and run a 1,000-GSE operational pilot. Verify
resume behavior, result alignment, artifact publication, analytics tables, and
Elasticsearch assembly. Generate a full-corpus dry-run estimate afterward.

The full-corpus submission remains a separate explicitly authorized action. No
test, default CLI invocation, or Prefect retry may authorize it implicitly.

### Facet promotion

Deterministic fields may be faceted immediately. LLM-derived values become
facet eligible only when all of these hold:

1. explicit evidence and valid scope;
2. complete input for that domain;
3. no unresolved conflict;
4. successful post-response grounding or alias normalization;
5. reviewed field-level precision sufficient for exact filtering; and
6. mapping and vocabulary versions recorded in `derived`.

The enabled set is controlled by a versioned, explicit facet-promotion manifest,
which is empty for all LLM-derived fields by default. Passing the validation
rules does not enable a facet unless the manifest names that field and mapping.

Unpromoted claims remain searchable/displayable hints and analytics rows. The
one-line synopsis is never a facet and does not enter the first embedding build.

## Elasticsearch and analytical outputs

Elasticsearch document assembly keeps separate namespaces:

```text
source.*       original canonical SOFT metadata
canonical.*    deterministic normalized values
extracted.*    evidence-backed LLM claims
facets.*       reviewed and promoted exact values
```

Every non-table SOFT key and value remains in the stored Elasticsearch
`_source`, but arbitrary keys do not become dynamic mapped fields. Each GSE also
gets three discovery-oriented raw-metadata projections:

- `source.attribute_keys`, a keyword set for key-presence queries;
- `source.attribute_values_text`, an analyzed concatenation for full-text
  retrieval; and
- `source.attributes_flat`, a `flattened` GSE-level key-to-values rollup for
  exact exploratory key/value queries.

The index mapping uses `dynamic: strict`. Verbatim per-Series, per-Platform, and
per-Sample attribute maps are stored under an `enabled: false` raw container.
This avoids mapping explosion from spelling variants
while preserving every value for display, reprocessing, and audit. Stable,
reviewed fields receive explicit typed mappings under `canonical.*`; the
flattened rollup never substitutes for sample-level co-occurrence.

The index also stores the original raw-field search text, selected embedding,
display synopsis, active extraction profile, source hashes, and retrieval build
identity. GSE-level rollups retain discovery semantics: they mean the study
contains a value somewhere. Exact within-sample intersections require a future
sample/group query surface.

The analytical Parquet tables provide reproducible inputs for:

- assay and platform transitions over time;
- array-platform and sequencing-instrument vendor adoption;
- library-kit and technology-product adoption;
- sample volume and metadata-coverage trends;
- submitter-contact-country trends; and
- explicitly reported collection or sequencing geography where available.

Every longitudinal table carries `study_year`, `study_year_source`, parsed
submission and public-release dates, and their raw source values. Default charts
use public-release year and label fallback rows so date-policy sensitivity can be
measured.

Analyses must deduplicate reused GSMs and account for nested SuperSeries when
the metric would otherwise double-count work. Submitter-country analysis is
labeled as GEO submission activity, not global research output or funding.

## Security and data minimization

- GEO metadata is public, but canonical records contain names, emails, phone
  numbers, and street addresses. Those fields are excluded from model prompts.
- Prompt inputs use only metadata needed for the approved schema.
- API keys are read from environment or secret management and never written to
  artifacts, reports, or logs.
- Logs contain GSE/custom IDs, state, counts, and bounded errors, not full prompt
  text or model output.
- Request and response JSONL artifacts are treated as data artifacts and excluded
  from source control.
- Provider request storage behavior is configured explicitly and recorded in the
  run manifest.

## Testing

Tests use fixtures and fake OpenAI clients; they never perform paid calls.

Required coverage:

- deterministic source-unit references and hashes;
- public-release parsing, study-year fallback, and date provenance;
- sample-group grouping, rare-group preservation, and membership manifests;
- exclusion of contact and unrelated plumbing fields;
- normal-path and oversized-record request construction;
- Pydantic/JSON Schema generation and additional-property rejection;
- evidence quote-occurrence resolution, derived offsets, and reference
  validation;
- domain-status consistency;
- geography-role and manufacturer/product relationship checks;
- deterministic request custom IDs and sharding;
- dry-run zero-network behavior;
- dual paid guard;
- submission-state persistence and resume;
- concurrent submission-lock exclusion;
- ambiguous submission reconciliation;
- out-of-order result alignment;
- refusal, expiry, error-file, partial-row, and malformed-output handling;
- deterministic shard merge and conflict retention;
- atomic per-GSE sidecar publication;
- extraction profile invalidation by every material version/hash input;
- immutable contract/run paths and atomic active-manifest selection;
- analytical table row/provenance integrity; and
- Elasticsearch assembly that preserves all raw keys without dynamic mapping
  explosion and never promotes ineligible claims.

## Acceptance criteria

- Existing canonical SOFT parsing and embedding tests remain unchanged and green.
- The new flow can prepare a complete dry run without credentials or network use.
- Paid submission requires both explicit authorization and `OPENAI_API_KEY`.
- Ordinary compacted GSEs produce one strict structured-output request.
- Oversized records preserve every sample group across bounded shards and expose
  incomplete domains rather than truncating silently.
- Every accepted claim is evidence-backed and scope-valid.
- Canonical records remain unchanged; extraction output is an immutable sidecar.
- All GPT-5.6 pilot profiles run against the same reviewed set before promotion.
- A 1,000-GSE operational pilot passes before full-corpus authorization.
- Contact country and technology/manufacturer roles are represented without
  semantic conflation.
- Elasticsearch and analytical tables identify the exact canonical,
  extraction, embedding, and vocabulary versions used.
- No funding claim is derived from submitter-contact geography alone.

## Revisit triggers

Revisit this design if:

- sample/group-level filtering becomes a product requirement;
- the selected GPT-5.6 profile cannot meet reviewed precision or abstention bars;
- compacted oversized records are common enough to dominate cost or complexity;
- provider Batch limits or Structured Output support change materially;
- canonical source-update detection replaces existence-only invalidation;
- PubMed/SRA/grant enrichment is authorized; or
- generated labels demonstrably improve embedding retrieval enough to justify a
  separately evaluated document-composition change.
