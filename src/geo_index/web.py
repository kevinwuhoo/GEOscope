"""Tiny local web UI: Elasticsearch hybrid retrieval vs GEO keyword search.

Side-by-side demo harness. Owns one Elasticsearch runtime, then per request
runs (a) Elasticsearch BM25/Gemini dense RRF and (b) a live GEO keyword search
via NCBI E-utilities (db=gds, restricted to Series), so you can eyeball what
GEO's own search returns for the same query vs what semantic retrieval finds.

    uv run python -m geo_index.web            # http://localhost:8000
    uv run python -m geo_index.web --port 8080
"""
from __future__ import annotations

import argparse
import json
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .elasticsearch_runtime import ElasticsearchRuntime
from .eutils import EutilsClient
from .search_models import SearchFilters, SearchResponse

_HTML = (Path(__file__).parent / "web_ui.html").read_text()

_runtime: ElasticsearchRuntime | None = None
_runtime_lock = threading.Lock()
_ncbi = EutilsClient()
_ncbi_lock = threading.Lock()


_WEB_FILTERS = {
    "organism_id": "organism_ids",
    "sex_id": "sex_ids",
    "assay_category": "assay_categories",
    "assay_label": "assay_labels",
}


def _parse_search_request(
    query_string: dict[str, list[str]],
) -> tuple[str, str, int, SearchFilters]:
    query = query_string.get("q", [""])[0].strip()
    mode = query_string.get("mode", ["hybrid"])[0]
    if mode not in {"hybrid", "dense", "bm25"}:
        mode = "hybrid"
    try:
        topk = max(1, min(50, int(query_string.get("topk", ["15"])[0])))
    except ValueError:
        topk = 15
    values = {
        internal: query_string.get(external, [])
        for external, internal in _WEB_FILTERS.items()
    }
    return query, mode, topk, SearchFilters.from_mapping(values)


def _serialize_search(
    response: SearchResponse, filters: SearchFilters
) -> dict[str, object]:
    return {
        "ours": list(response.hits),
        "filters": filters.as_dict(),
        "facets": {
            field: asdict(result)
            for field, result in response.facets.items()
        },
    }


def _our_search(
    query: str,
    mode: str,
    topk: int,
    filters: SearchFilters,
) -> SearchResponse:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = ElasticsearchRuntime()
        runtime = _runtime
    return runtime.search(query, mode=mode, topk=topk, filters=filters)


def _geo_keyword_search(query: str, topk: int) -> dict:
    """Live GEO keyword search via E-utilities (db=gds), Series only."""
    term = f"{query} AND gse[ETYP]"
    try:
        with _ncbi_lock:
            res = _ncbi.esearch("gds", term)
            if res.count == 0:
                return {"count": 0, "results": []}
            page = _ncbi.esummary_page("gds", res, 0, min(topk * 3, 100))
    except Exception as exc:  # network/NCBI hiccup — surface, don't crash the UI
        return {"count": -1, "error": str(exc)[:200], "results": []}
    results = []
    for uid in page.get("uids", []):
        d = page.get(uid, {})
        if str(d.get("entrytype", "")).upper() != "GSE":
            continue
        results.append(
            {
                "gse": d.get("accession") or "",
                "title": d.get("title"),
                "type": d.get("gdstype"),
                "taxon": d.get("taxon"),
                "summary": (d.get("summary") or "")[:240],
            }
        )
        if len(results) >= topk:
            break
    return {"count": res.count, "results": results}


def _geo_membership(query: str, accessions: list[str]) -> dict | None:
    """Which of `accessions` does GEO's keyword search for `query` actually return?

    One esearch restricting the query to our accessions ([ACCN]); membership is
    checked via the GEO gds UID mapping (uid = 200000000 + GSE number). Returns
    {gse: bool} or None if the check failed (network) so the UI can fall back.
    """
    accs = [a for a in accessions if a.startswith("GSE") and a[3:].isdigit()]
    if not accs:
        return {}
    term = f"({query}) AND (" + " OR ".join(f"{a}[ACCN]" for a in accs) + ")"
    try:
        with _ncbi_lock:
            ids = set(_ncbi.esearch_ids("gds", term, retmax=len(accs) + 10))
    except Exception:
        return None
    return {a: (str(200000000 + int(a[3:])) in ids) for a in accs}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._send(200, _HTML.encode(), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/search":
            qs = parse_qs(parsed.query, keep_blank_values=True)
            try:
                query, mode, topk, filters = _parse_search_request(qs)
            except ValueError as exc:
                body = json.dumps({"error": str(exc)}).encode()
                self._send(400, body, "application/json")
                return
            if not query:
                self._send(400, b'{"error":"empty query"}', "application/json")
                return
            response = _our_search(query, mode, topk, filters)
            ours = list(response.hits)
            membership = _geo_membership(query, [r["gse"] for r in ours])
            if membership is not None:
                for r in ours:
                    # True/False if checked, None if this accession wasn't checked
                    r["in_geo"] = membership.get(r["gse"])
            payload = _serialize_search(response, filters)
            payload.update(
                {
                    "query": query,
                    "mode": mode,
                    "geo": _geo_keyword_search(query, topk),
                }
            )
            self._send(200, json.dumps(payload).encode(), "application/json")
            return
        self._send(404, b"not found", "text/plain")

    def log_message(self, fmt: str, *args) -> None:  # quieter console
        return


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Elasticsearch/Gemini hybrid-vs-GEO-keyword demo UI."
    )
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="127.0.0.1")
    a = p.parse_args(argv)

    global _runtime
    print("connecting to Elasticsearch...", flush=True)
    _runtime = ElasticsearchRuntime()
    server = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"serving on http://{a.host}:{a.port}  (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if _runtime is not None:
            _runtime.close()
            _runtime = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
