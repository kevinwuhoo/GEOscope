const capabilities = [
  {
    verb: "Retrieve meaning",
    detail: "Fuse lexical precision with embedding recall across titles, summaries, designs, and sample metadata.",
    signal: "BM25 + dense",
  },
  {
    verb: "Resolve vocabulary",
    detail: "Turn submitter-authored values into controlled organisms, sex values, and assay labels.",
    signal: "ontology-backed",
  },
  {
    verb: "Constrain precisely",
    detail: "Filter and facet with exact identifiers, then expose the same bounded search operations over MCP.",
    signal: "filters + MCP",
  },
];


export function CapabilityFlow() {
  return (
    <section className="section capability" id="how-it-works" aria-labelledby="capability-title">
      <div className="section-kicker">ONE RETRIEVAL LOOP</div>
      <div className="section-heading">
        <h2 id="capability-title">Recall without surrendering precision.</h2>
      </div>
      <div className="capability-flow">
        {capabilities.map((capability, index) => (
          <article className="capability-step" key={capability.verb}>
            <div className="capability-step__marker" aria-hidden="true">
              <span>{index + 1}</span>
            </div>
            <div>
              <code>{capability.signal}</code>
              <h3>{capability.verb}</h3>
              <p>{capability.detail}</p>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
