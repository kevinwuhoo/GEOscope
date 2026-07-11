from importlib import import_module
import gzip
import json
from pathlib import Path
import threading
import tomllib
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

import pytest


def _write_gz(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(text)


def _get_json(url: str) -> object:
    try:
        with urlopen(url) as response:
            return json.loads(response.read())
    except HTTPError as error:
        return {"status": error.code, "body": error.read().decode()}


def _get_text(url: str) -> tuple[int, str]:
    try:
        with urlopen(url) as response:
            return response.status, response.read().decode()
    except HTTPError as error:
        return error.code, error.read().decode()


def test_family_file_path_maps_accession_to_mirrored_family_file(tmp_path: Path) -> None:
    browser = import_module("geo_index.soft_browser.server")

    assert browser.family_file_path(tmp_path, "GSE271800") == (
        tmp_path / "GSE271nnn" / "GSE271800_family.soft.gz"
    )


def test_family_file_path_rejects_non_accession_paths(tmp_path: Path) -> None:
    browser = import_module("geo_index.soft_browser.server")

    with pytest.raises(ValueError, match="GSE accession"):
        browser.family_file_path(tmp_path, "../../private")


def test_parse_rg_matches_groups_snippets_by_gse() -> None:
    browser = import_module("geo_index.soft_browser.server")
    output = "\n".join(
        json.dumps(record)
        for record in [
            {
                "type": "match",
                "data": {
                    "path": {"text": "GSE271nnn/GSE271800_family.soft.gz"},
                    "lines": {"text": "!Series_title = Kidney inflammation response\n"},
                },
            },
            {
                "type": "match",
                "data": {
                    "path": {"text": "GSE271nnn/GSE271800_family.soft.gz"},
                    "lines": {"text": "!Sample_title = inflamed kidney\n"},
                },
            },
            {
                "type": "match",
                "data": {
                    "path": {"text": "GSE100nnn/GSE100101_family.soft.gz"},
                    "lines": {"text": "!Series_summary = control\n"},
                },
            },
        ]
    )

    assert browser.parse_rg_matches(output, "kidney") == [
        {
            "gse": "GSE271800",
            "snippets": [
                "!Series_title = Kidney inflammation response",
                "!Sample_title = inflamed kidney",
            ],
        },
        {"gse": "GSE100101", "snippets": ["!Series_summary = control"]},
    ]


def test_search_files_finds_literal_text_in_compressed_family_files(
    tmp_path: Path,
) -> None:
    browser = import_module("geo_index.soft_browser.server")
    _write_gz(
        browser.family_file_path(tmp_path, "GSE271800"),
        "!Series_title = Kidney inflammation response\n",
    )

    assert browser.search_files(tmp_path, "kidney inflammation") == {
        "results": [
            {
                "gse": "GSE271800",
                "snippets": ["!Series_title = Kidney inflammation response"],
            }
        ],
        "truncated": False,
    }


def test_search_files_caps_the_number_of_matching_series(tmp_path: Path) -> None:
    browser = import_module("geo_index.soft_browser.server")
    for accession in ("GSE100001", "GSE100002", "GSE100003"):
        _write_gz(
            browser.family_file_path(tmp_path, accession),
            "!Series_summary = common phrase\n",
        )

    response = browser.search_files(tmp_path, "common phrase", max_results=2)

    assert len(response["results"]) == 2
    assert response["truncated"] is True


def test_search_files_stops_after_ten_series_by_default(tmp_path: Path) -> None:
    browser = import_module("geo_index.soft_browser.server")
    for number in range(100001, 100012):
        _write_gz(
            browser.family_file_path(tmp_path, f"GSE{number}"),
            "!Series_summary = common phrase\n",
        )

    response = browser.search_files(tmp_path, "common phrase")

    assert len(response["results"]) == 10
    assert response["truncated"] is True


def test_file_endpoint_streams_metadata_file_by_default(tmp_path: Path) -> None:
    browser = import_module("geo_index.soft_browser.server")
    raw_dir = tmp_path / "raw"
    metadata_dir = tmp_path / "metadata"
    _write_gz(
        browser.family_file_path(raw_dir, "GSE271800"),
        "raw expression table\n",
    )
    _write_gz(
        browser.family_file_path(metadata_dir, "GSE271800"),
        "stripped metadata\n",
    )
    server = browser.make_server(raw_dir, metadata_dir, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/api/file?gse=GSE271800") as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
            assert response.read().decode() == "stripped metadata\n"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_file_endpoint_streams_raw_file_when_requested(tmp_path: Path) -> None:
    browser = import_module("geo_index.soft_browser.server")
    raw_dir = tmp_path / "raw"
    metadata_dir = tmp_path / "metadata"
    _write_gz(
        browser.family_file_path(raw_dir, "GSE271800"),
        "raw expression table\n",
    )
    _write_gz(
        browser.family_file_path(metadata_dir, "GSE271800"),
        "stripped metadata\n",
    )
    server = browser.make_server(raw_dir, metadata_dir, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urlopen(
            f"http://127.0.0.1:{port}/api/file?gse=GSE271800&raw=1"
        ) as response:
            assert response.read().decode() == "raw expression table\n"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_search_endpoint_uses_metadata_files_by_default(tmp_path: Path) -> None:
    browser = import_module("geo_index.soft_browser.server")
    raw_dir = tmp_path / "raw"
    metadata_dir = tmp_path / "metadata"
    _write_gz(
        browser.family_file_path(raw_dir, "GSE271800"),
        "!Series_summary = raw-only phrase\n",
    )
    _write_gz(
        browser.family_file_path(metadata_dir, "GSE271800"),
        "!Series_summary = metadata phrase\n",
    )
    server = browser.make_server(raw_dir, metadata_dir, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        query = urlencode({"q": "metadata phrase"})
        assert _get_json(f"http://127.0.0.1:{port}/api/search?{query}") == {
            "results": [
                {
                    "gse": "GSE271800",
                    "snippets": ["!Series_summary = metadata phrase"],
                }
            ],
            "truncated": False,
        }
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_search_endpoint_uses_raw_files_when_requested(tmp_path: Path) -> None:
    browser = import_module("geo_index.soft_browser.server")
    raw_dir = tmp_path / "raw"
    metadata_dir = tmp_path / "metadata"
    _write_gz(
        browser.family_file_path(raw_dir, "GSE271800"),
        "!Series_summary = raw-only phrase\n",
    )
    _write_gz(
        browser.family_file_path(metadata_dir, "GSE271800"),
        "!Series_summary = metadata phrase\n",
    )
    server = browser.make_server(raw_dir, metadata_dir, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        query = urlencode({"q": "raw-only phrase", "raw": "1"})
        assert _get_json(f"http://127.0.0.1:{port}/api/search?{query}") == {
            "results": [
                {
                    "gse": "GSE271800",
                    "snippets": ["!Series_summary = raw-only phrase"],
                }
            ],
            "truncated": False,
        }
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_root_serves_browser_with_both_raw_file_controls(tmp_path: Path) -> None:
    browser = import_module("geo_index.soft_browser.server")
    server = browser.make_server(tmp_path / "raw", tmp_path / "metadata", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        status, page = _get_text(f"http://127.0.0.1:{port}/")
        assert status == 200
        assert 'id="search-raw"' in page
        assert 'id="show-raw"' in page
        assert 'id="results"' in page
        assert 'id="viewer"' in page
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_root_search_ui_consumes_the_capped_response(tmp_path: Path) -> None:
    browser = import_module("geo_index.soft_browser.server")
    server = browser.make_server(tmp_path / "raw", tmp_path / "metadata", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        status, page = _get_text(f"http://127.0.0.1:{port}/")
        assert status == 200
        assert "render(payload.results, payload.truncated);" in page
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_root_search_ui_prevents_stale_requests_from_rendering(tmp_path: Path) -> None:
    browser = import_module("geo_index.soft_browser.server")
    server = browser.make_server(tmp_path / "raw", tmp_path / "metadata", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        status, page = _get_text(f"http://127.0.0.1:{port}/")
        assert status == 200
        assert "activeSearch?.abort();" in page
        assert "if (activeSearch !== request) return;" in page
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_parse_args_uses_the_project_soft_data_defaults() -> None:
    browser = import_module("geo_index.soft_browser.server")

    args = browser.parse_args(["--port", "8012"])

    assert args.host == "127.0.0.1"
    assert args.port == 8012
    assert args.raw_dir == Path("data/raw/soft")
    assert args.metadata_dir == Path("data/processed/soft_meta")


def test_project_registers_the_soft_browser_command() -> None:
    project_root = Path(__file__).parents[1]
    with (project_root / "pyproject.toml").open("rb") as fh:
        config = tomllib.load(fh)

    assert config["project"]["scripts"]["geo-soft-browser"] == (
        "geo_index.soft_browser.server:main"
    )
