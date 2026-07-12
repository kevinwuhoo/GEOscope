from __future__ import annotations

from pathlib import Path

import pytest

from geo_index.elasticsearch_config import (
    INDEX_NAME,
    VECTOR_FIELDS,
    ElasticsearchSettings,
)


def test_compose_pins_local_single_node_with_volume_and_healthcheck() -> None:
    text = Path("docker-compose.elasticsearch.yml").read_text(encoding="utf-8")
    assert "docker.elastic.co/elasticsearch/elasticsearch:9.4.2" in text
    assert '"127.0.0.1:9200:9200"' in text
    assert "discovery.type=single-node" in text
    assert "xpack.security.enabled=true" in text
    assert "xpack.security.http.ssl.enabled=false" in text
    assert "xpack.license.self_generated.type=trial" in text
    assert "cluster.routing.allocation.disk.watermark.low=1gb" in text
    assert "cluster.routing.allocation.disk.watermark.high=750mb" in text
    assert "cluster.routing.allocation.disk.watermark.flood_stage=500mb" in text
    assert "geo_elasticsearch_data:/usr/share/elasticsearch/data" in text
    assert "healthcheck:" in text
    assert "/_security/_authenticate" in text
    assert "ELASTIC_PASSWORD" in text


def test_example_environment_file_can_be_sourced_by_a_shell() -> None:
    text = Path(".env.elasticsearch.example").read_text(encoding="utf-8")
    assert 'ELASTICSEARCH_JAVA_OPTS="-Xms1g -Xmx1g"' in text


def test_fixed_index_and_vector_fields() -> None:
    assert INDEX_NAME == "geo-series"
    assert {
        key: (spec.field, spec.dimensions) for key, spec in VECTOR_FIELDS.items()
    } == {
        "bge_small_v15": ("embedding_bge_384", 384),
        "medcpt_v1": ("embedding_medcpt_768", 768),
        "qwen3_06b_1024_v1": ("embedding_qwen3_06b_1024", 1024),
        "gemini_embedding_2_3072_v1": ("embedding_gemini_3072", 3072),
    }


def test_settings_accept_basic_auth_and_active_model() -> None:
    settings = ElasticsearchSettings.from_env(
        {
            "ELASTICSEARCH_URL": "http://localhost:9200",
            "ELASTICSEARCH_USERNAME": "elastic",
            "ELASTICSEARCH_PASSWORD": "secret",
            "ELASTICSEARCH_ACTIVE_MODEL": "bge_small_v15",
        }
    )
    assert settings.url == "http://localhost:9200"
    assert settings.username == "elastic"
    assert settings.password == "secret"
    assert settings.api_key is None
    assert settings.active_model_key == "bge_small_v15"


def test_settings_accept_api_key_without_basic_auth() -> None:
    settings = ElasticsearchSettings.from_env(
        {
            "ELASTICSEARCH_URL": "https://managed.example.test",
            "ELASTICSEARCH_API_KEY": "encoded-key",
            "ELASTICSEARCH_ACTIVE_MODEL": "medcpt_v1",
        }
    )
    assert settings.api_key == "encoded-key"
    assert settings.username is None


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        ({}, "ELASTICSEARCH_URL"),
        ({"ELASTICSEARCH_URL": "http://localhost:9200"}, "credentials"),
        (
            {
                "ELASTICSEARCH_URL": "http://localhost:9200",
                "ELASTICSEARCH_USERNAME": "elastic",
            },
            "username and password",
        ),
        (
            {
                "ELASTICSEARCH_URL": "http://localhost:9200",
                "ELASTICSEARCH_USERNAME": "elastic",
                "ELASTICSEARCH_PASSWORD": "secret",
                "ELASTICSEARCH_API_KEY": "encoded-key",
            },
            "one credential",
        ),
        (
            {
                "ELASTICSEARCH_URL": "http://localhost:9200",
                "ELASTICSEARCH_API_KEY": "encoded-key",
                "ELASTICSEARCH_ACTIVE_MODEL": "unknown",
            },
            "unknown active model",
        ),
        (
            {
                "ELASTICSEARCH_URL": "http://localhost:9200",
                "ELASTICSEARCH_API_KEY": "encoded-key",
                "ELASTICSEARCH_REQUEST_TIMEOUT": "0",
            },
            "request timeout",
        ),
        (
            {
                "ELASTICSEARCH_URL": "http://localhost:9200",
                "ELASTICSEARCH_API_KEY": "encoded-key",
                "ELASTICSEARCH_MAX_RETRIES": "-1",
            },
            "max retries",
        ),
    ],
)
def test_settings_reject_invalid_configuration(
    environ: dict[str, str], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        ElasticsearchSettings.from_env(environ)
