export function ResearcherExample() {
  return (
    <section className="section researcher" aria-labelledby="researcher-title">
      <div className="researcher-copy">
        <div className="section-kicker">A RESEARCHER ASKS</div>
        <h2 id="researcher-title">“Find human single-cell RNA studies involving peripheral blood.”</h2>
        <p>
          GEOscope keeps the fuzzy biological intent in semantic search while turning
          recognized constraints into explicit, inspectable filters.
        </p>
      </div>
      <div className="query-receipt" aria-label="Structured version of the researcher query">
        <div className="query-receipt__header">
          <span>QUERY RECEIPT</span>
          <span>auditable</span>
        </div>
        <dl>
          <div>
            <dt>semantic text</dt>
            <dd>peripheral blood studies</dd>
          </div>
          <div>
            <dt>organism</dt>
            <dd><code>NCBITaxon:9606</code></dd>
          </div>
          <div>
            <dt>assay label</dt>
            <dd><code>scRNA-seq</code></dd>
          </div>
          <div>
            <dt>result</dt>
            <dd>ranked GEO series + facets</dd>
          </div>
        </dl>
      </div>
    </section>
  );
}
