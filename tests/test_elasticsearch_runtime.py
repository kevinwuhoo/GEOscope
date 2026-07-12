from __future__ import annotations

from types import SimpleNamespace

from geo_index.elasticsearch_config import ElasticsearchSettings
from geo_index.elasticsearch_runtime import ElasticsearchRuntime
from geo_index.search_models import SearchFilters, SearchResponse


class FakeClient:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class FakeEncoder:
    def __init__(self) -> None:
        self.encoded: list[str] = []
        self.closed = 0

    def encode(self, query: str):
        self.encoded.append(query)
        return [1.0] * 3072

    def close(self) -> None:
        self.closed += 1


def _settings() -> ElasticsearchSettings:
    return ElasticsearchSettings(
        url="http://localhost:9200",
        api_key="elastic-key",
        active_model_key="gemini_embedding_2_3072_v1",
    )


def test_bm25_avoids_encoder_and_dense_reuses_one_encoder() -> None:
    client = FakeClient()
    encoder = FakeEncoder()
    created: list[str] = []
    runtime = ElasticsearchRuntime(
        settings=_settings(),
        client=client,
        encoder_factory=lambda model_key: created.append(model_key) or encoder,
    )
    calls: list[dict[str, object]] = []
    runtime._service.search = lambda query, **kwargs: (  # type: ignore[method-assign]
        calls.append({"query": query, **kwargs}) or SearchResponse(hits=())
    )

    runtime.search("immune", mode="bm25", topk=5)
    assert created == []
    runtime._encode_query("immune")
    runtime._encode_query("mouse")

    assert created == ["gemini_embedding_2_3072_v1"]
    assert encoder.encoded == ["immune", "mouse"]
    runtime.close()
    runtime.close()
    assert encoder.closed == 1
    assert client.closed == 1


def test_runtime_delegates_closed_search_contract() -> None:
    runtime = ElasticsearchRuntime(settings=_settings(), client=FakeClient())
    response = SearchResponse(hits=({"gse": "GSE1"},))
    calls: list[dict[str, object]] = []
    runtime._service.search = lambda query, **kwargs: (  # type: ignore[method-assign]
        calls.append({"query": query, **kwargs}) or response
    )
    filters = SearchFilters(organism_ids=("NCBITaxon:9606",))

    assert runtime.search("immune", mode="hybrid", topk=7, filters=filters) is response
    assert calls == [
        {"query": "immune", "mode": "hybrid", "topk": 7, "filters": filters}
    ]

