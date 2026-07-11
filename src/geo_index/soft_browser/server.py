"""Serve a local browser for compressed GEO SOFT family files."""

import argparse
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import subprocess
import threading
from urllib.parse import parse_qs, urlparse

from geo_index.fetch_soft import soft_path

_ACCESSION = re.compile(r"GSE\d+\Z")
_FAMILY_FILE = re.compile(r"(GSE\d+)_family\.soft\.gz\Z")
_MAX_SNIPPETS = 2
_MAX_RESULTS = 200
_SEARCH_TIMEOUT_SECONDS = 30
_HTML = (Path(__file__).with_name("ui.html")).read_text()


def family_file_path(root: Path, accession: str) -> Path:
    """Return the mirrored SOFT family-file path for a GSE accession."""
    if not _ACCESSION.fullmatch(accession):
        raise ValueError("expected a GSE accession")
    return soft_path(root, accession)


def _short_snippet(line: str, query: str, limit: int = 180) -> str:
    text = " ".join(line.split())
    if len(text) <= limit:
        return text
    start = max(0, text.lower().find(query.lower()) - 70)
    end = min(len(text), start + limit)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def parse_rg_matches(output: str, query: str) -> list[dict[str, object]]:
    """Group ripgrep JSON match events into compact per-series results."""
    results: dict[str, list[str]] = {}
    for line in output.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") != "match":
            continue
        data = record.get("data", {})
        path = data.get("path", {}).get("text", "")
        match = _FAMILY_FILE.search(path)
        text = data.get("lines", {}).get("text", "")
        if not match or not text:
            continue
        snippets = results.setdefault(match.group(1), [])
        if len(snippets) < _MAX_SNIPPETS:
            snippets.append(_short_snippet(text, query))
    return [{"gse": gse, "snippets": snippets} for gse, snippets in results.items()]


def search_files(
    root: Path,
    query: str,
    *,
    max_results: int = _MAX_RESULTS,
) -> dict[str, object]:
    """Search compressed family SOFT files below ``root`` with ripgrep."""
    query = query.strip()
    if not query:
        raise ValueError("search query is empty")
    if max_results < 1:
        raise ValueError("max_results must be positive")
    if not root.is_dir():
        return {"results": [], "truncated": False}
    try:
        process = subprocess.Popen(
            [
                "rg",
                "--json",
                "--search-zip",
                "--fixed-strings",
                "--ignore-case",
                "--glob",
                "*_family.soft.gz",
                "--max-count",
                str(_MAX_SNIPPETS),
                "--",
                query,
                ".",
            ],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ripgrep (rg) is not installed") from exc

    timed_out = threading.Event()

    def stop_timed_out_search() -> None:
        if process.poll() is None:
            timed_out.set()
            process.kill()

    timer = threading.Timer(_SEARCH_TIMEOUT_SECONDS, stop_timed_out_search)
    timer.daemon = True
    timer.start()
    results: dict[str, list[str]] = {}
    truncated = False
    assert process.stdout is not None
    try:
        for line in process.stdout:
            parsed = parse_rg_matches(line, query)
            if not parsed:
                continue
            result = parsed[0]
            accession = str(result["gse"])
            if accession not in results and len(results) >= max_results:
                truncated = True
                process.terminate()
                break
            snippets = results.setdefault(accession, [])
            for snippet in result["snippets"]:
                if len(snippets) < _MAX_SNIPPETS:
                    snippets.append(str(snippet))
    finally:
        timer.cancel()
        process.stdout.close()
        process.wait()

    assert process.stderr is not None
    error = process.stderr.read().strip()
    process.stderr.close()
    if timed_out.is_set():
        raise RuntimeError("search timed out")
    if not truncated and process.returncode not in {0, 1}:
        raise RuntimeError(error or "ripgrep search failed")
    return {
        "results": [
            {"gse": accession, "snippets": snippets}
            for accession, snippets in results.items()
        ],
        "truncated": truncated,
    }


def make_server(
    raw_dir: Path,
    metadata_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8001,
) -> ThreadingHTTPServer:
    """Create a local HTTP server bound to a raw and metadata SOFT tree."""

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _stream_file(self, path: Path) -> None:
            try:
                source = gzip.open(path, "rt", encoding="utf-8", errors="replace")
            except OSError as exc:
                self._send(500, f"could not open SOFT file: {exc}".encode(), "text/plain")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            with source:
                while chunk := source.read(64 * 1024):
                    self.wfile.write(chunk.encode())

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/index.html"}:
                self._send(200, _HTML.encode(), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/search":
                search_query = parse_qs(parsed.query)
                query = search_query.get("q", [""])[0]
                root = raw_dir if search_query.get("raw", ["0"])[0] == "1" else metadata_dir
                try:
                    results = search_files(root, query)
                except ValueError as exc:
                    self._send(400, str(exc).encode(), "text/plain")
                    return
                except RuntimeError as exc:
                    self._send(500, str(exc).encode(), "text/plain")
                    return
                self._send(200, json.dumps(results).encode(), "application/json")
                return
            if parsed.path != "/api/file":
                self._send(404, b"not found", "text/plain")
                return
            query = parse_qs(parsed.query)
            accession = query.get("gse", [""])[0]
            root = raw_dir if query.get("raw", ["0"])[0] == "1" else metadata_dir
            try:
                path = family_file_path(root, accession)
            except ValueError as exc:
                self._send(400, str(exc).encode(), "text/plain")
                return
            if not path.is_file():
                self._send(404, b"SOFT file not found", "text/plain")
                return
            self._stream_file(path)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options for the local browser."""
    parser = argparse.ArgumentParser(description="Browse downloaded GEO SOFT files.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw/soft"))
    parser.add_argument(
        "--metadata-dir", type=Path, default=Path("data/processed/soft_meta")
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Start the local SOFT browser."""
    args = parse_args(argv)
    server = make_server(
        args.raw_dir,
        args.metadata_dir,
        host=args.host,
        port=args.port,
    )
    print(f"serving SOFT browser at http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
