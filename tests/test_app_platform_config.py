from __future__ import annotations

from pathlib import Path


def test_app_platform_template_uses_one_small_private_service() -> None:
    text = Path(".do/app.yaml.tmpl").read_text()
    assert "region: sfo" in text
    assert "instance_size_slug: apps-s-1vcpu-0.5gb" in text
    assert "instance_count: 1" in text
    assert "disable_edge_cache: true" in text
    assert "http_path: /healthz" in text
    assert "ELASTICSEARCH_URL" in text
    assert "http://10.124.0.2:9200" in text
    assert "ELASTICSEARCH_PASSWORD" in text
    assert "GEMINI_API_KEY" in text
    assert "GEO_MCP_JWKS_URI" not in text
