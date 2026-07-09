"""Tiny local web UI: our Postgres hybrid retrieval vs NCBI GEO keyword search.

Side-by-side demo harness. Loads the embedding model once at startup, then per
request runs (a) our pg_search+pgvector hybrid and (b) a live GEO keyword search
via NCBI E-utilities (db=gds, restricted to Series), so you can eyeball what
GEO's own search returns for the same query vs what semantic retrieval finds.

    uv run python -m geo_index.web            # http://localhost:8000
    uv run python -m geo_index.web --port 8080
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import pg_hybrid
from .eutils import EutilsClient

_HTML = (Path(__file__).parent / "web_ui.html").read_text()

_model = None
_model_lock = threading.Lock()
_ncbi = EutilsClient()
_ncbi_lock = threading.Lock()


def _our_search(query: str, mode: str, topk: int) -> list[dict]:
    global _model
    qv = None
    if mode != "bm25":
        with _model_lock:
            if _model is None:
                _model = pg_hybrid.load_model()
            qv = pg_hybrid.embed_query(_model, query)
    conn = pg_hybrid._connect()
    try:
        return pg_hybrid.search_rows(conn, query, qv=qv, mode=mode, topk=topk)
    finally:
        conn.close()


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
            qs = parse_qs(parsed.query)
            query = (qs.get("q", [""])[0]).strip()
            mode = qs.get("mode", ["hybrid"])[0]
            if mode not in ("hybrid", "dense", "bm25"):
                mode = "hybrid"
            try:
                topk = max(1, min(50, int(qs.get("topk", ["15"])[0])))
            except ValueError:
                topk = 15
            if not query:
                self._send(400, b'{"error":"empty query"}', "application/json")
                return
            ours = _our_search(query, mode, topk)
            membership = _geo_membership(query, [r["gse"] for r in ours])
            if membership is not None:
                for r in ours:
                    # True/False if checked, None if this accession wasn't checked
                    r["in_geo"] = membership.get(r["gse"])
            payload = {
                "query": query,
                "mode": mode,
                "ours": ours,
                "geo": _geo_keyword_search(query, topk),
            }
            self._send(200, json.dumps(payload).encode(), "application/json")
            return
        self._send(404, b"not found", "text/plain")

    def log_message(self, fmt: str, *args) -> None:  # quieter console
        return


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="GEO hybrid-vs-keyword demo UI.")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="127.0.0.1")
    a = p.parse_args(argv)

    global _model
    print("loading embedding model (once)...", flush=True)
    _model = pg_hybrid.load_model()
    server = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"serving on http://{a.host}:{a.port}  (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
