from __future__ import annotations

from pathlib import Path


def test_production_elasticsearch_is_private_persistent_and_unlimited() -> None:
    text = Path("deploy/elasticsearch/docker-compose.production.yml").read_text()
    assert "elasticsearch:9.4.2" in text
    assert '127.0.0.1:9200:9200' in text
    assert '10.124.0.2:9200:9200' in text
    assert "0.0.0.0:9200" not in text
    assert "/srv/elasticsearch/data:/usr/share/elasticsearch/data" in text
    assert "ES_JAVA_OPTS" not in text
    assert "mem_limit" not in text
    assert "cpus:" not in text
    assert "max-size: 20m" in text


def test_heap_is_four_gibibytes() -> None:
    assert Path("deploy/elasticsearch/jvm.options.d/heap.options").read_text() == (
        "-Xms4g\n-Xmx4g\n"
    )
