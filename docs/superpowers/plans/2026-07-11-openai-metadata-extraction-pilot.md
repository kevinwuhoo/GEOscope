# OpenAI Metadata Extraction Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a resumable, evidence-backed OpenAI structured-metadata extraction pilot over 500 time-stratified canonical GSE records plus 50 locally selected stress records.

**Architecture:** A new sibling Prefect flow streams complete canonical JSON records into deterministic source-unit and exact sample-group manifests, prepares strict Responses API requests, and advances OpenAI Batch runs without blocking a worker. Immutable extraction sidecars are validated and then compared across four GPT-5.6 profiles; Elasticsearch assembly, production Parquet tables, and the later 1,000-GSE operational run remain separate implementation tranches.

**Tech Stack:** Python 3.11+, Prefect 3, Pydantic 2, OpenAI Python SDK, tiktoken, ijson, pycountry, SQLite, pytest, OpenAI Responses/Structured Outputs/Batch APIs

**Design source:** `docs/superpowers/specs/2026-07-11-openai-structured-metadata-enrichment-design.md`

## Global Constraints

- Use only locally materialized canonical records under `data/processed/series_records/`; perform no GEO, GPL, SRA, PubMed, or web fetch.
- After the active local SOFT ingestion and canonical ETL finish, select 100
  deterministic random GSEs from each of 2000–2005, 2006–2010, 2011–2015,
  2016–2020, and 2021–2024 with seed `20260711`, then add 50 non-overlapping
  stress GSEs.
- Evaluate exactly `gpt56_terra_low_v1`, `gpt56_terra_medium_v1`, `gpt56_luna_low_v1`, and `gpt56_sol_low_v1` against the same selected inputs.
- Ordinary compacted GSEs produce one `/v1/responses` request; only requests above 250,000 fully rendered input tokens use whole-sample-group fallback shards.
- Set `store=false` and `truncation=disabled` on every Responses request.
- Use strict Structured Outputs generated from the Pydantic contract; tools, web search, file search, and multi-agent model features stay disabled.
- Keep canonical records and existing embedding artifacts byte-for-byte unchanged.
- Default execution is a no-credential, zero-network dry run. New paid work requires both `OPENAI_API_KEY` and `--allow-paid-openai`; reconciliation requires the key but not the paid flag.
- Every paid submission is additionally bound to the exact prepared-manifest
  SHA-256 and an operator-approved maximum dollar amount; changed requests or a
  higher maximum fail before provider access.
- Bound each local Batch JSONL file to 1,000 requests, 1,000 distinct GSEs, and 100 MiB even though the provider permits larger files.
- Persist and fsync state around provider transitions and join results by
  `custom_id`. Pilot runs never change the production
  `active_enrichment_manifest.json` pointer.
- Data artifacts remain under the already ignored `data/` tree and must never be committed.
- Logs contain only GSE/custom IDs, states, counts, usage, and bounded errors;
  they never contain full prompt text, model output, API keys, contact values,
  or street addresses.
- Do not add Elasticsearch or PyArrow dependencies in this pilot tranche.
- Do not use `claude` or `codex` as a branch-name prefix.

## File Map

- Create `src/geo_index/openai_extract_models.py`: strict structured-output types only.
- Create `src/geo_index/openai_extract_profiles.py`: model profiles, prompt, versioned field policy, pricing, aliases, and contract hashing.
- Create `src/geo_index/openai_extract_compaction.py`: streaming canonical inventory, deterministic facts, source units, exact sample groups, and spill-to-SQLite compaction.
- Create `src/geo_index/openai_extract_requests.py`: fully rendered Responses bodies, token accounting, oversized fallback sharding, JSONL files, and estimates.
- Create `src/geo_index/openai_extract_validation.py`: provider-row parsing, evidence and semantic validation, shard merging, and immutable sidecars.
- Create `src/geo_index/openai_extract_batch.py`: exclusive state lock and nonblocking upload/submit/reconcile/download lifecycle.
- Create `src/geo_index/prefect_openai_extract.py`: standalone Prefect flow and `geo-openai-extract` CLI.
- Create `src/geo_index/openai_pilot.py`: deterministic base/stress selection, smoke/remainder manifests, comparison metrics, and blinded review queue.
- Create `eval/openai_extraction/seed_accessions_20260710.tsv`: tracked copy of
  the 20 previously sampled accessions and their provenance.
- Create `tests/fixtures/openai/canonical_small.json`: compact representative canonical input.
- Create `tests/fixtures/openai/response_valid.json`: one valid raw Batch output row.
- Create focused test modules matching each new source module.
- Modify `pyproject.toml` and `uv.lock`: direct runtime dependencies and two CLI entry points.
- Modify `README.md`: operator commands, state transitions, pricing caveat, and pilot scope.
- Modify `docs/superpowers/specs/2026-07-11-openai-structured-metadata-enrichment-design.md` only to retain its approved status and verified local-era correction.
- Leave `src/geo_index/soft_records.py`, `src/geo_index/prefect_etl.py`, all embedding modules, and their tests unchanged.

---

### Task 1: Lock the strict extraction schema and profile contracts

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/geo_index/openai_extract_models.py`
- Create: `src/geo_index/openai_extract_profiles.py`
- Create: `tests/test_openai_extract_models.py`
- Create: `tests/test_openai_extract_profiles.py`

**Interfaces:**
- Produces: `ExtractionResult`, `Claim`, `DomainState`, `Evidence`, and `StudyDesign`.
- Produces: `OpenAIExtractProfile`, `get_extract_profile(key)`, `response_schema()`, and `extraction_contract_id(profile)`.
- Consumes: no repository-internal API.

- [ ] **Step 1: Add failing schema tests**

Create tests that construct one valid result, then assert:

```python
def test_schema_is_strict_and_has_every_domain() -> None:
    result = ExtractionResult.model_validate(VALID_RESULT)
    assert {item.domain for item in result.domain_status} == set(REQUIRED_DOMAINS)
    schema = ExtractionResult.model_json_schema()
    assert schema["additionalProperties"] is False

    bad = deepcopy(VALID_RESULT)
    bad["unexpected"] = True
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ExtractionResult.model_validate(bad)


def test_v1_claims_require_explicit_support() -> None:
    bad = deepcopy(VALID_RESULT)
    bad["claims"][0]["support"] = "inferred"
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(bad)
```

Also assert duplicate or missing domain states fail, empty evidence quotes fail, synopsis length is capped at 280 characters, and biomedical claims cannot carry an ontology `candidate_ref`.

- [ ] **Step 2: Run the tests and verify the import failure**

Run:

```bash
uv run pytest -q tests/test_openai_extract_models.py tests/test_openai_extract_profiles.py
```

Expected: collection fails because the two modules do not exist.

- [ ] **Step 3: Add direct dependencies and refresh the lock**

Add these direct dependencies:

```toml
"ijson>=3.4,<4",
"openai>=2,<3",
"pycountry>=24",
"pydantic>=2.13,<3",
"tiktoken>=0.12,<1",
```

Run:

```bash
uv lock
uv sync
```

Expected: `uv.lock` records the direct dependencies and `uv sync` exits zero.

- [ ] **Step 4: Implement the strict model family**

Define a shared base and the complete discriminated claim union:

```python
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

