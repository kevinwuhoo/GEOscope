"""Parse stripped GEO family SOFT into deterministic canonical GSE records."""

from __future__ import annotations

import gzip
import json
import os
import re
from collections import Counter, OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .normalize import KEY_SYNONYMS, map_organisms, map_sex_field, normalize_assay_fields


SCHEMA_VERSION = 1
GSE_RE = re.compile(r"^GSE([1-9][0-9]*)$")
GSM_RE = re.compile(r"^GSM([1-9][0-9]*)$")
GPL_RE = re.compile(r"^GPL([1-9][0-9]*)$")
SOFT_NAME_RE = re.compile(r"^(GSE[1-9][0-9]*)_family\.soft\.gz$")


class SoftParseError(ValueError):
    """A metadata-only SOFT file cannot produce a complete canonical record."""


AttributeMap = OrderedDict[str, list[str]]


@dataclass(frozen=True)
class RecordJob:
    gse: str
    source: Path
    destination: Path
    soft_root: Path


@dataclass(frozen=True)
class DiscoveryResult:
    discovered: int
    skipped: int
    jobs: tuple[RecordJob, ...]


@dataclass(frozen=True)
class MaterializeResult:
    gse: str
    destination: Path
    created: bool


@dataclass(frozen=True)
class MaterializeFailure:
    gse: str
    source: Path
    error: str


@dataclass(frozen=True)
class BatchResult:
    created_gses: tuple[str, ...]
    skipped_gses: tuple[str, ...]
    failures: tuple[MaterializeFailure, ...]


