import { FormEvent, useRef, useState } from "react";

import { DemoResponse, GEOscopeResult, NativeResult, SearchMode, searchDemo } from "../api";


const examples = [
  "transcriptomes of individual cells",
  "macrophage polarization",
  "drug that suppresses mTOR signaling",
];


function GEOscopeCard({ result, inNative }: { result: GEOscopeResult; inNative?: boolean }) {
  return (
    <article className={`result-card result-card--scope${inNative === false ? " result-card--novel" : ""}`}>
      <div className="result-card__topline">
        <span className="result-rank">{String(result.rank).padStart(2, "0")}</span>
        <a href={`https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=${result.gse}`} target="_blank" rel="noreferrer">
          {result.gse}
        </a>
        {inNative === false && <span className="miss-label">Not returned by GEO keyword search</span>}
      </div>
      <h4>{result.title ?? "Untitled GEO series"}</h4>
      {result.snippet && <p>{result.snippet}</p>}
      <div className="result-tags">
        {result.organism_ids.slice(0, 2).map((value) => <code key={value}>{value}</code>)}
        {result.assay_labels.slice(0, 2).map((value) => <code key={value}>{value}</code>)}
      </div>
    </article>
  );
}


function NativeCard({ result, rank }: { result: NativeResult; rank: number }) {
  return (
    <article className="result-card result-card--native">
      <div className="result-card__topline">
        <span className="result-rank">{String(rank).padStart(2, "0")}</span>
        <a href={`https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=${result.gse}`} target="_blank" rel="noreferrer">
          {result.gse}
        </a>
      </div>
      <h4>{result.title ?? "Untitled GEO series"}</h4>
      {result.summary && <p>{result.summary}</p>}
      <div className="result-tags">
        {result.taxon && <span>{result.taxon}</span>}
        {result.study_type && <span>{result.study_type}</span>}
      </div>
    </article>
  );
}


export function LiveComparison() {
  const [query, setQuery] = useState(examples[0]);
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [state, setState] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [data, setData] = useState<DemoResponse | null>(null);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  async function runSearch(event?: FormEvent) {
    event?.preventDefault();
    const normalized = query.trim();
    if (!normalized) {
      setError("Describe the studies you want to find.");
      setState("error");
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState("loading");
    setError("");
    try {
      const response = await searchDemo(normalized, mode, controller.signal);
      setData(response);
      setState("success");
      const params = new URLSearchParams(window.location.search);
      params.set("q", normalized);
      params.set("mode", mode);
      window.history.replaceState(null, "", `${window.location.pathname}?${params}`);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      setError(caught instanceof Error ? caught.message : "The live comparison could not be loaded.");
      setState("error");
    }
  }

  return (
    <section className="section live-demo" id="live-demo" aria-labelledby="demo-title">
      <div className="demo-intro">
        <div>
          <div className="section-kicker">LIVE PROOF / SAME QUERY</div>
          <h2 id="demo-title">Put keyword search beside semantic retrieval.</h2>
        </div>
        <p>One query. Native GEO on the left. GEOscope on the right. The difference is vocabulary drift made visible.</p>
      </div>

      <form className="search-console" role="search" onSubmit={runSearch}>
        <label htmlFor="demo-query">Describe the studies you want</label>
        <div className="search-console__controls">
          <input
            id="demo-query"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="e.g. transcriptomes of individual cells"
          />
          <select value={mode} onChange={(event) => setMode(event.target.value as SearchMode)} aria-label="Retrieval mode">
            <option value="hybrid">Hybrid</option>
            <option value="dense">Semantic only</option>
            <option value="bm25">BM25 only</option>
          </select>
          <button type="submit" disabled={state === "loading"}>
            {state === "loading" ? "Scanning…" : "Compare results"}
          </button>
        </div>
        <div className="example-queries" aria-label="Example queries">
          <span>Try:</span>
          {examples.map((example) => (
            <button type="button" key={example} onClick={() => setQuery(example)}>{example}</button>
          ))}
        </div>
      </form>

      <div className="search-status" aria-live="polite">
        {state === "loading" && "Searching native GEO and GEOscope…"}
        {state === "error" && <span>{error}</span>}
        {state === "idle" && "Ready for a live backend comparison."}
        {state === "success" && data && `${data.geoscope.results.length} GEOscope results compared with ${data.geo.results.length} native GEO results.`}
      </div>

      {state === "success" && data && (
        <div className="comparison-grid">
          <div className="result-column result-column--native">
            <div className="result-column__header">
              <div><span className="source-shape source-shape--geo" />Native GEO keyword search</div>
              <span>{data.geo.count === null ? "unavailable" : `${data.geo.count.toLocaleString()} total`}</span>
            </div>
            {data.geo.error && <div className="result-empty">{data.geo.error}</div>}
            {!data.geo.error && data.geo.results.length === 0 && <div className="result-empty">No native keyword results returned.</div>}
            {data.geo.results.map((result, index) => <NativeCard key={result.gse} result={result} rank={index + 1} />)}
          </div>

          <div className="comparison-divider" aria-hidden="true"><span>VS</span></div>

          <div className="result-column result-column--scope">
            <div className="result-column__header">
              <div><span className="source-shape source-shape--scope" />GEOscope</div>
              <span>{data.mode}</span>
            </div>
            {data.geoscope.results.length === 0 && <div className="result-empty">No GEOscope results returned. Try a broader description.</div>}
            {data.geoscope.results.map((result) => (
              <GEOscopeCard
                key={result.gse}
                result={result}
                inNative={data.membership?.[result.gse]}
              />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
