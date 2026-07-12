"""Owned Elasticsearch search runtime with a lazy active-model query encoder."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .elasticsearch_config import ElasticsearchSettings, create_client
from .elasticsearch_query_embeddings import QueryEncoder, create_query_encoder
from .elasticsearch_search import ElasticsearchSearchService, SearchMode
from .search_models import SearchFilters, SearchResponse


class ElasticsearchRuntime:
    """Own one Elasticsearch client and lazily create its query encoder."""

    def __init__(
        self,
        *,
        settings: ElasticsearchSettings | None = None,
        client: Any | None = None,
        encoder_factory: Callable[[str], QueryEncoder] = create_query_encoder,
    ) -> None:
        self.settings = settings or ElasticsearchSettings.from_env()
        self._client = client if client is not None else create_client(self.settings)
        self._encoder_factory = encoder_factory
        self._encoder: QueryEncoder | None = None
        self._encoder_lock = threading.Lock()
        self._closed = False
        self._service = ElasticsearchSearchService(
            self._client,
            active_model_key=self.settings.active_model_key,
            encode_query=self._encode_query,
        )

    def _encode_query(self, query: str):
        with self._encoder_lock:
            if self._closed:
                raise RuntimeError("Elasticsearch runtime is closed")
            if self._encoder is None:
                self._encoder = self._encoder_factory(self.settings.active_model_key)
            encoder = self._encoder
        return encoder.encode(query)

    def search(
        self,
        query: str,
        *,
        mode: SearchMode = "hybrid",
        filters: SearchFilters | None = None,
        topk: int = 15,
    ) -> SearchResponse:
        if self._closed:
            raise RuntimeError("Elasticsearch runtime is closed")
        return self._service.search(query, mode=mode, filters=filters, topk=topk)

    def get_dataset(self, gse: str) -> dict[str, object] | None:
        if self._closed:
            raise RuntimeError("Elasticsearch runtime is closed")
        return self._service.get_dataset(gse)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._encoder is not None:
            self._encoder.close()
        self._client.close()

    def __enter__(self) -> ElasticsearchRuntime:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

