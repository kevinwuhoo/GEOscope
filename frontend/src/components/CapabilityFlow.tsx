const capabilities = [
  {
    verb: "Search language and meaning",
    detail: "Combine BM25 keyword precision with embedding-based similarity across NCBI GEO titles, summaries, designs, and sample metadata.",
    signal: "BM25 + embeddings",
  },
  {
    verb: "Structure the metadata",
    detail: "Extract and normalize useful biological concepts from submitter-authored metadata so relevant studies are easier to compare.",
    signal: "extraction + normalization",
  },
  {
    verb: "Narrow to evidence",
    detail: "Use exact filters and facets to turn a broad similarity search into a ranked, inspectable set of NCBI GEO series.",
    signal: "filters + facets",
  },
];


export function CapabilityFlow() {
  return (
    <section className="section capability" id="how-it-works" aria-labelledby="capability-title">
      <div className="section-heading">
        <h2 id="capability-title">From a specific question to inspectable evidence.</h2>
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
