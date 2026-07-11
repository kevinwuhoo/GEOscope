from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator

import numpy as np
import pytest

from geo_index.elasticsearch_config import (
    INDEX_NAME,
    ElasticsearchSettings,
    create_client,
    response_body,
)
from geo_index.elasticsearch_index import index_definition, reset_index
from geo_index.elasticsearch_loader import LoadReport, load_index
from geo_index.elasticsearch_search import ElasticsearchSearchService
from geo_index.search_models import SearchFilters


pytestmark = [
    pytest.mark.elastic_integration,
    pytest.mark.skipif(
        os.environ.get("GEO_TEST_ELASTIC") != "1",
        reason="set GEO_TEST_ELASTIC=1",
    ),
]


@pytest.fixture(scope="module")
def elastic_client() -> Iterator[object]:
    client = create_client(ElasticsearchSettings.from_env())
    yield client
    client.close()


def _write_record(
    root: Path,
    gse: str,
    *,
    title: str,
    organism_id: str,
    sex_id: str,
) -> None:
    path = root / "synthetic" / f"{gse}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "gse": gse,
                "title": title,
                "summary": f"{title} expression study",
                "overall_design": "synthetic verification record",
                "embed_text": f"Title: {title}",
                "type": ["Expression profiling by high throughput sequencing"],
                "pubmed_ids": [],
                "submission_date": "2024-01-01",
                "last_update_date": "2024-01-02",
                "platform_ids": ["GPL1"],
                "n_samples": 2,
                "organisms": [
                    "Homo sapiens"
                    if organism_id == "NCBITaxon:9606"
                    else "Mus musculus"
                ],
                "molecules": ["total RNA"],
                "source_names": ["blood"],
                "library_strategies": ["RNA-Seq"],
                "library_sources": ["TRANSCRIPTOMIC"],
                "library_selections": ["cDNA"],
                "organism_ids": [organism_id],
                "organism_status": "mapped",
                "sex_ids": [sex_id],
                "sex_status": "mapped",
                "assay_categories": ["transcriptomic"],
                "assay_labels": ["RNA-seq"],
                "assay_status": "mapped",
            }
        ),
        encoding="utf-8",
    )


def _write_sources(root: Path) -> tuple[Path, Path]:
    records = root / "records"
    artifacts = root / "artifacts"
    _write_record(
        records,
        "GSE2",
        title="human immune cells",
        organism_id="NCBITaxon:9606",
        sex_id="PATO:0000383",
    )
    _write_record(
        records,
        "GSE10",
        title="mouse chromatin",
        organism_id="NCBITaxon:10090",
        sex_id="PATO:0000384",
    )
    artifact = artifacts / "bge_small_v15"
    artifact.mkdir(parents=True)
    vectors = np.zeros((2, 384), dtype=np.float32)
    vectors[0, -1] = 1.0
    vectors[1, 0] = 1.0
    np.save(artifact / "vectors.npy", vectors, allow_pickle=False)
    (artifact / "ids.json").write_text(
        json.dumps(["GSE2", "GSE10"]), encoding="utf-8"
    )
    (artifact / "metadata.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_key": "bge_small_v15",
                "dimensions": 384,
                "record_count": 2,
            }
        ),
        encoding="utf-8",
    )
    return records, artifacts


@pytest.fixture(scope="module")
def loaded_reports(
    elastic_client: object,
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[LoadReport, LoadReport]:
    records, artifacts = _write_sources(tmp_path_factory.mktemp("elastic-live"))
    reset_index(elastic_client, confirm=True)
    first = load_index(
        elastic_client,
        records_root=records,
        artifacts_root=artifacts,
        model_keys=("bge_small_v15",),
        batch_size=1,
    )
    second = load_index(
        elastic_client,
        records_root=records,
        artifacts_root=artifacts,
        model_keys=("bge_small_v15",),
        batch_size=1,
    )
    return first, second


def _service(elastic_client: object) -> ElasticsearchSearchService:
    return ElasticsearchSearchService(
        elastic_client,
        active_model_key="bge_small_v15",
        encode_query=lambda _query: [0.0] * 383 + [1.0],
    )


def test_live_server_version_health_mapping_and_dimensions(
    elastic_client: object,
    loaded_reports: tuple[LoadReport, LoadReport],
) -> None:
    info = response_body(elastic_client.info())  # type: ignore[attr-defined]
    assert info["version"]["number"] == "9.4.2"  # type: ignore[index]
    health = response_body(
        elastic_client.cluster.health(  # type: ignore[attr-defined]
            wait_for_status="yellow", timeout="10s"
        )
    )
    assert health["status"] in {"yellow", "green"}
    mapping = response_body(
        elastic_client.indices.get_mapping(index=INDEX_NAME)  # type: ignore[attr-defined]
    )[INDEX_NAME]["mappings"]
    expected = index_definition()["mappings"]
    assert mapping["_meta"] == expected["_meta"]
    assert mapping["properties"]["embedding_bge_384"]["dims"] == 384
    assert mapping["properties"]["embedding_medcpt_768"]["dims"] == 768
    assert mapping["properties"]["embedding_qwen3_06b_1024"]["dims"] == 1024
    assert mapping["properties"]["embedding_gemini_3072"]["dims"] == 3072


def test_live_second_load_has_no_duplicates_and_reports_coverage(
    loaded_reports: tuple[LoadReport, LoadReport],
) -> None:
    first, second = loaded_reports
    assert first.document_count == 2
    assert second.document_count == 2
    assert first.succeeded == second.succeeded == 2
    assert second.vector_coverage["embedding_bge_384"] == 2
    assert second.vector_coverage["embedding_medcpt_768"] == 0


def test_live_exact_bm25_dense_and_native_rrf_smoke(
    elastic_client: object,
    loaded_reports: tuple[LoadReport, LoadReport],
) -> None:
    service = _service(elastic_client)
    assert service.get_dataset("GSE2")["title"] == "human immune cells"  # type: ignore[index]
    bm25 = service.search("immune", mode="bm25", topk=2, facet_pool=2)
    dense = service.search(
        "immune", mode="dense", topk=2, deep=2, num_candidates=2, facet_pool=2
    )
    hybrid = service.search(
        "immune", mode="hybrid", topk=2, deep=2, num_candidates=2, facet_pool=2
    )
    assert bm25.hits[0]["gse"] == "GSE2"
    assert dense.hits[0]["gse"] == "GSE2"
    assert hybrid.hits[0]["gse"] == "GSE2"
    assert hybrid.provenance is not None
    assert hybrid.provenance.mode == "hybrid"


def test_live_filters_and_disjunctive_blank_facets(
    elastic_client: object,
    loaded_reports: tuple[LoadReport, LoadReport],
) -> None:
    response = _service(elastic_client).search(
        "",
        mode="bm25",
        topk=2,
        filters=SearchFilters(organism_ids=("NCBITaxon:9606",)),
        bucket_limit=10,
    )
    assert [hit["gse"] for hit in response.hits] == ["GSE2"]
    organism_counts = {
        bucket.value: bucket.count
        for bucket in response.facets["organism_ids"].buckets
    }
    assert organism_counts == {
        "NCBITaxon:10090": 1,
        "NCBITaxon:9606": 1,
    }
    assert response.facets["organism_ids"].scope == "all_matches"
