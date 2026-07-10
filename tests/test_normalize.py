from geo_index.normalize import map_organisms, map_sex_value, normalize_row


def test_map_organisms_maps_human_and_mouse() -> None:
    ids, status = map_organisms("Homo sapiens, Mus musculus")
    assert ids == ["NCBITaxon:9606", "NCBITaxon:10090"]
    assert status == "mapped"


def test_map_sex_rejects_numeric_study_code() -> None:
    ids, reason, confidence = map_sex_value("1")
    assert ids == []
    assert reason == "numeric_code"
    assert confidence == 0.0


def test_normalize_row_keeps_absent_distinct_from_unmapped() -> None:
    result = normalize_row(
        {
            "organisms": "Homo sapiens",
            "characteristics": "",
            "title": "",
            "summary": "",
            "overall_design": "",
            "type": "Expression profiling by high throughput sequencing",
        }
    )
    assert result["organism_status"] == "mapped"
    assert result["sex_status"] == "absent"
    assert result["tissue_status"] == "absent"