Domain = Literal[
    "biospecimen", "condition", "intervention", "genetic_context",
    "demographic", "assay", "geography", "technology", "study_design",
]
DomainStatus = Literal[
    "reported", "not_reported", "ambiguous", "conflicting", "input_incomplete",
]
REQUIRED_DOMAINS: tuple[str, ...] = (
    "biospecimen", "condition", "intervention", "genetic_context",
    "demographic", "assay", "geography", "technology", "study_design",
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DomainState(StrictModel):
    domain: Domain
    status: DomainStatus


class Evidence(StrictModel):
    id: str = Field(pattern=r"^e[1-9][0-9]*$")
    source_ref: str
    quote: str = Field(min_length=1, max_length=240)
    occurrence: int = Field(ge=0)


class ClaimScope(StrictModel):
    level: Literal["series", "platforms", "sample_groups", "mixed"]
    platform_refs: list[str]
    group_refs: list[str]


class MeasuredValue(StrictModel):
    raw: str
    value: float | None
    low: float | None
    high: float | None
    unit: str | None


class BiospecimenValue(StrictModel):
    material_class: str
    biospecimen_label: str
    tissue_label: str | None
    cell_type_label: str | None
    cell_line_label: str | None


class ConditionValue(StrictModel):
    condition_type: Literal["disease", "health_state", "diagnosis", "phenotype", "infection", "other"]
    condition_label: str
    stage: str | None
    grade: str | None
    status: str | None
    qualifier: str | None


class InterventionValue(StrictModel):
    intervention_type: str
    agent: str
    dose: MeasuredValue | None
    duration: MeasuredValue | None
    timing: str | None
    route: str | None
    vehicle: str | None


class GeneticContextValue(StrictModel):
    relationship_type: str
    gene_or_construct: str | None
    alteration: str | None
    zygosity: str | None
    genotype: str | None
    strain_or_breed: str | None
    ecotype_or_cultivar: str | None


class DemographicValue(StrictModel):
    attribute_type: Literal["age", "developmental_stage", "sex", "gender", "ethnicity", "ancestry", "other"]
    raw_value: str
    normalized_label: str | None
    measured: MeasuredValue | None


class AssayValue(StrictModel):
    assay_label: str
    molecular_scope: str | None
    scale: Literal["bulk", "single_cell", "spatial", "other"] | None
    readout: str | None
    chemistry: str | None
    target: str | None


class GeographyValue(StrictModel):
    role: Literal[
        "submitter_contact", "contact_affiliation", "sample_collection",
        "participant_origin", "experimental_site", "sequencing_site",
    ]
    place_text: str
    city: str | None
    region: str | None
    country_label: str | None
    country_code: str | None


class TechnologyValue(StrictModel):
    entity_type: Literal[
        "platform", "instrument", "library_prep_kit", "assay_kit",
        "reagent", "antibody", "software", "other_product",
    ]
    role: Literal[
        "measurement", "library_preparation", "sample_processing",
        "target_enrichment", "labeling", "analysis", "other",
    ]
    organization: str | None
    product: str | None
    model: str | None
    catalog: str | None
    version: str | None
    candidate_ref: str | None


class ClaimBase(StrictModel):
    id: str = Field(pattern=r"^c[1-9][0-9]*$")
    scope: ClaimScope
    support: Literal["explicit"]
    evidence_refs: list[str] = Field(min_length=1)


class BiospecimenClaim(ClaimBase):
    kind: Literal["biospecimen"]
    value: BiospecimenValue


class ConditionClaim(ClaimBase):
    kind: Literal["condition"]
    value: ConditionValue


class InterventionClaim(ClaimBase):
    kind: Literal["intervention"]
    value: InterventionValue


class GeneticContextClaim(ClaimBase):
    kind: Literal["genetic_context"]
    value: GeneticContextValue


class DemographicClaim(ClaimBase):
    kind: Literal["demographic"]
    value: DemographicValue


class AssayClaim(ClaimBase):
    kind: Literal["assay"]
    value: AssayValue


class GeographyClaim(ClaimBase):
    kind: Literal["geography"]
    value: GeographyValue


class TechnologyClaim(ClaimBase):
    kind: Literal["technology"]
    value: TechnologyValue


Claim = Annotated[
    Union[
        BiospecimenClaim, ConditionClaim, InterventionClaim,
        GeneticContextClaim, DemographicClaim, AssayClaim,
        GeographyClaim, TechnologyClaim,
    ],
    Field(discriminator="kind"),
]


class DesignItem(StrictModel):
    id: str
    label: str
    group_refs: list[str]
    evidence_refs: list[str] = Field(min_length=1)


class StudyContrast(StrictModel):
    id: str
    label: str
    arm_refs: list[str] = Field(min_length=2)
    group_refs: list[str]
    evidence_refs: list[str] = Field(min_length=1)


class StudyDesign(StrictModel):
    design_types: list[DesignItem]
    factors: list[DesignItem]
    arms: list[DesignItem]
    contrasts: list[StudyContrast]


class Conflict(StrictModel):
    id: str
    domain: Domain
    claim_refs: list[str]
    design_refs: list[str]
    evidence_refs: list[str] = Field(min_length=1)
    description: str

    @model_validator(mode="after")
    def require_conflicting_objects(self) -> "Conflict":
        if len(self.claim_refs) + len(self.design_refs) < 2:
            raise ValueError("conflict must reference at least two objects")
        if self.domain == "study_design" and len(self.design_refs) < 2:
            raise ValueError("study_design conflicts require two design_refs")
        return self


class ExtractionResult(StrictModel):
    synopsis: str = Field(max_length=280)
    synopsis_claim_refs: list[str]
    domain_status: list[DomainState] = Field(min_length=9, max_length=9)
    evidence: list[Evidence] = Field(max_length=512)
    claims: list[Claim] = Field(max_length=256)
    study_design: StudyDesign
    conflicts: list[Conflict]

    @model_validator(mode="after")
    def require_domain_statuses(self) -> "ExtractionResult":
        domains = [item.domain for item in self.domain_status]
        if len(domains) != len(set(domains)) or set(domains) != set(REQUIRED_DOMAINS):
            raise ValueError("domain_status must contain every required domain exactly once")
        return self
```

Add validators rejecting empty strings after trimming and rejecting `candidate_ref` on every claim kind except `technology`.

- [ ] **Step 5: Implement the four immutable profiles and contract hash**

Use:

```python
@dataclass(frozen=True)
class OpenAIExtractProfile:
    key: str
    model: str
    reasoning_effort: Literal["low", "medium"]
    max_output_tokens: int
    encoding_name: str
    batch_input_usd_per_million: Decimal
    batch_cached_input_usd_per_million: Decimal
    batch_output_usd_per_million: Decimal


PROFILES = {
    "gpt56_terra_low_v1": OpenAIExtractProfile(
        "gpt56_terra_low_v1", "gpt-5.6-terra", "low", 32768,
        "o200k_base", Decimal("1.25"), Decimal("0.125"), Decimal("7.5"),
    ),
    "gpt56_terra_medium_v1": OpenAIExtractProfile(
        "gpt56_terra_medium_v1", "gpt-5.6-terra", "medium", 32768,
        "o200k_base", Decimal("1.25"), Decimal("0.125"), Decimal("7.5"),
    ),
    "gpt56_luna_low_v1": OpenAIExtractProfile(
        "gpt56_luna_low_v1", "gpt-5.6-luna", "low", 32768,
        "o200k_base", Decimal("0.5"), Decimal("0.05"), Decimal("3"),
    ),
    "gpt56_sol_low_v1": OpenAIExtractProfile(
        "gpt56_sol_low_v1", "gpt-5.6-sol", "low", 32768,
        "o200k_base", Decimal("2.5"), Decimal("0.25"), Decimal("15"),
    ),
}
```

Store `PROMPT_VERSION = "geo_extract_v1"`, `SCHEMA_VERSION = "geo_extract_schema_v1"`, `COMPACTOR_VERSION = "geo_compactor_v1"`, `FIELD_POLICY_VERSION = "geo_field_policy_v1"`, `TOKENIZER_VERSION = "o200k_base_v1"`, and the full extraction instructions in this module. Compute `extraction_contract_id` as SHA-256 over canonical compact JSON containing the selected profile, all version strings, the instruction SHA, and `ExtractionResult.model_json_schema()`.

Use this exact v1 instruction text:

```python
REQUEST_INSTRUCTIONS = """You extract structured metadata from one NCBI GEO GSE record.

Use only the supplied deterministic facts and source units. Never use outside
knowledge. Emit a claim only when the value or relationship is explicitly
supported by cited source text. Semantic normalization of an explicitly stated
phrase is allowed; guessing an unstated fact is not.

For every claim, cite one or more evidence objects. Each evidence object must
name a supplied source_ref, copy a nonempty exact substring no longer than 240
characters, and give the zero-based occurrence of that substring in the source
unit. Scope claims to series, platform_refs, and/or sample group_refs exactly as
supported. Do not turn values found in different sample groups into one
within-sample relationship.

Return exactly one status for every required domain. reported requires a claim
for that domain, except study_design where it requires at least one supported
design item. not_reported means the supplied metadata does not report the fact;
it is not a real-world absence claim. Use ambiguous for unclear text,
conflicting with a conflict object for incompatible explicit statements, and
input_incomplete only when the input manifest marks eligible source content as
omitted.

Keep disease, health state, diagnosis, intervention, genetic context,
demographics, biospecimen, anatomy, cell type, cell line, assay, and technology
as distinct concepts. Preserve exact raw dose, duration, timing, age, and unit
text alongside parsed values.

Geography claims require one explicit role: submitter_contact,
contact_affiliation, sample_collection, participant_origin, experimental_site,
or sequencing_site. Never infer sample collection, participant origin,
experiment location, nationality, or funding from a submitter address,
ethnicity, ancestry, contributor name, institution name, or publication.

Technology claims require both an entity_type and role. Cite evidence that
supports the product/vendor relationship, not merely two unrelated mentions.
candidate_ref may select only a supplied technology candidate. Do not invent or
emit ontology IDs for biomedical labels.

Create a factual one-line synopsis no longer than 280 characters. Every
substantive synopsis fact must be supported by synopsis_claim_refs. When a value
cannot be found, use the domain status rather than fabricating a value.
"""
```

- [ ] **Step 6: Verify exact request-policy facts**

Tests must assert all four profiles resolve, `gpt-5.6` without a suffix is absent, tiktoken loads `o200k_base`, pricing matches the official Batch table dated 2026-07-11, and a one-character prompt or schema change changes the contract ID.

Run:

```bash
uv run pytest -q tests/test_openai_extract_models.py tests/test_openai_extract_profiles.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit the contract**

```bash
git add pyproject.toml uv.lock src/geo_index/openai_extract_models.py src/geo_index/openai_extract_profiles.py tests/test_openai_extract_models.py tests/test_openai_extract_profiles.py
git commit -m "feat: define OpenAI extraction contract"
```

---

### Task 2: Stream canonical metadata into deterministic compacted manifests

**Files:**
- Create: `src/geo_index/openai_extract_compaction.py`
- Create: `tests/fixtures/openai/canonical_small.json`
- Create: `tests/test_openai_extract_compaction.py`

**Interfaces:**
- Consumes: `FIELD_POLICY_VERSION` and versioned country/vendor aliases.
- Produces: `CanonicalRecordRef`, `SourceUnit`, `SampleGroup`, `CompactedStudy`, `inventory_selected_records(...)`, and `compact_record(...)`.

- [ ] **Step 1: Write failing compaction tests**

Cover:

```python
def test_exact_sample_grouping_preserves_design_identifiers(tmp_path: Path) -> None:
    compacted = compact_record(FIXTURE, tmp_path / "work")
    assert [group.sample_count for group in compacted.sample_groups] == [1, 1]
    assert any("donor: D1" in unit.text for unit in compacted.source_units)
    assert any("donor: D2" in unit.text for unit in compacted.source_units)


def test_field_policy_records_every_exclusion(tmp_path: Path) -> None:
    compacted = compact_record(FIXTURE, tmp_path / "work")
    reasons = {item.key: item.reason for item in compacted.excluded_units}
    assert reasons["Series_contact_email"] == "pii"
    assert reasons["Series_sample_id"] == "relationship_plumbing"
    assert "Series_summary" not in reasons


def test_public_release_and_contact_country_are_deterministic(tmp_path: Path) -> None:
    compacted = compact_record(FIXTURE, tmp_path / "work")
    assert compacted.deterministic.public_release_date == "2024-01-03"
    assert compacted.deterministic.study_year == 2024
    assert compacted.deterministic.study_year_source == "public_release"
    assert compacted.deterministic.submitter_contact_country_code == "US"
```

Also test stable source references and hashes, repeated values and occurrence numbers, whitespace-only equivalence, differing batch/well/donor IDs preventing grouping, full GSM membership written to JSONL, unknown non-table keys included, PII excluded, Unicode preserved, and canonical file bytes unchanged.

- [ ] **Step 2: Run the red tests**

```bash
uv run pytest -q tests/test_openai_extract_compaction.py
```

Expected: import failure because the compaction module is absent.

- [ ] **Step 3: Define immutable compaction values**

Use:

```python
@dataclass(frozen=True)
class CanonicalRecordRef:
    gse: str
    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class SourceUnit:
    source_ref: str
    entity_type: Literal["series", "platform", "sample_group"]
    entity_ref: str
    key: str
    occurrence: int
    text: str
    sha256: str


@dataclass(frozen=True)
class ExcludedUnit:
    entity_type: str
    entity_ref: str
    key: str
    occurrence: int
    reason: Literal["pii", "relationship_plumbing", "file_plumbing", "table_metadata", "out_of_scope"]


@dataclass(frozen=True)
class SampleGroup:
    group_ref: str
    signature_sha256: str
    sample_count: int
    representative_gsms: tuple[str, ...]
    source_units: tuple[SourceUnit, ...]


@dataclass(frozen=True)
class DeterministicFacts:
    submission_date: str | None
    public_release_date: str | None
    study_year: int | None
    study_year_source: Literal["public_release", "submission_fallback", "missing"]
    platform_ids: tuple[str, ...]
    sample_count: int
    organisms: tuple[str, ...]
    organism_ids: tuple[str, ...]
    study_types: tuple[str, ...]
    molecules: tuple[str, ...]
    library_strategies: tuple[str, ...]
    library_sources: tuple[str, ...]
    library_selections: tuple[str, ...]
    sex_ids: tuple[str, ...]
    assay_categories: tuple[str, ...]
    characteristic_key_aliases: tuple[tuple[str, str], ...]
    submitter_contact_country_raw: str | None
    submitter_contact_country_code: str | None
    submitter_contact_country_label: str | None
    platform_manufacturers: tuple[str, ...]
    instrument_models: tuple[str, ...]
    instrument_vendors: tuple[str, ...]


@dataclass(frozen=True)
class CompactedStudy:
    record: CanonicalRecordRef
    deterministic: DeterministicFacts
    series_units: tuple[SourceUnit, ...]
    platform_units: tuple[SourceUnit, ...]
    sample_groups: tuple[SampleGroup, ...]
    excluded_units: tuple[ExcludedUnit, ...]
    membership_manifest: Path
    compactor_version: str
    field_policy_version: str

    @property
    def source_units(self) -> tuple[SourceUnit, ...]:
        sample_units = tuple(
            unit for group in self.sample_groups for unit in group.source_units
        )
        return self.series_units + self.platform_units + sample_units
```

- [ ] **Step 4: Implement streaming reads and spill-backed grouping**

Use `ijson.kvitems` and `ijson.items` with separate file passes for root scalars, `series_attributes`, `platforms.item`, and `samples.item`. Compute the canonical SHA-256 with a 1 MiB byte loop. Never call `json.load` on a production canonical record.

Give each concurrent task its own workspace at
`<artifacts_root>/<contract_id>/<run_id>/compaction/<bucket>/<gse>.<canonical_sha256>/`.
Create `groups.sqlite` inside that GSE-specific directory with:

```sql
CREATE TABLE sample_group (
  signature_sha256 TEXT PRIMARY KEY,
  group_json TEXT NOT NULL,
  sample_count INTEGER NOT NULL,
  representatives_json TEXT NOT NULL
);
CREATE TABLE membership (
  signature_sha256 TEXT NOT NULL,
  gsm TEXT NOT NULL,
  PRIMARY KEY (signature_sha256, gsm)
);
```

For each sample, filter promptable attributes, normalize only CRLF and surrounding whitespace, serialize the complete promptable sample map with sorted keys and compact separators, hash that exact serialization, and upsert the group. Do not strip donor, participant, batch, replicate, well, title, or characteristic values. Emit groups sorted by signature hash and assign `SG000001`-style references; emit complete membership as JSONL using atomic replace.

- [ ] **Step 5: Implement the versioned field policy**

Include every non-table textual field unless an exact rule excludes it. Encode these initial exclusions with a reason:

```python
PII_KEY_RE = re.compile(
    r"^(Series|Platform|Sample)_contact_"
    r"(name|email|phone|fax|address|zip|postal_code)"
    r"(?:_ch[0-9]+)?(?:_[0-9]+)?$",
    re.IGNORECASE,
)
RELATIONSHIP_KEYS = {
    "Series_geo_accession", "Series_sample_id", "Series_platform_id",
    "Platform_geo_accession", "Sample_geo_accession", "Sample_platform_id",
}
FILE_KEY_RE = re.compile(
    r"^(Series|Platform|Sample)_(supplementary_file|relation)(?:_[0-9]+)?$",
    re.IGNORECASE,
)
OUT_OF_SCOPE_KEY_RE = re.compile(
    r"^(Series|Platform|Sample)_contributor(?:_[0-9]+)?$",
    re.IGNORECASE,
)
TABLE_KEY_FRAGMENTS = ("table_begin", "table_end", "data_row_count")
```

Keep contact institution, department, city, state, and country. Record every omitted occurrence in `excluded_units`.
Apply the PII/file/contributor patterns to Series, Platform, and Sample keys and
test channel-suffixed and numbered variants such as
`Platform_contact_email`, `Sample_contact_address_ch1`, and
`Sample_supplementary_file_1`.

- [ ] **Step 6: Implement deterministic dates, country, and technology facts**

Parse `Series_status` only when it exactly matches `Public on %b %d %Y`. Use release year first, submission year second, and never last-update year. Resolve `Series_contact_country` with pycountry plus a reviewed alias map containing at least `USA`, `United States`, `UK`, `South Korea`, `Russia`, and `Taiwan`; unresolved values retain raw text and null code.

Copy the existing canonical platform/sample relationships, counts, organisms and
NCBITaxon IDs, atomic study types, library fields, molecules, conservative sex
IDs, and assay categories into `DeterministicFacts` after strict type checks.
Use the existing `normalize.KEY_SYNONYMS` only to emit
`characteristic_key_aliases`; retain every raw key/value in source units.

Preserve all `Platform_manufacturer` and `Sample_instrument_model` values. Resolve instrument vendors only through a versioned alias map for unambiguous model prefixes such as Illumina, Ion Torrent, Oxford Nanopore, PacBio, and BGI/MGI. Unknown models retain raw text and no vendor.

- [ ] **Step 7: Run compaction tests, including a synthetic large stream**

```bash
uv run pytest -q tests/test_openai_extract_compaction.py
```

Expected: all tests pass and the large fixture test confirms peak in-memory sample objects are bounded independently of sample count.

- [ ] **Step 8: Commit compaction**

```bash
git add src/geo_index/openai_extract_compaction.py tests/fixtures/openai/canonical_small.json tests/test_openai_extract_compaction.py
git commit -m "feat: compact canonical metadata for extraction"
```

---

### Task 3: Prepare token-bounded Responses requests and dry-run estimates

**Files:**
- Create: `src/geo_index/openai_extract_requests.py`
- Create: `tests/test_openai_extract_requests.py`

**Interfaces:**
- Consumes: `CompactedStudy`, `OpenAIExtractProfile`, `ExtractionResult`, and extraction instructions.
- Produces: `ExtractionRequest`, `RequestShard`, `RequestEstimate`, `build_study_requests(...)`, and `prepare_request_files(...)`.

- [ ] **Step 1: Write failing request tests**

Assert an ordinary GSE produces one row shaped exactly as:

```python
assert row == {
    "custom_id": expected_custom_id,
    "method": "POST",
    "url": "/v1/responses",
    "body": {
        "model": "gpt-5.6-terra",
        "instructions": REQUEST_INSTRUCTIONS,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": expected_payload}]}],
        "reasoning": {"effort": "low"},
        "max_output_tokens": 32768,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "geo_metadata_extraction_v1",
                "strict": True,
                "schema": ExtractionResult.model_json_schema(),
            }
        },
        "store": False,
        "truncation": "disabled",
    },
}
```

Add tests for canonical JSON serialization, full schema/instruction/payload token accounting, deterministic custom IDs, no tools, complete source units, whole-group oversize partitioning, repeated series/platform context, no silent truncation, an individually oversized group failing closed, 1,000-row and 100 MiB file boundaries, one model per file, and expected/worst-case cost math.

- [ ] **Step 2: Run the red tests**

```bash
uv run pytest -q tests/test_openai_extract_requests.py
```

Expected: import failure.

- [ ] **Step 3: Define request and estimate values**

```python
@dataclass(frozen=True)
class ExtractionRequest:
    gse: str
    canonical_sha256: str
    shard_index: int
    shard_count: int
    custom_id: str
    body: dict[str, object]
    rendered_input_tokens: int


@dataclass(frozen=True)
class RequestShard:
    index: int
    path: Path
    sha256: str
    request_count: int
    distinct_gse_count: int
    bytes: int
    custom_ids: tuple[str, ...]


@dataclass(frozen=True)
class RequestEstimate:
    extraction_contract_id: str
    request_manifest_sha256: str
    profile_key: str
    gse_count: int
    request_count: int
    oversized_gse_count: int
    input_tokens: int
    expected_output_tokens: int
    maximum_output_tokens: int
    expected_cost_usd: Decimal
    maximum_cost_usd: Decimal
    shards: tuple[RequestShard, ...]
```

- [ ] **Step 4: Render and count the complete request**

Serialize the source registry, deterministic facts, included/excluded manifest
summary, and sample groups as canonical JSON in the user input. Use
`tiktoken.get_encoding(profile.encoding_name)` and encode the complete canonical
JSON serialization of the request body so instructions, input, schema, and
formatting keys all contribute to the conservative count. Refuse publication
when the tokenizer cannot load. Use `MAX_RENDERED_INPUT_TOKENS = 250_000` and do
not call an OpenAI counting endpoint.

The user payload has this exact top-level shape, with dataclasses serialized to
plain JSON values and all lists in stable reference order:

```python
payload = {
    "input_contract": {
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "compactor_version": COMPACTOR_VERSION,
        "field_policy_version": FIELD_POLICY_VERSION,
    },
    "gse": compacted.record.gse,
    "canonical_sha256": compacted.record.sha256,
    "deterministic": asdict(compacted.deterministic),
    "completeness": {
        "eligible_omitted_domains": (
            ["study_design"] if shard_count > 1 else []
        ),
        "excluded_counts_by_reason": excluded_counts(compacted.excluded_units),
    },
    "technology_candidates": technology_candidates(compacted),
    "series_source_units": [asdict(unit) for unit in compacted.series_units],
    "platform_source_units": [asdict(unit) for unit in compacted.platform_units],
    "sample_groups": [
        {
            "group_ref": group.group_ref,
            "sample_count": group.sample_count,
            "representative_gsms": list(group.representative_gsms),
            "source_units": [asdict(unit) for unit in group.source_units],
        }
        for group in selected_groups
    ],
}
```

The membership-manifest path and excluded PII values never enter the request.

- [ ] **Step 5: Implement whole-group fallback partitioning**

First render every group in one request. When it exceeds the limit, greedily add groups in stable `group_ref` order until the next group would exceed the limit, publish the current partition, and retry the next group with repeated series/platform context. If context plus one complete group exceeds the limit, raise `OversizedSampleGroupError` with GSE, group reference, and token count.

For a multi-shard GSE, record `study_design` as cross-group incomplete because
no single request observes every sample group simultaneously. Shards still
extract group-local design evidence, but the merged GSE-level study-design
status is forced to `input_incomplete` and is not eligible for exact comparison
or faceting.

Prefix custom IDs with `geoextract-` and derive their remaining identity from extraction contract, GSE, canonical SHA, and zero-based shard index. Keep every custom ID below the provider's documented length limit.

- [ ] **Step 6: Implement deterministic JSONL publication and cost ranges**

Write at most 1,000 requests, 1,000 GSEs, or 100 MiB per file using temp file, flush, fsync, and replace. Reject one row above 100 MiB. Remove only unpublished stale request files in the same run workspace.

Report two output scenarios per profile:

- expected: 8,000 output tokens per request;
- maximum: `profile.max_output_tokens` per request.

Use exact rendered input tokens at uncached Batch input price for a conservative estimate and the profile's Batch output price for both scenarios.
Compute `request_manifest_sha256` over the canonical manifest payload excluding
that hash field, then write the hash into the final manifest. Submission
recomputes it from disk before comparing operator approval.

- [ ] **Step 7: Run request tests**

```bash
uv run pytest -q tests/test_openai_extract_requests.py
```

Expected: all tests pass.

- [ ] **Step 8: Commit request preparation**

```bash
git add src/geo_index/openai_extract_requests.py tests/test_openai_extract_requests.py
git commit -m "feat: prepare bounded OpenAI extraction requests"
```

---

### Task 4: Validate provider rows and publish immutable sidecars

**Files:**
- Create: `src/geo_index/openai_extract_validation.py`
- Create: `tests/fixtures/openai/response_valid.json`
- Create: `tests/test_openai_extract_validation.py`

**Interfaces:**
- Consumes: raw output/error JSONL, expected request inventory, `ExtractionResult`, and compacted source indexes.
- Produces: `ValidatedExtraction`, `RowFailure`, `RunValidationError`, `parse_batch_rows(...)`, `validate_extraction(...)`, `merge_study_shards(...)`, and `publish_sidecar(...)`.

- [ ] **Step 1: Write failing provider-wrapper tests**

Cover out-of-order successes, separate error-file rows, duplicate/missing/unexpected custom IDs, non-200 status, response body error, incomplete response, refusal content, zero or multiple assistant output texts, malformed JSON, Pydantic failure, and expired partial output. Assert all row failures aggregate before one `RunValidationError` and no active manifest changes.
Also cover missing/negative usage fields, requested-versus-returned model
provenance, cached/reasoning token extraction, and exact Decimal cost math
without double charging reasoning tokens.

- [ ] **Step 2: Write failing semantic tests**

Use exact source units to assert:

- evidence quote plus occurrence resolves and derived Python character offsets reproduce the quote;
- missing, ambiguous, or out-of-range occurrences fail;
- every evidence, claim, synopsis, arm, contrast, and conflict reference resolves;
- every contrast's group scope resolves and study-design conflicts reference at
  least two design objects;
- each claim has evidence and valid scope;
- `reported` requires a claim, or a nonempty study-design object for `study_design`;
- `not_reported` forbids corresponding claims/design objects;
- `conflicting` requires a conflict;
- geography role and technology product/vendor relationship are supported by the cited evidence;
- biomedical `candidate_ref` is rejected;
- `input_incomplete` remains ineligible for facets.

- [ ] **Step 3: Run the red validation module**

```bash
uv run pytest -q tests/test_openai_extract_validation.py
```

Expected: import failure.

- [ ] **Step 4: Parse raw Batch wrappers without SDK parse helpers**

Define the validated wrapper before parsing:

```python
@dataclass(frozen=True)
class UsageRecord:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int
    actual_cost_usd: Decimal


@dataclass(frozen=True)
class ValidatedExtraction:
    custom_id: str
    gse: str
    shard_index: int
    requested_model: str
    returned_model: str
    provider_request_id: str
    response_id: str
    usage: UsageRecord
    extraction: ExtractionResult
    derived_evidence_offsets: tuple[dict[str, int | str], ...]
```

Validate each JSONL line as:

```python
def extract_structured_text(row: Mapping[str, object]) -> str:
    response = require_mapping(row, "response")
    if response.get("status_code") != 200:
        raise RowValidationError("non-200 response")
    body = require_mapping(response, "body")
    if body.get("status") != "completed" or body.get("error") is not None:
        raise RowValidationError("response body not completed")
    if body.get("incomplete_details") is not None:
        raise RowValidationError("incomplete response")
    texts: list[str] = []
    for item in require_list(body, "output"):
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        for content in require_list(item, "content"):
            if content.get("type") == "refusal":
                raise RowValidationError("model refusal")
            if content.get("type") == "output_text":
                texts.append(require_string(content, "text"))
    if len(texts) != 1:
        raise RowValidationError("expected exactly one assistant output_text")
    return texts[0]
```

Join the union of output and error rows by `custom_id`; never use file order or synchronous `output_parsed`.
Require nonnegative `usage.input_tokens`, cached-input detail, output tokens,
reasoning-output detail, and total tokens from the response body. Persist the
requested and returned models plus provider request/response IDs. Calculate
actual cost from uncached input, cached input, and output tokens using the
profile rates; reasoning tokens are already included in output tokens and are
reported separately without double charging.

- [ ] **Step 5: Resolve evidence and semantic invariants**

Resolve `quote` with `occurrence` using non-overlapping exact substring matches in the source unit. Store computed `start` and `end` under pipeline-authored derived evidence, leaving the exact LLM object unchanged. Implement the complete invariant list from the tests before a row becomes accepted.

- [ ] **Step 6: Merge oversized GSE shards deterministically**

Prefix every model-authored ID with `sNNN:` internally before resolving
references. Deduplicate semantically identical claims by kind, normalized
payload, and support; union evidence and sample-group scope. After merging,
renumber evidence and claims deterministically back to `e1..eN` and `c1..cN`,
rewriting every synopsis/conflict/design reference; retain original shard IDs in
pipeline-authored provenance. Merge status in this order: `input_incomplete`,
`conflicting`, `reported`, `ambiguous`, `not_reported`. Detect cross-shard
incompatible claims. Ignore shard synopses and build a bounded deterministic
synopsis from ranked assay, organism, biospecimen, condition, and intervention
claims with `synopsis_origin="deterministic_merge"`.

- [ ] **Step 7: Publish immutable sidecars**

Publish only to:

```text
data/processed/series_extractions/<contract_id>/<run_id>/<bucket>/<GSE>.<canonical_sha256>.json
```

The sidecar contains `meta`, `source_index`, and exact `extraction` plus derived
evidence offsets/QC. `meta` records requested/returned model, provider
request/response IDs, all usage fields, actual cost, profile/prompt/schema/SDK
versions, run/contract IDs, and source hashes. Write with temp, flush, fsync,
and `os.replace` only when the destination does not already exist; an existing
path must hash-identically or fail. Do not update
`active_enrichment_manifest.json` in this task.

- [ ] **Step 8: Run validation tests**

```bash
uv run pytest -q tests/test_openai_extract_validation.py
```

Expected: all tests pass.

- [ ] **Step 9: Commit validation**

```bash
git add src/geo_index/openai_extract_validation.py tests/fixtures/openai/response_valid.json tests/test_openai_extract_validation.py
git commit -m "feat: validate OpenAI extraction results"
```

---

### Task 5: Add the locked, resumable, nonblocking Batch lifecycle

**Files:**
- Create: `src/geo_index/openai_extract_batch.py`
- Create: `tests/test_openai_extract_batch.py`

**Interfaces:**
- Consumes: `RequestEstimate`, request JSONL files, and validation/publishing functions.
- Produces: `BatchRunResult`, `OpenAIProvider`, `prepare_run_state(...)`, and `advance_batch_run(...)`.

- [ ] **Step 1: Build fake provider tests**

Define a fake with:

```python
class FakeOpenAIProvider:
    def upload_batch_file(self, path: Path) -> str:
        self.uploads.append(path)
        return f"file-{len(self.uploads)}"

    def create_batch(self, input_file_id: str, metadata: dict[str, str]) -> dict[str, object]:
        self.creates.append((input_file_id, metadata))
        return {"id": f"batch-{len(self.creates)}", "status": "validating"}

    def list_batches(self) -> Iterable[dict[str, object]]:
        return tuple(self.listed_batches)

    def retrieve_batch(self, batch_id: str) -> dict[str, object]:
        return self.retrieved[batch_id]

    def download_file(self, file_id: str) -> bytes:
        return self.downloads[file_id]
```

Tests must cover:

- prepare mode never reads `OPENAI_API_KEY` or constructs a provider;
- submit mode rejects the paid flag before key/client access;
- submit mode rejects a missing key;
- submit mode rejects a wrong prepared-manifest SHA or an approved dollar cap
  below the manifest's maximum before client construction;
- reconcile mode resumes with a key and no paid flag;
- exclusive lock prevents two submitters;
- upload and batch IDs persist before the next transition;
- crash-after-create reconciles through a fully paginated unique submission token;
- zero or multiple matches fail closed;
- completed shards are not uploaded or submitted twice;
- nonterminal provider status returns immediately as `in_progress`;
- output and error files download once and retain SHA-256;
- expired/failed/cancelled and partial completed runs never publish a complete
  run manifest;
- state/request identity mismatch fails closed.

- [ ] **Step 2: Run red lifecycle tests**

```bash
uv run pytest -q tests/test_openai_extract_batch.py
```

Expected: import failure.

- [ ] **Step 3: Define provider and result boundaries**

```python
class OpenAIProvider(Protocol):
    def upload_batch_file(self, path: Path) -> str: ...
    def create_batch(self, input_file_id: str, metadata: dict[str, str]) -> Mapping[str, object]: ...
    def list_batches(self) -> Iterable[Mapping[str, object]]: ...
    def retrieve_batch(self, batch_id: str) -> Mapping[str, object]: ...
    def download_file(self, file_id: str) -> bytes: ...


@dataclass(frozen=True)
class BatchRunResult:
    status: Literal["prepared", "submitted", "in_progress", "completed", "failed"]
    contract_id: str
    run_id: str
    request_count: int
    completed_count: int
    failed_count: int
    sidecars: tuple[Path, ...]
    errors: tuple[str, ...]
```

The real adapter uses `OpenAI().files.create(file=handle, purpose="batch")`, `batches.create(endpoint="/v1/responses", completion_window="24h")`, paginated `batches.list()`, `batches.retrieve()`, and `files.content()`.

- [ ] **Step 4: Implement durable state and exclusive transitions**

Use `fcntl.flock(handle, fcntl.LOCK_EX)` on `openai_state.lock`. Under the lock:
reload and validate state, process each eligible request-file transition in
stable shard order, persist transition intent before its provider call, persist
the call result, and fsync the file and directory. Release the lock only after
every eligible shard has been processed or one ambiguous transition has failed
closed.

Per request file persist:

```json
{
  "request_sha256": "hex",
  "custom_ids": ["geoextract-..."],
  "input_file_id": null,
  "submission_token": "contract-run-shard-token",
  "batch_id": null,
  "status": "prepared",
  "request_counts": null,
  "output_file_id": null,
  "error_file_id": null,
  "output_sha256": null,
  "error_sha256": null,
  "last_provider_error": null
}
```

Persist the unique submission token before `batches.create` and include contract, run, shard, token, and input file ID in Batch metadata. When create outcome is ambiguous, paginate all batches and accept exactly one matching token plus endpoint/input-file identity.

- [ ] **Step 5: Implement execution modes**

Use `mode: Literal["prepare", "submit", "reconcile"]`:

- `prepare`: local artifacts only, no environment lookup;
- `submit`: requires key, paid flag, approved manifest identity, and cost cap;
  it uploads and creates a Batch for every pending request file before returning;
- `reconcile`: may retrieve/download existing state with key but must never upload or create.

Submit returns only after every request file has a persisted `batch_id` or the
run has failed closed. Reconcile checks every existing batch and downloads every
newly available terminal file, then returns. Neither mode sleeps or occupies a
Prefect worker while waiting for provider completion.

- [ ] **Step 6: Validate, publish, and close only complete runs**

Validate every locally available terminal row and publish each accepted per-GSE
sidecar independently, even when another row fails. Publish the immutable
complete-run manifest only when every request shard is terminal and the entire
expected GSE inventory succeeded. Preserve request/state/result/error files for
any failure. Do not update the production `active_enrichment_manifest.json`;
promotion belongs to the later Elasticsearch tranche.

- [ ] **Step 7: Run lifecycle tests**

```bash
uv run pytest -q tests/test_openai_extract_batch.py
```

Expected: all tests pass without network access.

- [ ] **Step 8: Commit lifecycle**

```bash
git add src/geo_index/openai_extract_batch.py tests/test_openai_extract_batch.py
git commit -m "feat: add resumable OpenAI batch lifecycle"
```

---

### Task 6: Expose the standalone Prefect flow and operator CLI

**Files:**
- Create: `src/geo_index/prefect_openai_extract.py`
- Create: `tests/test_prefect_openai_extract.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Consumes: a selected-accession manifest, profile key, run ID, canonical root, and artifact root.
- Produces: `OpenAIExtractReport`, `geo_openai_extract(...)`, and `main(argv)`.

- [ ] **Step 1: Write failing flow/CLI tests**

Test `geo_openai_extract.fn(...)` and monkeypatched task submissions. Assert:

- selected accessions are inventoried once and missing canonical records fail before requests;
- compaction tasks are bounded by configured workers;
- every future resolves and per-GSE failures aggregate;
- `prepare` prints estimates and returns zero without credentials/network;
- `submit` passes paid authorization only when the flag exists;
- `reconcile` omits the paid flag and can return `in_progress` with exit zero;
- terminal validation failures return nonzero;
- report publication is atomic;
- canonical file SHA-256 values before and after the flow are identical.

- [ ] **Step 2: Run the red flow tests**

```bash
uv run pytest -q tests/test_prefect_openai_extract.py
```

Expected: import failure.

- [ ] **Step 3: Implement report and Prefect boundaries**

```python
@dataclass(frozen=True)
class OpenAIExtractReport:
    status: Literal["prepared", "submitted", "in_progress", "completed", "failed"]
    profile_key: str
    contract_id: str
    run_id: str
    request_manifest_sha256: str | None
    selected_gses: int
    compacted_gses: int
    requests: int
    oversized_gses: int
    expected_cost_usd: str | None
    maximum_cost_usd: str | None
    sidecars: int
    failures: tuple[dict[str, str], ...]

    @property
    def succeeded(self) -> bool:
        return self.status != "failed"
```

Create `compact-selected-gse` Prefect tasks with local retries only around deterministic file work. Keep provider create/reconcile inside the locked lifecycle without Prefect automatic retries.

- [ ] **Step 4: Implement explicit CLI modes**

Register:

```toml
geo-openai-extract = "geo_index.prefect_openai_extract:main"
```

CLI arguments:

```text
--selection-manifest PATH
--profile {gpt56_terra_low_v1,gpt56_terra_medium_v1,gpt56_luna_low_v1,gpt56_sol_low_v1}
--run-id TEXT
--mode {prepare,submit,reconcile}
--records-root PATH
--artifacts-root PATH
--workers INT
--allow-paid-openai
--approval-file PATH
```

Reject `--approval-file` outside submit mode. In submit mode require the paid
flag and an approval entry matching profile, run ID, exact prepared-manifest
SHA-256, and a per-run cap greater than or equal to that manifest's hard
maximum; also verify the approval's aggregate cap. Default mode is `prepare`.
Print one compact JSON report containing the manifest SHA and expected/maximum
costs.

- [ ] **Step 5: Run flow tests and refresh the lock**

```bash
uv lock
uv run pytest -q tests/test_prefect_openai_extract.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit orchestration**

```bash
git add pyproject.toml uv.lock src/geo_index/prefect_openai_extract.py tests/test_prefect_openai_extract.py
git commit -m "feat: orchestrate OpenAI extraction with Prefect"
```

---

### Task 7: Build the reproducible 550-GSE selection and comparison packet

**Files:**
- Create: `src/geo_index/openai_pilot.py`
- Create: `tests/test_openai_pilot.py`
- Create: `eval/openai_extraction/seed_accessions_20260710.tsv`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Produces: `select_pilot(...)`, `build_phase_manifest(...)`, `compare_pilot(...)`, and `main(argv)`.
- Consumes: canonical filenames, local summary JSONL, optional historical 20-GSE TSV, completed sidecars, and run reports.

- [ ] **Step 1: Write failing selection tests**

Create a small synthetic inventory spanning all five eras. Assert:

- exactly 100 base GSEs per real era and exactly 50 non-overlapping stress GSEs;
- base rank is `sha256("20260711:" + gse)` after forced eligible historical selections;
- input order does not affect selection;
- every selected accession has a canonical file;
- manifest records GSE, cohort, era/category, rank/reason, date source, canonical path, canonical SHA, file bytes, and selection version;
- repeated execution is byte-identical;
- 10-record smoke phase contains one GSE per era plus five stress GSEs;
- remainder contains the other 540 exactly once.

- [ ] **Step 2: Write failing comparison tests**

Given four tiny completed profile outputs, assert the report contains profile-blinded labels, structural success, refusals/errors, actual input/output/reasoning/cached tokens, actual cost, evidence resolution, claim counts by domain, pairwise normalized-claim agreement, consensus/disagreement counts, and a deterministic review queue. Assert no precision/recall/F1 is claimed until a human annotation file is supplied.

- [ ] **Step 3: Run the red pilot tests**

```bash
uv run pytest -q tests/test_openai_pilot.py
```

Expected: import failure.

- [ ] **Step 4: Implement base selection from local coverage**

Intersect canonical filenames with `data/raw/geo_series_summaries.jsonl` and use
`pdat` as the release-date source. Use these exact bins after freezing the
caught-up canonical inventory:

```python
ERA_BINS = (
    ("2000-2005", 2000, 2005),
    ("2006-2010", 2006, 2010),
    ("2011-2015", 2011, 2015),
    ("2016-2020", 2016, 2020),
    ("2021-2024", 2021, 2024),
)
SELECTION_SEED = "20260711"
BASE_PER_ERA = 100
STRESS_COUNT = 50
```

Copy the 20 accessions and seed provenance from
`data/processed/geometadb_spot_check_2026-07-10/selection.tsv` into the tracked
seed file, with no study metadata. Force-include eligible tracked accessions in
their era, then hash-rank remaining sorted accessions to fill 100. Record the
frozen canonical inventory count, accession-list SHA-256, source catalog
SHA-256, and per-year coverage in the manifest; fail if any era has fewer than
100 eligible local canonical records. For the final 550 selections, stream the
canonical `Series_status` value and require its parsed public date to equal the
local eSummary `pdat`; fail the selection on any mismatch rather than silently
changing strata.

The tracked TSV is exactly:

```text
gse	selection_seed	source
GSE91074	20260710	geometadb_spot_check
GSE258232	20260710	geometadb_spot_check
GSE54935	20260710	geometadb_spot_check
GSE75041	20260710	geometadb_spot_check
GSE132310	20260710	geometadb_spot_check
GSE223430	20260710	geometadb_spot_check
GSE164836	20260710	geometadb_spot_check
GSE183328	20260710	geometadb_spot_check
GSE144347	20260710	geometadb_spot_check
GSE87546	20260710	geometadb_spot_check
GSE130832	20260710	geometadb_spot_check
GSE118349	20260710	geometadb_spot_check
GSE255852	20260710	geometadb_spot_check
GSE173275	20260710	geometadb_spot_check
GSE183647	20260710	geometadb_spot_check
GSE213933	20260710	geometadb_spot_check
GSE122226	20260710	geometadb_spot_check
GSE166847	20260710	geometadb_spot_check
GSE52890	20260710	geometadb_spot_check
GSE71940	20260710	geometadb_spot_check
```

- [ ] **Step 5: Implement bounded stress selection**

Build a candidate pool from:

- 200 largest canonical file sizes;
- 200 highest `n_samples` from the local summary JSONL;
- 200 highest sample counts among SQLite GSEs having any `gsm.channel_count > 1`.

Stream only that union through Task 2 compaction to compute distinct Series/Platform/Sample keys and exact sample-group count. Excluding the base 500, take with stable numeric-GSE ties:

- 15 largest bytes;
- 15 highest sample counts;
- 10 multi-channel records;
- 10 highest metadata-diversity scores.

Deduplicate in that order and backfill each short category from its own ranked candidates, then from a composite percentile rank. Record every chosen category and metric.

- [ ] **Step 6: Implement phase manifests and blinded comparison**

The smoke phase is the first hash-ranked GSE from each base era plus the top stress GSE from each stress category, with deterministic dedup/backfill to 10. Remainder is the other 540.

Blind profile labels by sorting `sha256("20260711:blind:" + profile_key)` and assigning A–D. Normalize claims for agreement using kind, casefolded value payload, and scope. Emit:

```text
data/processed/openai_pilot/selection.json
data/processed/openai_pilot/smoke.json
data/processed/openai_pilot/remainder.json
data/processed/openai_pilot/run_matrix.json
data/processed/openai_pilot/comparison.json
data/processed/openai_pilot/review_queue.jsonl
data/processed/openai_pilot/review_template.jsonl
```

The run matrix maps every profile and phase to exact contract ID, run ID,
request-manifest SHA-256, selection/inventory SHA-256, and request files; stale
runs under the artifact root are never discovered implicitly. The review
template accepts `gse`, `domain`, `gold_claims`, `missing_claims`,
`unsupported_claim_refs`, `scope_error_claim_refs`, and `reviewer_notes`.
Compute precision/recall/F1 only when those fields have been reviewed.

- [ ] **Step 7: Register and test the pilot CLI**

Register:

```toml
geo-openai-pilot = "geo_index.openai_pilot:main"
```

Subcommands are `select`, `phase`, `runs`, `approve`, `compare`, and `score`.
`runs` builds the exact run matrix from explicit profile/run pairs. `approve`
writes an immutable approval containing matrix SHA, phase, selection SHA,
approved aggregate maximum, and per-run manifest hashes/caps; it refuses an
aggregate amount below the summed hard maximum. Each repeated `--run` value is
`<phase>:<profile_key>=<run_id>` and duplicate phase/profile pairs fail. Run:

```bash
uv lock
uv run pytest -q tests/test_openai_pilot.py
```

Expected: all tests pass.

- [ ] **Step 8: Commit pilot tooling**

```bash
git add pyproject.toml uv.lock src/geo_index/openai_pilot.py tests/test_openai_pilot.py eval/openai_extraction/seed_accessions_20260710.tsv
git commit -m "feat: add reproducible OpenAI extraction pilot"
```

---

### Task 8: Verify locally, document the runbook, and execute the staged pilot

**Files:**
- Modify: `README.md`
- Test: all new focused modules
- Test: full `tests/`
- Generate only under `data/processed/openai_pilot/`, `data/processed/extraction_artifacts/`, and `data/processed/series_extractions/`

**Interfaces:**
- Consumes: all completed implementation tasks.
- Produces: a verified local dry run, then smoke and remainder provider runs, validated sidecars, and comparison artifacts.

- [ ] **Step 1: Document exact operator commands and state semantics**

Document selection, prepare, submit, reconcile, comparison, and score commands. State that prepare is offline; submit creates paid Batch work; reconcile is networked but cannot create paid work; Batch pricing is read from the versioned profile table and must be rechecked against `https://developers.openai.com/api/docs/pricing` before submission.

- [ ] **Step 2: Run focused tests**

```bash
uv run pytest -q \
  tests/test_openai_extract_models.py \
  tests/test_openai_extract_profiles.py \
  tests/test_openai_extract_compaction.py \
  tests/test_openai_extract_requests.py \
  tests/test_openai_extract_validation.py \
  tests/test_openai_extract_batch.py \
  tests/test_prefect_openai_extract.py \
  tests/test_openai_pilot.py
```

Expected: all focused tests pass with zero network calls.

- [ ] **Step 3: Run the full repository suite**

```bash
uv run pytest -q
```

Expected: all tests pass; no existing canonical/embedding behavior changes.

- [ ] **Step 4: Wait for the existing ingestion and catch up canonical ETL**

Do not start another SOFT fetch. Confirm the existing `soft_meta` writer has
finished by checking that its file count is stable and that no fetch process is
reported in the operator's terminal. Then run:

```bash
uv run geo-soft-etl
```

Require a successful `soft_etl_report.json` with zero parse failures. Snapshot
the sorted canonical accession list and its SHA-256. Do not select while the
canonical count or accession-list hash is changing.

- [ ] **Step 5: Generate and verify the real selection**

```bash
uv run geo-openai-pilot select \
  --records-root data/processed/series_records \
  --summary-jsonl data/raw/geo_series_summaries.jsonl \
  --sqlite data/external/GEOmetadb.sqlite \
  --historical-selection eval/openai_extraction/seed_accessions_20260710.tsv \
  --output data/processed/openai_pilot/selection.json

uv run geo-openai-pilot phase \
  --selection data/processed/openai_pilot/selection.json \
  --phase smoke \
  --output data/processed/openai_pilot/smoke.json

uv run geo-openai-pilot phase \
  --selection data/processed/openai_pilot/selection.json \
  --phase remainder \
  --output data/processed/openai_pilot/remainder.json
```

Verify 500 base records, 100 per era, 50 distinct stress records, 10 smoke
records, 540 remainder records, zero missing canonical paths, and a frozen
inventory hash matching the caught-up ETL snapshot.

- [ ] **Step 6: Recheck pricing, then prepare all four smoke runs offline**

Before request preparation, compare profile rates to the official Batch pricing
table. If rates or model capabilities changed, update the profile constants and
contract tests first; do not mutate an already prepared manifest. Then run
`mode prepare` for every profile using run IDs:

```text
pilot-20260711-smoke-gpt56-terra-low-v1
pilot-20260711-smoke-gpt56-terra-medium-v1
pilot-20260711-smoke-gpt56-luna-low-v1
pilot-20260711-smoke-gpt56-sol-low-v1
```

```bash
profiles=(
  gpt56_terra_low_v1
  gpt56_terra_medium_v1
  gpt56_luna_low_v1
  gpt56_sol_low_v1
)
for profile in $profiles; do
  env -u OPENAI_API_KEY uv run geo-openai-extract \
    --selection-manifest data/processed/openai_pilot/smoke.json \
    --profile "$profile" \
    --run-id "pilot-20260711-smoke-${profile//_/-}" \
    --mode prepare
done
```

Confirm zero network, 10 selected GSEs per profile, request/token/file counts,
expected and maximum dollar estimates, and no request above 250,000 tokens.
Each immutable request manifest records the retrieval date, rates, contract ID,
and its own SHA-256.

- [ ] **Step 7: Review and bind the smoke paid boundary**

Sum expected and hard-maximum smoke costs across all four prepared manifests and
report the exact amounts and manifest hashes to the operator. Continue only
after explicit approval of that numeric maximum. Store the approved per-profile
manifest hashes and caps in
`data/processed/openai_pilot/smoke_paid_approval.json`; a changed manifest or
higher cap requires a new approval. Do not submit if any request uses the >272K
price multiplier.

Build the exact smoke run matrix:

```bash
uv run geo-openai-pilot runs \
  --selection data/processed/openai_pilot/smoke.json \
  --artifacts-root data/processed \
  --run smoke:gpt56_terra_low_v1=pilot-20260711-smoke-gpt56-terra-low-v1 \
  --run smoke:gpt56_terra_medium_v1=pilot-20260711-smoke-gpt56-terra-medium-v1 \
  --run smoke:gpt56_luna_low_v1=pilot-20260711-smoke-gpt56-luna-low-v1 \
  --run smoke:gpt56_sol_low_v1=pilot-20260711-smoke-gpt56-sol-low-v1 \
  --output data/processed/openai_pilot/run_matrix.json
```

After the operator supplies the approved numeric maximum, write the bound
approval with:

```bash
uv run geo-openai-pilot approve \
  --run-matrix data/processed/openai_pilot/run_matrix.json \
  --phase smoke \
  --approved-total-max-cost-usd "$approved_total_max_cost_usd" \
  --output data/processed/openai_pilot/smoke_paid_approval.json
```

- [ ] **Step 8: Submit the 10-record smoke phase for all four profiles**

This step requires `OPENAI_API_KEY` in the executing environment:

```bash
profiles=(
  gpt56_terra_low_v1
  gpt56_terra_medium_v1
  gpt56_luna_low_v1
  gpt56_sol_low_v1
)
for profile in $profiles; do
  uv run geo-openai-extract \
    --selection-manifest data/processed/openai_pilot/smoke.json \
    --profile "$profile" \
    --run-id "pilot-20260711-smoke-${profile//_/-}" \
    --mode submit \
    --allow-paid-openai \
    --approval-file data/processed/openai_pilot/smoke_paid_approval.json
done
```

Record every provider file and batch ID; never print the key.

- [ ] **Step 9: Reconcile smoke without creating more work**

```bash
profiles=(
  gpt56_terra_low_v1
  gpt56_terra_medium_v1
  gpt56_luna_low_v1
  gpt56_sol_low_v1
)
for profile in $profiles; do
  uv run geo-openai-extract \
    --selection-manifest data/processed/openai_pilot/smoke.json \
    --profile "$profile" \
    --run-id "pilot-20260711-smoke-${profile//_/-}" \
    --mode reconcile
done
```

Re-run after provider completion. Require all 40 expected GSE/profile outputs
to validate, zero missing/duplicate/unexpected rows, zero refusals, exact
evidence resolution, and no partial complete-run manifest before continuing.

- [ ] **Step 10: Complete the blinded smoke semantic gate**

Generate a smoke-only blinded packet:

```bash
uv run geo-openai-pilot compare \
  --selection data/processed/openai_pilot/smoke.json \
  --run-matrix data/processed/openai_pilot/run_matrix.json \
  --output-root data/processed/openai_pilot/smoke_review
```

A human reviewer checks all 40 blinded GSE/profile outputs against their cited
source units and completes the smoke review template. Stop and revise the
prompt/schema if any prompt contains excluded PII, any geography or
manufacturer relationship is unsupported, or the same unsupported-claim,
scope, or abstention failure appears in two or more GSEs. Proceed only when the
review file records no such blocker.

- [ ] **Step 11: Prepare and amount-approve the 540-record remainder**

Prepare the remainder offline:

```bash
profiles=(
  gpt56_terra_low_v1
  gpt56_terra_medium_v1
  gpt56_luna_low_v1
  gpt56_sol_low_v1
)
for profile in $profiles; do
  env -u OPENAI_API_KEY uv run geo-openai-extract \
    --selection-manifest data/processed/openai_pilot/remainder.json \
    --profile "$profile" \
    --run-id "pilot-20260711-remainder-${profile//_/-}" \
    --mode prepare
done
```

Rebuild the exact matrix with all eight runs:

```bash
uv run geo-openai-pilot runs \
  --selection data/processed/openai_pilot/selection.json \
  --artifacts-root data/processed \
  --run smoke:gpt56_terra_low_v1=pilot-20260711-smoke-gpt56-terra-low-v1 \
  --run smoke:gpt56_terra_medium_v1=pilot-20260711-smoke-gpt56-terra-medium-v1 \
  --run smoke:gpt56_luna_low_v1=pilot-20260711-smoke-gpt56-luna-low-v1 \
  --run smoke:gpt56_sol_low_v1=pilot-20260711-smoke-gpt56-sol-low-v1 \
  --run remainder:gpt56_terra_low_v1=pilot-20260711-remainder-gpt56-terra-low-v1 \
  --run remainder:gpt56_terra_medium_v1=pilot-20260711-remainder-gpt56-terra-medium-v1 \
  --run remainder:gpt56_luna_low_v1=pilot-20260711-remainder-gpt56-luna-low-v1 \
  --run remainder:gpt56_sol_low_v1=pilot-20260711-remainder-gpt56-sol-low-v1 \
  --output data/processed/openai_pilot/run_matrix.json
```

Report the exact expected and hard-maximum remainder totals. After the operator
explicitly approves the numeric maximum, write:

```bash
uv run geo-openai-pilot approve \
  --run-matrix data/processed/openai_pilot/run_matrix.json \
  --phase remainder \
  --approved-total-max-cost-usd "$approved_total_max_cost_usd" \
  --output data/processed/openai_pilot/remainder_paid_approval.json
```

- [ ] **Step 12: Submit and reconcile the approved remainder**

```bash
profiles=(
  gpt56_terra_low_v1
  gpt56_terra_medium_v1
  gpt56_luna_low_v1
  gpt56_sol_low_v1
)
for profile in $profiles; do
  uv run geo-openai-extract \
    --selection-manifest data/processed/openai_pilot/remainder.json \
    --profile "$profile" \
    --run-id "pilot-20260711-remainder-${profile//_/-}" \
    --mode submit \
    --allow-paid-openai \
    --approval-file data/processed/openai_pilot/remainder_paid_approval.json
done
for profile in $profiles; do
  uv run geo-openai-extract \
    --selection-manifest data/processed/openai_pilot/remainder.json \
    --profile "$profile" \
    --run-id "pilot-20260711-remainder-${profile//_/-}" \
    --mode reconcile
done
```

Re-run reconcile after provider completion. Require 2,160 validated remainder
GSE/profile outputs plus the 40 smoke outputs, subject only to explicitly
reported oversized fallback request counts.

- [ ] **Step 13: Generate the full comparison and blinded review packet**

```bash
uv run geo-openai-pilot compare \
  --selection data/processed/openai_pilot/selection.json \
  --run-matrix data/processed/openai_pilot/run_matrix.json \
  --output-root data/processed/openai_pilot
```

Verify actual token/cost totals match accepted provider rows, the profile key is
absent from blinded review rows, structural/evidence metrics exist for all four
profiles, and precision/recall/F1 are marked unavailable until review
annotations exist.

- [ ] **Step 14: Complete human annotations and score model quality**

Human reviewers adjudicate the 500-GSE blinded queue, including evidence-backed
gold claims and explicit absence for every required domain. After saving the
completed file as `data/processed/openai_pilot/review_completed.jsonl`, run:

```bash
uv run geo-openai-pilot score \
  --comparison data/processed/openai_pilot/comparison.json \
  --annotations data/processed/openai_pilot/review_completed.jsonl \
  --output data/processed/openai_pilot/scored_comparison.json
```

Require per-domain precision, recall, F1, abstention, scope accuracy, and a
blinded disagreement audit before selecting a promoted profile.

- [ ] **Step 15: Re-run verification after real outputs**

```bash
uv run pytest -q
git status --short
```

Expected: tests pass; generated `data/` artifacts are ignored; only intended
README/source/test/lock/eval changes are tracked.

- [ ] **Step 16: Commit the runbook and report the pilot state**

```bash
git add README.md
git commit -m "docs: document OpenAI extraction pilot"
```

Report commit IDs, focused/full test counts, selection hash and era counts,
per-profile requests/tokens/cost/status, validation failures, smoke-gate result,
comparison artifact paths, and human-review completion state.

## Official OpenAI References

- `https://developers.openai.com/api/docs/guides/structured-outputs`
- `https://developers.openai.com/api/docs/guides/batch`
- `https://developers.openai.com/api/docs/pricing`
- `https://developers.openai.com/api/docs/models/gpt-5.6-terra`
- `https://developers.openai.com/api/docs/models/gpt-5.6-luna`
- `https://developers.openai.com/api/docs/models/gpt-5.6-sol`
