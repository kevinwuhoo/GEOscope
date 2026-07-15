from __future__ import annotations

import os
from pathlib import Path


def test_production_elasticsearch_is_private_persistent_and_unlimited() -> None:
    text = Path("deploy/elasticsearch/docker-compose.production.yml").read_text()
    elasticsearch = text.split("\n  vector:", maxsplit=1)[0]
    assert "elasticsearch:9.4.2" in elasticsearch
    assert '127.0.0.1:9200:9200' in elasticsearch
    assert '10.124.0.2:9200:9200' in elasticsearch
    assert "0.0.0.0:9200" not in elasticsearch
    assert (
        "/srv/elasticsearch/data:/usr/share/elasticsearch/data"
        in elasticsearch
    )
    assert "ES_JAVA_OPTS" not in elasticsearch
    assert "mem_limit" not in elasticsearch
    assert "cpus:" not in elasticsearch
    assert "max-size: 20m" in elasticsearch


def test_heap_is_four_gibibytes() -> None:
    assert Path("deploy/elasticsearch/jvm.options.d/heap.options").read_text() == (
        "-Xms4g\n-Xmx4g\n"
    )


def test_vector_is_private_persistent_and_resource_bounded() -> None:
    compose = Path(
        "deploy/elasticsearch/docker-compose.production.yml"
    ).read_text()

    assert "timberio/vector:0.57.0-debian" in compose
    assert '10.124.0.2:8686:8686' in compose
    assert '0.0.0.0:8686:8686' not in compose
    assert "/srv/vector/data:/var/lib/vector" in compose
    assert "./vector.yaml:/etc/vector/vector.yaml:ro" in compose
    assert "mem_limit: 256m" in compose
    assert 'cpus: "0.25"' in compose
    assert 'VECTOR_BATCH_TIMEOUT_SECONDS: "3600"' in compose
    assert (
        'VECTOR_DANGEROUSLY_ALLOW_ENV_VAR_INTERPOLATION: "true"'
        in compose
    )
    assert "R2_ACCESS_KEY_ID" in compose
    assert "R2_SECRET_ACCESS_KEY" in compose
    assert "max-size: 10m" in compose
    assert 'max-file: "3"' in compose


def test_vector_archives_hourly_gzip_jsonl_to_r2() -> None:
    config = Path("deploy/elasticsearch/vector.yaml").read_text()

    for fragment in (
        "data_dir: /var/lib/vector",
        "type: http_server",
        "address: 0.0.0.0:8686",
        "path: /events",
        "strict_path: true",
        "method: POST",
        "method: newline_delimited",
        "type: aws_s3",
        "endpoint: ${R2_ENDPOINT}",
        "bucket: ${R2_BUCKET}",
        "region: auto",
        "force_path_style: true",
        "compression: gzip",
        "filename_extension: jsonl.gz",
        "timeout_secs: ${VECTOR_BATCH_TIMEOUT_SECONDS}",
        "max_bytes: 134217728",
        "type: disk",
        "max_size: 536870912",
        "when_full: block",
    ):
        assert fragment in config
    assert (
        "runtime/app=geoscope/year=%Y/month=%m/day=%d/hour=%H/"
        in config
    )
    assert "api:" not in config
    assert "auth:" not in config
    assert "tls:" not in config


def test_vector_credentials_are_placeholders_and_data_dir_is_bootstrapped() -> None:
    example = Path(
        "deploy/elasticsearch/elasticsearch.env.example"
    ).read_text()
    for setting in (
        "R2_ENDPOINT=https://replace-with-account-id.r2.cloudflarestorage.com",
        "R2_BUCKET=geoscope-runtime-logs",
        "R2_ACCESS_KEY_ID=replace-with-bucket-access-key-id",
        "R2_SECRET_ACCESS_KEY=replace-with-bucket-secret-access-key",
    ):
        assert setting in example

    bootstrap = Path(
        "deploy/elasticsearch/bootstrap-ubuntu.sh"
    ).read_text()
    assert "install -d -m 0750 /srv/vector/data" in bootstrap


def test_vector_s3_integration_script_is_executable_and_self_cleaning() -> None:
    script_path = Path("tests/vector_r2_integration.sh")
    script = script_path.read_text()

    assert os.access(script_path, os.X_OK)
    assert "timberio/vector:0.57.0-debian" in script
    assert "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z" in script
    assert "trap cleanup EXIT" in script
    assert 'docker volume rm "$VECTOR_DATA"' in script
    assert "VECTOR_BATCH_TIMEOUT_SECONDS=1" in script
    assert "VECTOR_DANGEROUSLY_ALLOW_ENV_VAR_INTERPOLATION=true" in script
    assert "application/x-ndjson" in script
    assert "gzip -dc" in script
    assert "runtime/app=geoscope/year=" in script


def test_runbook_covers_log_archive_rollout_and_recovery() -> None:
    text = Path("docs/deployment/digitalocean.md").read_text()
    for phrase in (
        "R2 runtime log archive",
        "R2_ENDPOINT",
        "10.124.0.2:8686",
        "App Platform VPC egress private IP",
        "docker stats",
        "vector validate --skip-healthchecks",
        "application/x-ndjson",
        "gzip -dc",
        "/srv/vector/data",
        "512 MiB",
    ):
        assert phrase in text
