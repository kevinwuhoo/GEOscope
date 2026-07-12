# GEOscope Marketing Page Design

## Purpose

Build a mobile-first marketing page for **GEOscope**, the ontology-aware
semantic search experience for NCBI GEO metadata. The primary audience is a
hackathon judge who must understand the novelty and see convincing evidence in
under a minute. Genomics researchers are the secondary audience, so the page
must use real GEO terminology, realistic searches, and accurate ontology
examples rather than generic AI claims.

The page has one job: move a visitor from **"GEO keyword search misses relevant
studies"** to **"I want to try GEOscope's live comparison."**

## Approved product story

The lead message is:

> **See what GEO search misses.**
>
> GEOscope turns inconsistent genomics metadata into precise,
> ontology-aware discovery.

The proof is an interactive query such as **"transcriptomes of individual
cells."** GEOscope can surface studies described with vocabulary such as
`10x Chromium`, `Drop-seq`, or `Smart-seq2` even when the literal query words
are absent. Below that retrieval proof, the page shows how raw values such as
`human`, `H. sapiens`, and `Homo sapiens` collapse to the controlled identifier
`NCBITaxon:9606`, enabling exact filters and facets.

Claims remain bounded to capabilities documented or implemented in this
repository. The page does not claim that every GEO field is normalized, that
retrieval is perfect, or that expression matrices are indexed. It explicitly
describes GEOscope as a metadata search system.

## Visual direction

### Subject, audience, and single job

- **Concrete subject:** searching noisy functional-genomics study metadata.
- **Primary audience:** hackathon judges evaluating novelty, usefulness, and
  execution.
- **Secondary audience:** genomics researchers who recognize GEO accessions,
  assay names, taxonomies, and ontology identifiers.
- **Single job:** make the live comparison irresistible to try.

### Design concept: the accession scope

The page uses the visual language of GEO itself: accession labels, metadata
fragments, controlled identifiers, result ranks, and compact scientific
annotations. Its signature element is an interactive **accession scope** in the
hero. The scope behaves like an optical instrument trained on a query: messy
metadata phrases enter at the edge, while ranked GSE accessions and normalized
concept chips resolve in the focal area. Submitting the query turns this
composed proof into the real live search comparison.

This is the page's one aesthetic risk. The rest of the layout stays disciplined
and quiet so the scope reads as an instrument rather than decoration.

### Color tokens

- **Laboratory paper** — `#F3F7F4`: cool, low-glare page ground.
- **Carbon** — `#102321`: primary text and dark panels.
- **Specimen white** — `#FCFFFD`: elevated surfaces.
- **Cobalt signal** — `#315CFF`: links, focus, and semantic retrieval.
- **Ontology lime** — `#B9E769`: normalized concepts and successful matches.
- **GEO amber** — `#E3A43B`: native GEO keyword results and comparison labels.

The palette deliberately avoids gradients and the common cream/terracotta or
black/acid-green landing-page defaults. Color encodes source and meaning:
amber always means native GEO, cobalt means semantic retrieval, and lime means
normalized structured data.

### Typography

- **Display:** Bricolage Grotesque Variable, used only for the GEOscope mark and
  thesis-sized headlines.
- **Body:** Atkinson Hyperlegible, chosen for readable scientific explanations
  and mobile copy.
- **Data:** IBM Plex Mono, used for GSE accessions, ontology IDs, result ranks,
  query fragments, and interface labels.

Fonts are bundled through Fontsource packages so the page does not depend on a
third-party font CDN at runtime.

### Layout sketch

Desktop:

```text
+--------------------------------------------------------------+
| GEOscope                         How it works   Open live demo |
+--------------------------------------------------------------+
| SEE WHAT GEO SEARCH MISSES.     /--------------------------\  |
| Short evidence-led pitch       |     ACCESSION SCOPE        | |
| [Try a live search]            | query -> GSEs + concepts   | |
| corpus / metadata proof        \--------------------------/  |
+--------------------------------------------------------------+
| Native GEO keyword results  <comparison rail>  GEOscope      |
+--------------------------------------------------------------+
| raw phrases -----> normalized ontology-backed values         |
+--------------------------------------------------------------+
| semantic recall  +  controlled facets  +  MCP access         |
+--------------------------------------------------------------+
| researcher query -> explicit filters -> ranked studies       |
+--------------------------------------------------------------+
| final live-demo invitation                                  ->|
+--------------------------------------------------------------+
```

