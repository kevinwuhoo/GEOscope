const mappings = [
  {
    label: "organism",
    raw: ["human", "H. sapiens", "Homo sapiens"],
    normalized: "NCBITaxon:9606",
    note: "Homo sapiens",
  },
  {
    label: "sex",
    raw: ["M", "male", "Male"],
    normalized: "PATO:0000384",
    note: "male",
  },
  {
    label: "assay",
    raw: ["10x Chromium", "single cell RNA", "scRNA-seq"],
    normalized: "scRNA-seq",
    note: "controlled assay label",
  },
];


export function NormalizationProof() {
  return (
    <section className="section normalization" id="normalization" aria-labelledby="normalization-title">
      <div className="section-kicker">NOT JUST VECTOR SEARCH</div>
      <div className="section-heading section-heading--split">
        <h2 id="normalization-title">Messy in. Comparable out.</h2>
        <p>
          Semantic recall finds related studies. Controlled identifiers make the results
          safe to filter, count, and hand to an agent.
        </p>
      </div>

      <div className="mapping-table" role="table" aria-label="Metadata normalization examples">
        {mappings.map((mapping) => (
          <div className="mapping-row" role="row" key={mapping.label}>
            <div className="mapping-field" role="cell">{mapping.label}</div>
            <div className="mapping-raw" role="cell">
              {mapping.raw.map((value) => <span key={value}>{value}</span>)}
            </div>
            <div className="mapping-arrow" aria-hidden="true">→</div>
            <div className="mapping-target" role="cell">
              <code>{mapping.normalized}</code>
              <span>{mapping.note}</span>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
