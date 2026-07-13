import { FormEvent, useRef, useState } from "react";

import { DemoResponse, GEOscopeResult, NativeResult, searchDemo } from "../api";


const examples = [
  "human breast cancer transcriptomics before and after neoadjuvant chemotherapy with treatment response data",
  "liver transcriptomics comparing nonalcoholic steatohepatitis with healthy human controls",
  "mouse skeletal muscle gene expression after endurance exercise in insulin resistance",
];


function GEOscopeCard({ result, inNative }: { result: GEOscopeResult; inNative?: boolean }) {
  const title = result.title ?? "Untitled NCBI GEO series";
  return (
    <article
      className={`result-card result-card--scope${inNative === false ? " result-card--novel" : ""}`}
      aria-label={`GEOscope result ${result.rank}: ${title}`}
    >
      <div className="result-card__topline">
        <span className="result-rank">{String(result.rank).padStart(2, "0")}</span>
        <a href={`https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=${result.gse}`} target="_blank" rel="noreferrer">
          {result.gse}
        </a>
      </div>
      <h4>{title}</h4>
      {result.snippet && <p>{result.snippet}</p>}
      <div className="result-tags">
        {result.organism_ids.slice(0, 2).map((value) => <code key={value}>{value}</code>)}
        {result.assay_labels.slice(0, 2).map((value) => <code key={value}>{value}</code>)}
      </div>
    </article>
  );
}


function NativeCard({ result, rank }: { result: NativeResult; rank: number }) {
  const title = result.title ?? "Untitled NCBI GEO series";
  return (
    <article
      className="result-card result-card--native"
      aria-label={`NCBI GEO result ${rank}: ${title}`}
    >
      <div className="result-card__topline">
        <span className="result-rank">{String(rank).padStart(2, "0")}</span>
        <a href={`https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=${result.gse}`} target="_blank" rel="noreferrer">
          {result.gse}
        </a>
      </div>
      <h4>{title}</h4>
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
      const response = await searchDemo(normalized, "hybrid", controller.signal);
      setData(response);
      setState("success");
      const params = new URLSearchParams(window.location.search);
      params.set("q", normalized);
      params.set("mode", "hybrid");
      window.history.replaceState(null, "", `${window.location.pathname}?${params}`);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      setError(caught instanceof Error ? caught.message : "The live comparison could not be loaded.");
      setState("error");
    }
  }

  const comparisonRows = data
    ? Array.from(
        { length: Math.max(data.geoscope.results.length, data.geo.results.length, 1) },
        (_, index) => ({
          geoscope: data.geoscope.results[index],
          native: data.geo.results[index],
        }),
      )
    : [];

  return (
    <section className="section live-demo" id="live-demo" aria-labelledby="demo-title">
      <div className="demo-intro">
        <div>
          <h2 id="demo-title">Compare retrieval, result by result.</h2>
        </div>
        <p>See the difference for yourself. GEOscope results on the left, NCBI GEO results on the right.</p>
      </div>

      <form className="search-console" role="search" onSubmit={runSearch}>
        <label htmlFor="demo-query">Describe the studies you want</label>
        <div className="search-console__controls">
          <input
            id="demo-query"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="e.g. breast cancer before and after neoadjuvant chemotherapy"
          />
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
        {state === "loading" && "Searching GEOscope and NCBI GEO…"}
        {state === "error" && <span>{error}</span>}
        {state === "idle" && "Ready for a live backend comparison."}
        {state === "success" && data && `${data.geoscope.results.length} GEOscope results compared with ${data.geo.results.length} NCBI GEO results.`}
      </div>

      {state === "success" && data && (
        <div className="comparison-grid">
          <div className="comparison-header">
            <div className="result-column__header result-column__header--scope">
              <div><span className="source-shape source-shape--scope" />GEOscope</div>
              <span>Hybrid · BM25 + embeddings</span>
            </div>
            <div className="result-column__header result-column__header--native">
              <div><span className="source-shape source-shape--geo" />NCBI GEO keyword search</div>
              <span>{data.geo.count === null ? "unavailable" : `${data.geo.count.toLocaleString()} total`}</span>
            </div>
          </div>
          <div className="comparison-results">
            {comparisonRows.map((row, index) => (
              <div className="comparison-row" key={row.geoscope?.gse ?? row.native?.gse ?? index}>
                <div className="comparison-cell comparison-cell--scope">
                  {row.geoscope ? (
                    <GEOscopeCard
                      result={row.geoscope}
                      inNative={data.membership?.[row.geoscope.gse]}
                    />
                  ) : (
                    <div className="result-empty">No GEOscope result at this rank.</div>
                  )}
                </div>
                <div className="comparison-cell comparison-cell--native">
                  {row.native ? (
                    <NativeCard result={row.native} rank={index + 1} />
                  ) : (
                    <div className="result-empty">
                      {index === 0 && data.geo.error
                        ? data.geo.error
                        : "No NCBI GEO result at this rank."}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