Mobile:

```text
+---------------------------+
| GEOscope        Demo      |
| SEE WHAT GEO SEARCH       |
| MISSES.                   |
| pitch + CTA               |
| [accession scope card]    |
| [query input]             |
+---------------------------+
| GEO keyword               |
| result cards              |
|       vs.                 |
| GEOscope result cards     |
+---------------------------+
| raw -> normalized rows    |
| capability cards          |
| researcher example       |
| final CTA                 |
+---------------------------+
```

No section relies on horizontal scrolling. Comparison columns become a labeled
vertical sequence below 760 px. Controls remain at least 44 px high, long
accessions wrap safely, and the hero copy does not use viewport-sized text that
overflows narrow phones.

### Motion

One orchestrated page-load sequence focuses the accession scope: metadata
fragments enter, the focal ring settles, and result accessions resolve. Search
submission uses a restrained scan-line transition while results load. Other
sections use only small hover and focus responses.

When `prefers-reduced-motion: reduce` is active, all translations, scans, and
staggered reveals are disabled; content appears immediately.

### Frontend-design self-critique

The initial scientific-editorial proposal risked becoming the common warm-paper
and serif landing-page template. The revised direction removes the generic
serif/editorial treatment and derives its identity from GEO accessions,
ontology identifiers, metadata evidence, and an optical search instrument. The
signature scope is specific to the GEOscope name and to the act of resolving
messy metadata. Decorative elements that do not encode source, evidence, or
normalization are excluded.

## Information architecture and copy

### 1. Hero and accession scope

The hero contains the GEOscope wordmark, the thesis headline, a two-sentence
explanation, and a **Try a live search** button. A concise proof line describes
the indexed unit as GEO series metadata and avoids hard-coding a corpus count
unless the backend supplies a verified count.

The accession scope starts with a representative, explicitly labeled example
so the page remains meaningful before the backend is available. The example is
not presented as a live result. Submitting a query scrolls or expands into the
live comparison state.

### 2. Live keyword-versus-semantic comparison

The existing comparison concept is preserved but redesigned as a first-class
product proof. The same query runs against native GEO keyword search and the
GEOscope backend. Each result shows accession, title, bounded excerpt, and
retrieval/ontology evidence when supplied by the API.

Results absent from the native GEO result set receive a precise label such as
**Not returned by GEO keyword search**, not an unsupported claim that GEO could
never find the study. The interface distinguishes example data from live data.

### 3. Metadata normalization proof

Three compact transformations explain why semantic retrieval alone is not
enough:

- `human`, `H. sapiens`, `Homo sapiens` -> `NCBITaxon:9606`;
- `M`, `male`, contextual sex values -> `PATO:0000384` when supported;
- assay phrases such as `10x Chromium` and `scRNA-seq` -> the project's
  controlled assay label/category representation.

Copy explains that normalized identifiers make exact filtering and facet counts
possible. It does not imply that arbitrary values are automatically or
perfectly normalized.

### 4. How GEOscope works

Three connected capabilities replace generic feature cards:

1. **Retrieve meaning** with lexical and embedding search.
2. **Resolve vocabulary** into controlled concepts.
3. **Constrain precisely** with filters and facets exposed to people and MCP
   clients.

The connection among these capabilities is shown as one query flow, because
their order and relationship are meaningful.

### 5. Researcher example

A realistic query is translated into a visible structured request. For example:

> Find human single-cell RNA studies involving peripheral blood.

The page shows the free-text semantic portion alongside explicit organism,
assay, and tissue constraints. Only filters supported by the live backend are
interactive; unsupported future fields may be described in copy but never
rendered as working controls.

### 6. MCP and final call to action

The final section explains that the same bounded retrieval operations are
available to an LLM client through MCP. The primary action remains **Open the
live comparison**. A secondary repository or documentation link is shown only
when a real target URL is configured.

## Technical architecture

### Frontend

