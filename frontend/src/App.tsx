import { AccessionScope } from "./components/AccessionScope";
import { CapabilityFlow } from "./components/CapabilityFlow";
import { LiveComparison } from "./components/LiveComparison";
import { ResearcherExample } from "./components/ResearcherExample";

import "./styles.css";


const features = [
  "Hybrid BM25 + embedding retrieval",
  "Semantic similarity search",
  "Structured metadata extraction and normalization",
  "Exact filters and facets",
  "An MCP server for your agent",
];


export default function App() {
  return (
    <div className="site-shell">
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="site-header">
        <a className="wordmark" href="#top" aria-label="GEOscope home">
          <span className="wordmark__geo">GEO</span><span>scope</span>
          <i aria-hidden="true" />
        </a>
        <nav aria-label="Primary navigation">
          <a href="#live-demo">Live proof</a>
          <a href="#how-it-works">How it works</a>
          <a href="#mcp">MCP for agents</a>
        </nav>
        <a className="header-cta" href="#live-demo">Open live demo <span aria-hidden="true">↘</span></a>
      </header>

      <main id="main">
        <section className="hero" id="top" aria-labelledby="hero-title">
          <div className="hero-copy">
            <h1 id="hero-title">See what NCBI GEO search misses.</h1>
            <p className="hero-lede">
              GEOscope turns inconsistent genomics metadata into precise,
              ontology-aware discovery—so the study you need is not hidden behind
              the words its submitter happened to use.
            </p>
            <div className="hero-actions">
              <a className="primary-cta" href="#live-demo">Try a live comparison <span aria-hidden="true">↓</span></a>
              <a className="text-link" href="#how-it-works">See how it works</a>
            </div>
            <div className="hero-proof">
              <div><strong>GSE</strong><span>series-level metadata</span></div>
              <div><strong>Hybrid</strong><span>BM25 + embeddings</span></div>
              <div><strong>MCP</strong><span>agent-ready retrieval</span></div>
            </div>
          </div>
          <AccessionScope />
        </section>

        <div className="signal-strip" aria-label="GEOscope capabilities" tabIndex={0}>
          {[false, true].map((duplicate) => (
            <div
              className="signal-strip__track"
              aria-hidden={duplicate || undefined}
              key={String(duplicate)}
            >
              {features.map((feature) => (
                <span className="signal-strip__item" key={feature}>
                  {feature}<i aria-hidden="true" />
                </span>
              ))}
            </div>
          ))}
        </div>

        <LiveComparison />
        <CapabilityFlow />
        <ResearcherExample />

        <section className="closing" aria-labelledby="closing-title">
          <div className="closing-orbit" aria-hidden="true"><i /><i /><i /></div>
          <div>
            <h2 id="closing-title">Ask naturally.<br />Filter exactly.</h2>
          </div>
          <div className="closing-copy">
            <p>
              Search the metadata people wrote, through the concepts they meant.
              Find relevant NCBI GEO series without guessing the submitter's vocabulary.
            </p>
            <a className="closing-cta" href="#live-demo">Run the comparison <span aria-hidden="true">↗</span></a>
          </div>
        </section>
      </main>

      <footer>
        <a className="wordmark wordmark--footer" href="#top"><span className="wordmark__geo">GEO</span><span>scope</span></a>
        <p>Hybrid, semantic discovery for NCBI GEO metadata.</p>
      </footer>
    </div>
  );
}
