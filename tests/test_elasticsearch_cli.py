from __future__ import annotations

import json

from geo_index import elasticsearch_cli
from geo_index.search_models import SearchProvenance, SearchResponse


def test_cli_prints_elasticsearch_response_and_closes_runtime(
    monkeypatch,
    capsys,
) -> None:
    calls: list[dict[str, object]] = []

    class Runtime:
        closed = False

        def search(self, query, **kwargs):
            calls.append({"query": query, **kwargs})
            return SearchResponse(
                hits=({"gse": "GSE1", "title": "immune"},),
                provenance=SearchProvenance(
                    backend="elasticsearch",
                    mapping_revision="geo-series-v1",
                    active_model_key="gemini_embedding_2_3072_v1",
                    vector_field="embedding_gemini_3072",
                    dimensions=3072,
                    mode="hybrid",
                ),
            )

        def close(self):
            self.closed = True

    runtime = Runtime()
    monkeypatch.setattr(elasticsearch_cli, "ElasticsearchRuntime", lambda: runtime)

    code = elasticsearch_cli.main(
        ["immune cells", "--topk", "5", "--organism-id", "NCBITaxon:9606"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["provenance"]["backend"] == "elasticsearch"
    assert payload["provenance"]["dimensions"] == 3072
    assert calls[0]["query"] == "immune cells"
    assert calls[0]["topk"] == 5
    assert calls[0]["filters"].organism_ids == ("NCBITaxon:9606",)
    assert runtime.closed is True