def _accession_key(value: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", value)
    if not match:
        raise ValueError(f"invalid GEO accession: {value}")
    return match.group(1), int(match.group(2))


def record_path(records_root: Path, gse: str) -> Path:
    """Return the one canonical path for ``gse`` under ``records_root``."""
    normalized = gse.upper()
    match = GSE_RE.fullmatch(normalized)
    if not match:
        raise ValueError(f"invalid GSE accession: {gse}")
    digits = match.group(1)
    bucket = f"GSE{digits[:-3]}nnn" if len(digits) > 3 else "GSEnnn"
    return records_root / bucket / f"{normalized}.json"


def discover_records(soft_root: Path, records_root: Path) -> DiscoveryResult:
    """Inventory inputs once and select jobs using destination existence only."""
    sources = list(soft_root.rglob("*_family.soft.gz"))
    jobs: list[RecordJob] = []
    skipped = 0
    seen: set[str] = set()
    for source in sources:
        match = SOFT_NAME_RE.fullmatch(source.name)
        if not match:
            continue
        gse = match.group(1)
        if gse in seen:
            raise ValueError(f"duplicate SOFT input for {gse}")
        seen.add(gse)
        destination = record_path(records_root, gse)
        if destination.exists():
            skipped += 1
            continue
        jobs.append(RecordJob(gse, source, destination, soft_root))
    jobs.sort(key=lambda job: _accession_key(job.gse))
    return DiscoveryResult(
        discovered=len(sources),
        skipped=skipped,
        jobs=tuple(jobs),
    )


def discover_missing(soft_root: Path, records_root: Path) -> list[RecordJob]:
    """Return numeric-GSE-sorted jobs whose canonical destinations are absent."""
    return list(discover_records(soft_root, records_root).jobs)


def materialize_record(job: RecordJob) -> MaterializeResult:
    """Parse and atomically publish one missing record."""
    if job.destination.exists():
        return MaterializeResult(job.gse, job.destination, created=False)

    record = parse_soft_record(job.source, soft_root=job.soft_root)
    if record["gse"] != job.gse:
        raise SoftParseError(
            f"parsed accession {record['gse']} does not match job {job.gse}"
        )
    job.destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = job.destination.with_suffix(job.destination.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(
                record,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, job.destination)
    finally:
        temporary.unlink(missing_ok=True)
    return MaterializeResult(job.gse, job.destination, created=True)


def materialize_batch(jobs: Sequence[RecordJob]) -> BatchResult:
    """Materialize a bounded batch while retaining per-record failures."""
    created: list[str] = []
    skipped: list[str] = []
    failures: list[MaterializeFailure] = []
    for job in jobs:
        try:
            result = materialize_record(job)
        except Exception as exc:  # noqa: BLE001 - batch isolation is intentional
            failures.append(
                MaterializeFailure(
                    gse=job.gse,
                    source=job.source,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        (created if result.created else skipped).append(job.gse)
    return BatchResult(tuple(created), tuple(skipped), tuple(failures))


def _distinct_sorted(values: list[str]) -> list[str]:
    return sorted({value for value in values if value})


def _accessions_sorted(values: list[str], pattern: re.Pattern[str]) -> list[str]:
    distinct = set(values)
    for value in distinct:
        if not pattern.fullmatch(value):
            raise SoftParseError(f"invalid GEO accession {value!r}")
    return sorted(distinct, key=_accession_key)


def _values(attributes: Mapping[str, list[str]], prefix: str) -> list[str]:
    values: list[str] = []
    for key, repeated in attributes.items():
        if key.startswith(prefix):
            values.extend(repeated)
    return values


def _first(attributes: Mapping[str, list[str]], key: str) -> str:
    values = attributes.get(key, [])
    return values[0] if values else ""


def _joined(attributes: Mapping[str, list[str]], key: str) -> str:
    return "\n".join(value for value in attributes.get(key, []) if value)


def _date(attributes: Mapping[str, list[str]], key: str) -> str | None:
    raw = _first(attributes, key)
    if not raw:
        return None
    for format_string in ("%b %d %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, format_string).date().isoformat()
        except ValueError:
            continue
    raise SoftParseError(f"invalid GEO date {key}={raw!r}")


def _characteristic(raw: str) -> dict[str, str]:
    name, separator, value = raw.partition(":")
    if not separator:
        return {"name": "", "value": raw.strip(), "raw": raw}
    return {"name": name.strip(), "value": value.strip(), "raw": raw}


def _sample_record(header_gsm: str, attributes: AttributeMap) -> dict[str, object]:
    accessions = attributes.get("Sample_geo_accession", [])
    if not accessions:
        raise SoftParseError(f"sample block {header_gsm} is missing !Sample_geo_accession")
    if any(accession != header_gsm for accession in accessions):
        raise SoftParseError(
            f"sample header {header_gsm} does not match Sample_geo_accession {accessions}"
        )
    if not GSM_RE.fullmatch(header_gsm):
        raise SoftParseError(f"invalid sample accession {header_gsm!r}")
    title = _first(attributes, "Sample_title")
    if not title:
        raise SoftParseError(f"sample {header_gsm} is missing !Sample_title")
    characteristics = [
        _characteristic(raw)
        for raw in _values(attributes, "Sample_characteristics_ch")
    ]
    return {
        "gsm": header_gsm,
        "title": title,
        "source_name": (_values(attributes, "Sample_source_name_ch") or [""])[0],
        "organism": (_values(attributes, "Sample_organism_ch") or [""])[0],
        "molecule": (_values(attributes, "Sample_molecule_ch") or [""])[0],
        "characteristics": characteristics,
        "attributes": {key: list(values) for key, values in attributes.items()},
    }


def _platform_record(header_gpl: str, attributes: AttributeMap) -> dict[str, object]:
    accessions = attributes.get("Platform_geo_accession", [])
    if not accessions:
        raise SoftParseError(
            f"platform block {header_gpl} is missing !Platform_geo_accession"
        )
    if any(accession != header_gpl for accession in accessions):
        raise SoftParseError(
            f"platform header {header_gpl} does not match Platform_geo_accession {accessions}"
        )
    if not GPL_RE.fullmatch(header_gpl):
        raise SoftParseError(f"invalid platform accession {header_gpl!r}")
    return {
        "gpl": header_gpl,
        "attributes": {key: list(values) for key, values in attributes.items()},
    }


def normalize_soft_record(record: Mapping[str, object]) -> dict[str, object]:
    """Return ``record`` with the existing deterministic normalizers applied."""
    normalized = dict(record)
    organisms = [str(value) for value in record.get("organisms", [])]
    organism_ids, organism_status = map_organisms(", ".join(organisms))

    sex_values: set[str] = set()
    for item in record.get("characteristics", []):
        characteristic = dict(item)
        canonical_name = KEY_SYNONYMS.get(str(characteristic.get("name", "")).lower())
        if canonical_name == "sex":
            sex_values.update(str(value) for value in characteristic.get("values", []))
    sex_ids, sex_status, _ = map_sex_field(sex_values)

    study_types = record.get("type", [])
    if isinstance(study_types, str):
        study_type_text = study_types
    else:
        study_type_text = "; ".join(str(value) for value in study_types)
    assay = normalize_assay_fields(
        {
            "title": record.get("title", ""),
            "summary": record.get("summary", ""),
            "overall_design": record.get("overall_design", ""),
            "type": study_type_text,
        }
    )
    normalized.update(
        {
            "organism_ids": sorted(organism_ids),
            "organism_status": organism_status,
            "sex_ids": sorted(sex_ids),
            "sex_status": sex_status,
            "assay_categories": sorted(assay["assay_categories"]),
            "assay_labels": sorted(assay["assay_labels"]),
            "assay_status": assay["assay_status"],
        }
    )
    return normalized


def compose_soft_embed_text(record: Mapping[str, object]) -> str:
    """Compose deterministic raw-field document text without normalized labels."""
    parts: list[str] = []

    def add(label: str, value: object) -> None:
        if value:
            parts.append(f"{label}: {value}")

    add("Title", record.get("title", ""))
    study_types = record.get("type", [])
    if isinstance(study_types, str):
        add("Study type", study_types)
    else:
        add("Study type", "; ".join(str(value) for value in study_types))
    add("Organisms", ", ".join(str(value) for value in record.get("organisms", [])))
    add("Summary", record.get("summary", ""))
    add("Overall design", record.get("overall_design", ""))
    add("Molecules", ", ".join(str(value) for value in record.get("molecules", [])))
    add("Sample sources", ", ".join(str(value) for value in record.get("source_names", [])))

    characteristic_text: list[str] = []
    for item in record.get("characteristics", []):
        characteristic = dict(item)
        name = str(characteristic.get("name", ""))
        for value in characteristic.get("values", []):
            characteristic_text.append(f"{name}: {value}" if name else str(value))
    add("Sample characteristics", " | ".join(characteristic_text))
    return "\n".join(parts)


def parse_soft_record(source: Path, *, soft_root: Path) -> dict[str, object]:
    """Stream one stripped family SOFT file into the locked canonical schema."""
    filename_match = SOFT_NAME_RE.fullmatch(source.name)
    if not filename_match:
        raise SoftParseError(f"invalid family SOFT filename: {source.name}")
    filename_gse = filename_match.group(1)

    series_header: str | None = None
    series_attributes: AttributeMap | None = None
    platforms: list[tuple[str, AttributeMap]] = []
    samples: list[tuple[str, AttributeMap]] = []
    current_type: str | None = None
    current_attributes: AttributeMap | None = None
    in_series_table = False

    with gzip.open(source, "rt", encoding="utf-8", errors="replace") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            line = raw_line.rstrip("\r\n")
            if in_series_table:
                if line == "!series_table_end":
                    in_series_table = False
                continue
            if re.fullmatch(r"!series_table_begin(?: = .*)?", line):
                in_series_table = True
                continue
            if line.startswith("^"):
                record_type, separator, accession = line[1:].partition(" = ")
                if not separator:
                    raise SoftParseError(f"{source}:{line_number}: malformed record header")
                if record_type == "DATABASE":
                    current_type = record_type
                    current_attributes = OrderedDict()
                    continue
                if record_type == "SERIES":
                    if series_attributes is not None:
                        raise SoftParseError("multiple ^SERIES blocks")
                    series_header = accession
                    series_attributes = OrderedDict()
                    current_type = record_type
                    current_attributes = series_attributes
                    continue
                if record_type == "PLATFORM":
                    attributes: AttributeMap = OrderedDict()
                    platforms.append((accession, attributes))
                    current_type = record_type
                    current_attributes = attributes
                    continue
                if record_type == "SAMPLE":
                    attributes = OrderedDict()
                    samples.append((accession, attributes))
                    current_type = record_type
                    current_attributes = attributes
                    continue
                raise SoftParseError(
                    f"{source}:{line_number}: unsupported record type {record_type!r}"
                )
            if line.startswith("!"):
                key, separator, value = line[1:].partition(" = ")
                if not separator or current_attributes is None or current_type is None:
                    raise SoftParseError(f"{source}:{line_number}: malformed attribute")
                expected_prefix = current_type.title()
                if not key.startswith(f"{expected_prefix}_"):
                    raise SoftParseError(
                        f"{source}:{line_number}: {key!r} in {current_type} block"
                    )
                current_attributes.setdefault(key, []).append(value)

    if series_attributes is None or series_header is None:
        raise SoftParseError("missing ^SERIES block")
    series_accessions = series_attributes.get("Series_geo_accession", [])
    if not series_accessions:
        raise SoftParseError("missing !Series_geo_accession")
    if any(accession != series_accessions[0] for accession in series_accessions):
        raise SoftParseError(f"conflicting Series_geo_accession values: {series_accessions}")
    gse = series_accessions[0]
    if gse != series_header:
        raise SoftParseError(f"series header {series_header} does not match accession {gse}")
    if gse != filename_gse:
        raise SoftParseError(f"filename {filename_gse} does not match accession {gse}")
    if not GSE_RE.fullmatch(gse):
        raise SoftParseError(f"invalid GSE accession {gse!r}")
    title = _first(series_attributes, "Series_title")
    if not title:
        raise SoftParseError("missing !Series_title")

    parsed_platforms = [_platform_record(gpl, attrs) for gpl, attrs in platforms]
    parsed_samples = [_sample_record(gsm, attrs) for gsm, attrs in samples]
    sample_gses = [str(sample["gsm"]) for sample in parsed_samples]
    duplicates = sorted(
        (gsm for gsm, count in Counter(sample_gses).items() if count > 1),
        key=_accession_key,
    )
    if duplicates:
        raise SoftParseError(f"duplicate sample accession {duplicates[0]}")

    declared_samples = series_attributes.get("Series_sample_id", [])
    if declared_samples and len(declared_samples) != len(parsed_samples):
        raise SoftParseError(
            f"declares {len(declared_samples)} sample IDs but has "
            f"{len(parsed_samples)} sample blocks"
        )
    if declared_samples and sorted(declared_samples, key=_accession_key) != sorted(
        sample_gses, key=_accession_key
    ):
        raise SoftParseError(
            f"declared sample IDs do not match sample blocks: {declared_samples} != {sample_gses}"
        )

    organisms: list[str] = []
    molecules: list[str] = []
    source_names: list[str] = []
    characteristic_values: dict[str, list[str]] = {}
    library_strategies: list[str] = []
    library_sources: list[str] = []
    library_selections: list[str] = []
    platform_ids = list(series_attributes.get("Series_platform_id", []))
    sample_titles: list[str] = []
    for sample, (_, attributes) in zip(parsed_samples, samples, strict=True):
        organisms.extend(_values(attributes, "Sample_organism_ch"))
        molecules.extend(_values(attributes, "Sample_molecule_ch"))
        source_names.extend(_values(attributes, "Sample_source_name_ch"))
        library_strategies.extend(_values(attributes, "Sample_library_strategy"))
        library_sources.extend(_values(attributes, "Sample_library_source"))
        library_selections.extend(_values(attributes, "Sample_library_selection"))
        platform_ids.extend(_values(attributes, "Sample_platform_id"))
        sample_titles.append(str(sample["title"]))
        for characteristic in sample["characteristics"]:
            item = dict(characteristic)
            name = str(item["name"])
            characteristic_values.setdefault(name, []).append(str(item["value"]))
    platform_ids.extend(str(platform["gpl"]) for platform in parsed_platforms)

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "gse": gse,
        "source_soft": source.relative_to(soft_root).as_posix(),
        "title": title,
        "summary": _joined(series_attributes, "Series_summary"),
        "overall_design": _joined(series_attributes, "Series_overall_design"),
        "type": _distinct_sorted(list(series_attributes.get("Series_type", []))),
        "pubmed_ids": _distinct_sorted(list(series_attributes.get("Series_pubmed_id", []))),
        "submission_date": _date(series_attributes, "Series_submission_date"),
        "last_update_date": _date(series_attributes, "Series_last_update_date"),
        "platform_ids": _accessions_sorted(platform_ids, GPL_RE),
        "n_samples": len(parsed_samples),
        "organisms": _distinct_sorted(organisms),
        "molecules": _distinct_sorted(molecules),
        "source_names": _distinct_sorted(source_names),
        "characteristics": [
            {"name": name, "values": _distinct_sorted(values)}
            for name, values in sorted(characteristic_values.items())
        ],
        "library_strategies": _distinct_sorted(library_strategies),
        "library_sources": _distinct_sorted(library_sources),
        "library_selections": _distinct_sorted(library_selections),
        "sample_titles": _distinct_sorted(sample_titles),
        "sample_accessions": _accessions_sorted(sample_gses, GSM_RE),
        "series_attributes": {
            key: list(values) for key, values in series_attributes.items()
        },
        "platforms": parsed_platforms,
        "samples": parsed_samples,
    }
    normalized = normalize_soft_record(record)
    for key in (
        "organism_ids",
        "organism_status",
        "sex_ids",
        "sex_status",
        "assay_categories",
        "assay_labels",
        "assay_status",
    ):
        record[key] = normalized[key]
    record["embed_text"] = compose_soft_embed_text(record)

    ordered_keys = (
        "schema_version",
        "gse",
        "source_soft",
        "title",
        "summary",
        "overall_design",
        "type",
        "pubmed_ids",
        "submission_date",
        "last_update_date",
        "platform_ids",
        "n_samples",
        "organisms",
        "molecules",
        "source_names",
        "characteristics",
        "library_strategies",
        "library_sources",
        "library_selections",
        "organism_ids",
        "organism_status",
        "sex_ids",
        "sex_status",
        "assay_categories",
        "assay_labels",
        "assay_status",
        "sample_titles",
        "sample_accessions",
        "series_attributes",
        "platforms",
        "samples",
        "embed_text",
    )
    return {key: record[key] for key in ordered_keys}