Create a Vite + React + TypeScript application under `frontend/`.

The frontend owns:

- marketing content and responsive layout;
- the accession-scope interaction;
- query, loading, result, empty, and error states;
- API response validation at the browser boundary;
- deep-linkable demo state using URL search parameters; and
- accessible navigation, controls, and result announcements.

Components are split by responsibility rather than by every visual fragment:

- `MarketingPage` composes the narrative sections;
- `AccessionScope` owns the signature hero interaction;
- `LiveComparison` owns query state and result presentation;
- `NormalizationProof` owns the raw-to-controlled examples;
- `CapabilityFlow` explains retrieval, normalization, and filtering; and
- `ResearcherExample` demonstrates natural-language-to-structured search.

The production Vite build is emitted to `frontend/dist/`. In development, Vite
proxies `/api` to FastAPI. In production, FastAPI serves the compiled assets and
uses a history fallback for frontend routes.

### FastAPI adapter

Add a small FastAPI application in the Python package. It is the browser-safe
adapter to the existing GEOscope backend; the React application never receives
MCP credentials, Elasticsearch credentials, or NCBI configuration.

The adapter calls the same `McpSearchService` domain service used by the FastMCP
tools, so the website and MCP surface share retrieval semantics and bounded
outputs without making the browser itself an MCP client. The native GEO
comparison continues through the existing bounded E-utilities client.

Initial endpoints:

- `GET /api/health` returns frontend-visible readiness without secrets.
- `GET /api/demo/search?q=<query>&mode=<mode>&limit=<n>` returns the GEO keyword
  result set, GEOscope result set, applied filters, scoped facets when
  available, and membership evidence used by comparison labels.

The endpoint accepts only strict, bounded inputs. Queries are trimmed and
length-limited, `mode` is an enum, and `limit` has a small maximum. API models
define the response contract explicitly rather than passing arbitrary backend
dictionaries to the browser.

The FastAPI application constructs the search service once in its lifespan and
closes it on shutdown. Blocking NCBI work is isolated from the async event loop.

### Failure and empty states

- If the backend is unavailable, the marketing narrative remains usable and
  the demo explains that live search is unavailable.
- If NCBI keyword search fails but GEOscope succeeds, GEOscope results remain
  visible and the comparison side states that native results could not be
  fetched.
- If a query returns no results, the interface offers concrete example queries.
- Invalid input produces field-specific guidance and preserves the typed query.
- The API never exposes exception traces, credentials, internal hosts, or raw
  backend payloads.

## Accessibility and responsive requirements

- Meet WCAG AA contrast for normal text and controls.
- Use semantic landmarks and a logical heading order.
- Support keyboard operation with visible focus treatment.
- Announce live-search status and result counts with an appropriate live region.
- Do not encode GEO versus GEOscope using color alone; pair color with labels
  and shapes.
- Preserve usable layout from 320 px wide phones through large desktop screens.
- Respect reduced-motion and increased-text preferences.
- Keep touch targets at least 44 by 44 CSS pixels.
- Avoid hover-only explanations and interaction.

## Verification

Frontend tests cover:

- hero and core proof copy;
- query submission and URL state;
- loading, partial failure, empty, and success rendering;
- GEO/GEOscope result distinction without color dependence;
- API schema rejection for malformed responses; and
- reduced-motion and responsive structural behavior where practical.

Python tests cover:

- bounded query parsing and validation;
- adapter delegation to the shared MCP search service;
- serialized response contracts;
- partial NCBI failure behavior;
- service startup/shutdown lifecycle; and
- static asset/history fallback without shadowing `/api` routes.

Final QA includes production builds, focused Python and frontend test suites,
keyboard navigation, and screenshots at representative phone, tablet, and
desktop widths. The visual pass checks overflow, line wrapping, focus states,
content hierarchy, and whether the accession scope remains the single memorable
element.

## Scope boundaries

This tranche creates the marketing page and browser-safe live demo adapter. It
does not redesign the authenticated MCP protocol, add user accounts, expose
private MCP tokens to the browser, build a general chat interface, or promise
normalization fields not supported by the current backend. Deployment and
public-domain configuration are separate decisions unless requested after the
local production build is complete.
