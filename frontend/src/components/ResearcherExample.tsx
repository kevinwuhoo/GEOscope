export function ResearcherExample() {
  return (
    <section className="section researcher" aria-labelledby="researcher-title">
      <div className="researcher-copy">
        <h2 id="researcher-title">
          “Find human breast cancer transcriptomics before and after neoadjuvant
          chemotherapy, including treatment response.”
        </h2>
        <p>
          GEOscope keeps the biological intent in hybrid retrieval while turning
          extracted metadata into explicit, inspectable filters.
        </p>
      </div>
      <div className="query-receipt" aria-label="Structured version of the researcher query">
        <dl>
          <div>
            <dt>semantic text</dt>
            <dd>neoadjuvant chemotherapy and treatment response</dd>
          </div>
          <div>
            <dt>organism</dt>
            <dd><code>NCBITaxon:9606</code></dd>
          </div>
          <div>
            <dt>disease</dt>
            <dd>breast cancer</dd>
          </div>
          <div>
            <dt>result</dt>
            <dd>ranked NCBI GEO series + facets</dd>
          </div>
        </dl>
      </div>
    </section>
  );
}
