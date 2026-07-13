from __future__ import annotations

import os

import pytest

from geo_index.reranker import OpenAIReranker
from geo_index.search_candidates import SearchCandidate


def _candidate(gse: str, rank: int, taxon: str, organism_id: str) -> SearchCandidate:
    return SearchCandidate(
        gse=gse,
        title=f"Provider schema smoke {gse}",
        snippet="Skeletal muscle gene expression after endurance exercise.",
        study_type="Expression profiling by high throughput sequencing",
        n_samples=10,
        pubmed_id=None,
        organism_ids=(organism_id,),
        organism_status="mapped",
        sex_ids=(),
        sex_status="absent",
        assay_categories=("expression (seq)",),
        assay_labels=(),
        assay_status="category",
        source="elasticsearch",
        retrieval_score=1.0 / rank,
        original_rank=rank,
        native_rank=None,
        taxon=taxon,
    )


@pytest.mark.provider_integration
def test_live_luna_accepts_the_strict_complete_ranking_schema() -> None:
    if os.environ.get("GEO_TEST_OPENAI") != "1":
        pytest.skip("set GEO_TEST_OPENAI=1 to permit the live provider call")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        pytest.skip("OPENAI_API_KEY is not configured")
    reranker = OpenAIReranker(
        api_key=api_key,
        model="gpt-5.6-luna",
        reasoning_effort="low",
        timeout_seconds=30,
    )
    candidates = (
        _candidate("GSE11803", 1, "Mus musculus", "NCBITaxon:10090"),
        _candidate("GSE310900", 2, "Homo sapiens", "NCBITaxon:9606"),
    )
    try:
        result = reranker.rerank(
            "mouse skeletal muscle after endurance exercise",
            candidates,
            limit=2,
        )
    finally:
        reranker.close()

    assert set(result.scores) == {"GSE11803", "GSE310900"}
