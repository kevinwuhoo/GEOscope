import { AccessionScope } from "./components/AccessionScope";
import { CapabilityFlow } from "./components/CapabilityFlow";
import { LiveComparison } from "./components/LiveComparison";
import { NormalizationProof } from "./components/NormalizationProof";
import { ResearcherExample } from "./components/ResearcherExample";

import "./styles.css";


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
          <a href="#normalization">Normalization</a>
          <a href="#how-it-works">How it works</a>
        </nav>
        <a className="header-cta" href="#live-demo">Open live demo <span aria-hidden="true">↘</span></a>
      </header>

      <main id="main">
        <section className="hero" id="top" aria-labelledby="hero-title">
          <div className="hero-copy">
            <div className="hero-index"><span>GEO / METADATA DISCOVERY</span><span>v0.1</span></div>
            <h1 id="hero-title">See what GEO search misses.</h1>
            <p className="hero-lede">
              GEOscope turns inconsistent genomics metadata into precise,
              ontology-aware discovery—so the study you need is not hidden behind
              the words its submitter happened to use.
            </p>
            <div className="hero-actions">
              <a className="primary-cta" href="#live-demo">Try a live comparison <span aria-hidden="true">↓</span></a>
              <a className="text-link" href="#normalization">See the ontology layer</a>
            </div>
            <div className="hero-proof">
              <div><strong>GSE</strong><span>series-level metadata</span></div>
              <div><strong>3-way</strong><span>hybrid · semantic · lexical</span></div>
              <div><strong>MCP</strong><span>agent-ready retrieval</span></div>
            </div>
          </div>
          <AccessionScope />
        </section>

        <div className="signal-strip" aria-label="GEOscope capabilities">
          <span>SEMANTIC RECALL</span><i />
          <span>CONTROLLED VOCABULARIES</span><i />
          <span>EXACT FACETS</span><i />
          <span>FULL GEO METADATA</span>
        </div>

        <LiveComparison />
        <NormalizationProof />
        <CapabilityFlow />
        <ResearcherExample />

        <section className="closing" aria-labelledby="closing-title">
          <div className="closing-orbit" aria-hidden="true"><i /><i /><i /></div>
          <div>
            <div className="section-kicker">THE RETRIEVAL LAYER FOR GEO</div>
            <h2 id="closing-title">Ask naturally.<br />Filter exactly.</h2>
          </div>
          <div className="closing-copy">
            <p>
              Search the metadata people wrote, through the concepts they meant.
              Then use the same bounded operations from a human interface or an MCP client.
            </p>
            <a className="closing-cta" href="#live-demo">Run the comparison <span aria-hidden="true">↗</span></a>
          </div>
        </section>
      </main>

      <footer>
        <a className="wordmark wordmark--footer" href="#top"><span className="wordmark__geo">GEO</span><span>scope</span></a>
        <p>Ontology-aware semantic discovery for NCBI GEO metadata.</p>
        <span className="footer-note">PROTOTYPE / BUILT FOR EVIDENCE</span>
      </footer>
    </div>
  );
}
