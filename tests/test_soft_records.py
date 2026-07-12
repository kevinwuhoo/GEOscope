from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

import pytest

from geo_index.soft_records import (
    SoftParseError,
    compose_soft_embed_text,
    normalize_soft_record,
    parse_soft_record,
    record_path,
)


FIXTURES = Path(__file__).parent / "fixtures" / "soft"


def _write_soft(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(text)
    return path


def _fixture_as_gse(tmp_path: Path, fixture_name: str, gse: str) -> Path:
    destination = tmp_path / f"{gse}_family.soft.gz"
    shutil.copyfile(FIXTURES / fixture_name, destination)
    return destination


def test_record_path_uses_geo_bucket() -> None:
    root = Path("records")
    assert record_path(root, "GSE271800") == (
        root / "GSE271nnn" / "GSE271800.json"
    )
    assert record_path(root, "gse42") == root / "GSEnnn" / "GSE42.json"


@pytest.mark.parametrize("gse", ["GSM1", "GSE0", "GSE-1", "GSE"])
def test_record_path_rejects_malformed_accessions(gse: str) -> None:
    with pytest.raises(ValueError, match="invalid GSE"):
        record_path(Path("records"), gse)


def test_parser_materializes_locked_schema_and_complete_attribute_maps(
    tmp_path: Path,
) -> None:
    record = parse_soft_record(
        _fixture_as_gse(tmp_path, "minimal_family.soft.gz", "GSE1001"),
        soft_root=tmp_path,
    )

    assert list(record) == [
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
    ]
    assert record["schema_version"] == 1
    assert record["gse"] == "GSE1001"
    assert record["source_soft"] == "GSE1001_family.soft.gz"
    assert record["title"] == "Human blood RNA sequencing"
    assert record["summary"] == "Raw study summary."
    assert record["overall_design"] == "Raw overall design."
    assert record["type"] == [
        "Expression profiling by high throughput sequencing"
    ]
    assert record["pubmed_ids"] == ["12345678"]
    assert record["submission_date"] == "2024-01-01"
    assert record["last_update_date"] == "2024-01-02"
    assert record["platform_ids"] == ["GPL10"]
    assert record["n_samples"] == 2
    assert record["organisms"] == ["Homo sapiens"]
    assert record["molecules"] == ["total RNA"]
    assert record["source_names"] == ["blood"]
    assert record["characteristics"] == [
        {"name": "disease", "values": ["control", "status: active"]},
        {"name": "gender", "values": ["F"]},
        {"name": "sex", "values": ["female"]},
    ]
    assert record["library_strategies"] == ["RNA-Seq"]
    assert record["library_sources"] == ["TRANSCRIPTOMIC"]
    assert record["library_selections"] == ["cDNA"]
    assert record["sample_titles"] == ["Female donor", "Second donor"]
    assert record["sample_accessions"] == ["GSM2", "GSM11"]

    series_attributes = record["series_attributes"]
    assert series_attributes["Series_title"] == ["Human blood RNA sequencing"]
    assert series_attributes["Series_relation"] == [
        "BioProject: https://example.test/PRJ1"
    ]
    assert series_attributes["Series_sample_id"] == ["GSM11", "GSM2"]

    assert record["platforms"] == [
        {
            "gpl": "GPL10",
            "attributes": {
                "Platform_title": ["Illumina NovaSeq 6000 (Homo sapiens)"],
                "Platform_geo_accession": ["GPL10"],
                "Platform_technology": ["high-throughput sequencing"],
                "Platform_custom_field": ["exact platform value"],
            },
        }
    ]
    samples = record["samples"]
    assert [sample["gsm"] for sample in samples] == ["GSM11", "GSM2"]
    assert samples[0]["characteristics"] == [
        {"name": "sex", "value": "female", "raw": "sex: female"},
        {
            "name": "disease",
            "value": "status: active",
            "raw": "disease: status: active",
        },
    ]
    assert samples[0]["attributes"]["Sample_treatment_protocol_ch1"] == [
        "first protocol",
        "second protocol",
    ]


def test_parser_applies_existing_normalizers_without_injecting_labels(
    tmp_path: Path,
) -> None:
    record = parse_soft_record(
        _fixture_as_gse(tmp_path, "minimal_family.soft.gz", "GSE1001"),
        soft_root=tmp_path,
    )

    assert record["organism_ids"] == ["NCBITaxon:9606"]
    assert record["organism_status"] == "mapped"
    assert record["sex_ids"] == ["PATO:0000383"]
    assert record["sex_status"] == "mapped"
    assert record["assay_categories"] == ["expression (seq)"]
    assert record["assay_labels"] == []
    assert record["assay_status"] == "category"
    assert record["embed_text"] == (
        "Title: Human blood RNA sequencing\n"
        "Study type: Expression profiling by high throughput sequencing\n"
        "Organisms: Homo sapiens\n"
        "Summary: Raw study summary.\n"
        "Overall design: Raw overall design.\n"
        "Molecules: total RNA\n"
        "Sample sources: blood\n"
        "Sample characteristics: disease: control | disease: status: active | "
        "gender: F | sex: female"
    )
    assert "NCBITaxon" not in record["embed_text"]
    assert "PATO:" not in record["embed_text"]


def test_repeated_unknown_metadata_and_first_colon_are_preserved(
    tmp_path: Path,
) -> None:
    record = parse_soft_record(
        _fixture_as_gse(
            tmp_path,
            "repeated_characteristics_family.soft.gz",
            "GSE42",
        ),
        soft_root=tmp_path,
    )

    assert record["summary"] == "First summary line.\nSecond summary line."
    assert record["type"] == ["Expression profiling by array", "Other"]
    assert record["series_attributes"]["Series_relation"] == [
        "BioProject: https://example.test/one",
        "SRA: https://example.test/two",
    ]
    assert record["series_attributes"]["Series_unknown"] == ["first", "second"]
    assert record["platforms"][0]["attributes"]["Platform_unknown"] == [
        "alpha",
        "beta",
    ]
    sample = record["samples"][0]
    assert sample["characteristics"] == [
        {
            "name": "disease",
            "value": "status: active",
            "raw": "disease: status: active",
        },
        {"name": "disease", "value": "control", "raw": "disease: control"},
        {"name": "", "value": "no delimiter value", "raw": "no delimiter value"},
    ]
    assert sample["attributes"]["Sample_unknown"] == ["alpha", "beta"]


def test_normalize_and_compose_are_deterministic_pure_interfaces() -> None:
    raw = {
        "title": "Title",
        "summary": "Summary",
        "overall_design": "Design",
        "type": ["Other"],
        "organisms": ["Mus musculus"],
        "molecules": [],
        "source_names": ["liver"],
        "characteristics": [{"name": "sex", "values": ["male"]}],
    }
    normalized = normalize_soft_record(raw)
    assert normalized["organism_ids"] == ["NCBITaxon:10090"]
    assert normalized["sex_ids"] == ["PATO:0000384"]
    assert compose_soft_embed_text(raw) == (
        "Title: Title\nStudy type: Other\nOrganisms: Mus musculus\n"
        "Summary: Summary\nOverall design: Design\nSample sources: liver\n"
        "Sample characteristics: sex: male"
    )


def test_filename_and_series_accession_must_match(tmp_path: Path) -> None:
    source = _write_soft(
        tmp_path / "GSE2_family.soft.gz",
        "^SERIES = GSE1\n!Series_geo_accession = GSE1\n!Series_title = x\n",
    )
    with pytest.raises(SoftParseError, match="filename GSE2"):
        parse_soft_record(source, soft_root=tmp_path)


def test_missing_series_accession_is_rejected(tmp_path: Path) -> None:
    source = _write_soft(
        tmp_path / "GSE1_family.soft.gz",
        "^SERIES = GSE1\n!Series_title = x\n",
    )
    with pytest.raises(SoftParseError, match="missing !Series_geo_accession"):
        parse_soft_record(source, soft_root=tmp_path)


def test_declared_sample_ids_must_match_sample_blocks(tmp_path: Path) -> None:
    source = _write_soft(
        tmp_path / "GSE1_family.soft.gz",
        "^SERIES = GSE1\n"
        "!Series_geo_accession = GSE1\n"
        "!Series_title = x\n"
        "!Series_sample_id = GSM1\n"
        "!Series_sample_id = GSM2\n"
        "^SAMPLE = GSM1\n"
        "!Sample_geo_accession = GSM1\n"
        "!Sample_title = one\n",
    )
    with pytest.raises(SoftParseError, match="declares 2 sample IDs.*1 sample blocks"):
        parse_soft_record(source, soft_root=tmp_path)


def test_duplicate_sample_accessions_are_rejected(tmp_path: Path) -> None:
    source = _write_soft(
        tmp_path / "GSE1_family.soft.gz",
        "^SERIES = GSE1\n"
        "!Series_geo_accession = GSE1\n"
        "!Series_title = x\n"
        "!Series_sample_id = GSM1\n"
        "!Series_sample_id = GSM1\n"
        "^SAMPLE = GSM1\n"
        "!Sample_geo_accession = GSM1\n"
        "!Sample_title = one\n"
        "^SAMPLE = GSM1\n"
        "!Sample_geo_accession = GSM1\n"
        "!Sample_title = two\n",
    )
    with pytest.raises(SoftParseError, match="duplicate sample accession GSM1"):
        parse_soft_record(source, soft_root=tmp_path)


def test_retained_series_data_table_is_ignored_without_losing_later_metadata(
    tmp_path: Path,
) -> None:
    source = _write_soft(
        tmp_path / "GSE1_family.soft.gz",
        "^SERIES = GSE1\n"
        "!Series_geo_accession = GSE1\n"
        "!Series_title = table-bearing study\n"
        "!Series_sample_id = GSM1\n"
        "!series_table_begin = Comparison values\n"
        "ID_REF\tlog fold change\n"
        "probe-1\t2.5\n"
        "!series_table_end\n"
        "^SAMPLE = GSM1\n"
        "!Sample_geo_accession = GSM1\n"
        "!Sample_title = sample after table\n",
    )

    record = parse_soft_record(source, soft_root=tmp_path)

    assert record["n_samples"] == 1
    assert record["samples"][0]["title"] == "sample after table"
    assert "series_table_begin" not in record["series_attributes"]
    assert "probe-1" not in json.dumps(record)
