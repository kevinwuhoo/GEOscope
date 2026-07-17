# GEOscope Favicon Design

## Decision

Adopt the **monochrome Ribbon Notch** as GEOscope’s favicon/logo mark. It is a
custom, asymmetric organic tile containing one broad, continuous internal
opening. The mark is abstract: it suggests focus or a folded field without
depicting a lens, globe, cell, DNA helix, or search icon.

## Rationale

The marketing site already communicates GEOscope’s metadata-search story in
copy and interaction. The favicon therefore should be a compact, memorable
signature rather than an illustration of the product pipeline. Ribbon Notch was
selected because its silhouette stays recognizably distinct at browser-tab
sizes while being quieter and less mechanical than the earlier scope, knot, and
rift explorations.

## Mark construction

- Use a 64×64 viewBox, with a transparent background in the canonical SVG.
- Draw one filled asymmetric outer silhouette and one rounded, open internal
  stroke/cutout. Do not include the lime accent from the exploration board.
- The canonical color is Carbon (`#102321`). For dark backgrounds, use Specimen
  White (`#FCFFFD`) as a single-color inverse. No multi-color favicon variant
  is needed.
- Preserve generous clear space around the silhouette; do not enclose it in a
  square, circle, or rounded tile in the SVG itself.

## Deliverables

1. A canonical SVG mark in the frontend public assets.
2. A 32×32 browser favicon derived from that SVG, referenced from the Vite HTML
   entry point.
3. A small inline mark next to the existing GEOscope wordmark, scaled without
   changing the current header layout or wordmark typography.

## Acceptance criteria

- The icon is legible at 16×16, 32×32, and 48×48 pixels.
- The visible mark contains only one foreground color in either light or dark
  context.
- The favicon has a transparent canvas and remains identifiable against the
  site’s Laboratory Paper background.
- The React build and existing frontend tests continue to pass.

## Scope exclusions

- No rebrand of the existing type-based GEOscope wordmark.
- No decorative gradients, shadows, animation, or raster image generation.
- No changes to search behavior or the MCP/Elasticsearch layer.
