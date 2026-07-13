import { FormEvent, useRef, useState } from "react";

import { DemoResponse, GEOscopeResult, NativeResult, searchDemo } from "../api";


const examples = [
  "breast tumors before and after neoadjuvant chemotherapy",
  "NASH liver transcriptomes compared with healthy controls",
  "PI3K signaling in insulin-resistant skeletal muscle",
];


function ncbiGeoSearchUrl(query: string) {
  const params = new URLSearchParams({ term: `${query} AND gse[ETYP]` });
  return `https://www.ncbi.nlm.nih.gov/gds/?${params.toString()}`;
}


type ResultTag = {
  kind: "normalized" | "source";
  text: string;
  title?: string;
};


function geoscopeResultTags(result: GEOscopeResult): ResultTag[] {
  const organismValues = Array.from(
    { length: Math.max(result.organism_ids.length, result.organism_labels.length) },
    (_, index) => (result.organism_labels[index] ?? result.organism_ids[index])?.trim(),
  ).filter((value): value is string => Boolean(value));
  const uniqueOrganisms = organismValues.filter(
    (value, index) => organismValues.indexOf(value) === index,
  );
  const organism = uniqueOrganisms.length > 0
    ? `${uniqueOrganisms.slice(0, 2).join(" · ")}${
        uniqueOrganisms.length > 2 ? ` +${uniqueOrganisms.length - 2}` : ""
      }`
    : null;
  const assay = result.assay_labels.find((value) => value.trim())?.trim();
  const studyType = result.study_type?.trim();
  const organismIds = result.organism_ids.map((value) => value.trim()).filter(Boolean);
  const organismTitle = organism && organismIds.length > 0
    && organism !== organismIds.join(" · ")
    ? organismIds.join(", ")
    : undefined;
  const candidates: Array<ResultTag | null> = [
    organism
      ? { kind: "normalized", text: organism, title: organismTitle }
      : null,
    assay ? { kind: "normalized", text: assay } : null,
    studyType ? { kind: "source", text: studyType } : null,
  ];
  const seen = new Set<string>();
  return candidates
    .filter((tag): tag is ResultTag => tag !== null)
    .filter((tag) => {
      const key = tag.text.toLocaleLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 3);
}


function GEOscopeCard({ result, inNative }: { result: GEOscopeResult; inNative?: boolean }) {
  const title = result.title ?? "Untitled NCBI GEO series";
  const tags = geoscopeResultTags(result);
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
      {tags.length > 0 && (
        <div className="result-tags">
          {tags.map((tag) => (
            <span
              className={`result-tag result-tag--${tag.kind}`}
              key={`${tag.kind}:${tag.text}`}
              title={tag.title}
            >
              {tag.text}
            </span>
          ))}
        </div>
      )}
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
  const [query, setQuery] = useState("");
  const [state, setState] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [data, setData] = useState<DemoResponse | null>(null);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  async function runSearch(event?: FormEvent, selectedQuery?: string) {
    event?.preventDefault();
    const normalized = (selectedQuery ?? query).trim();
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
      const response = await searchDemo(normalized, controller.signal);
      setData(response);
      setState("success");
      const params = new URLSearchParams(window.location.search);
      params.set("q", normalized);
      params.delete("mode");
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
          <h2 id="demo-title">Search the same research question two ways.</h2>
        </div>
        <p>
          Enter a research question, then compare GEOscope’s hybrid metadata search
          with the literal keyword results from NCBI GEO.
        </p>
      </div>

      <form className="search-console" role="search" onSubmit={runSearch}>
        <label htmlFor="demo-query">Describe the studies you want</label>
        <div className="search-console__controls">
          <input
            id="demo-query"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Describe a disease, treatment, pathway, assay, or comparison"
          />
          <button type="submit" disabled={state === "loading"}>
            {state === "loading" ? "Scanning…" : "Compare results"}
          </button>
        </div>
        <div className="example-queries" aria-label="Example queries">
          <span>Try:</span>
          {examples.map((example) => (
            <button
              type="button"
              key={example}
              onClick={() => {
                setQuery(example);
                void runSearch(undefined, example);
              }}
            >
              {example}
            </button>
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
              <div className="native-search-actions">
                <span>{data.geo.count === null ? "unavailable" : `${data.geo.count.toLocaleString()} total`}</span>
                <a
                  href={ncbiGeoSearchUrl(data.query)}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open this search on NCBI GEO ↗
                </a>
              </div>
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
