# GEOscope MCP Client Install Tabs Design

## Purpose

Turn the existing MCP panel in the GEOscope marketing hero into a compact,
copy-ready installation guide. The panel will support exactly three programming
agents, in this order: Claude Code, Codex, and Gemini CLI. It will not depend on
MCP store listings or imply that GEOscope is available in a client marketplace.

The panel continues to point every client at the canonical production endpoint:

```text
https://geoscope.kevinformatics.com/mcp
```

## Interaction and content

Replace the current read-only URL field and the four-item “Works with” list with
an accessible three-tab client selector. Claude Code is selected by default.
Selecting a client updates one shared instruction panel beneath the tabs rather
than rendering three long cards at once.

The tab order and commands are:

1. **Claude Code**

   ```bash
   claude mcp add --scope user --transport http geoscope https://geoscope.kevinformatics.com/mcp
   ```

2. **Codex**

   ```bash
   codex mcp add geoscope --url https://geoscope.kevinformatics.com/mcp
   ```

3. **Gemini CLI**

   ```bash
   gemini mcp add --scope user --transport http geoscope https://geoscope.kevinformatics.com/mcp
   ```

Each client view contains a concise instruction to run the command in a
terminal, the complete command in a selectable code block, and a copy button.
The copy button copies the entire active command. A successful copy announces
that the command was copied. If clipboard access is unavailable or fails, the
command remains selectable and a polite live region instructs the visitor to
copy it manually. Switching tabs clears stale copy status.

No authentication, installation troubleshooting, package installation, store
listing, or client download instructions are added. These commands configure
an already-installed client to use GEOscope's public Streamable HTTP MCP
endpoint.

## Product logos

Each tab pairs its text label with the product's real mark:

- **Claude Code:** the orange Claude starburst app icon published in Anthropic's
  official Claude application;
- **Codex:** the blue-purple terminal-in-Blossom Codex application icon
  published in OpenAI's official ChatGPT/Codex application; and
- **Gemini CLI:** the color Gemini CLI icon SVG published in Google's official
  [Gemini CLI brand kit](https://geminicli.com/brand-kit/).

Store the chosen assets in the frontend source tree so the page has no runtime
dependency on external image hosts. Preserve their proportions and brand
colors; do not redraw, recolor, animate, or combine the marks into the GEOscope
identity. Render the product name as real text beside each image, with the image
treated as decorative so assistive technology does not announce the name
twice. Keep visual sizing consistent even if the source asset view boxes differ.

Use the first-party SVG for Gemini CLI. Use the first-party raster application
icons for Claude Code and Codex at an appropriate intrinsic resolution; do not
upscale either raster past its useful size.

## Component design

`McpInstall` owns a small client configuration array containing the stable key,
display name, local logo path, exact command, and one-sentence instruction for
each client. Component state stores the active client key and copy status.

The tab list and shared tab panel remain inside the existing hero-side MCP card.
The panel retains the “Bring GEOscope to your agent.” headline and short MCP
description. The shared panel uses a semantic code element for the command and
one explicit “Copy command” button rather than an icon-only action whose target
could be ambiguous.

No changes are made to the marketing API, MCP server, endpoint, search service,
or Elasticsearch behavior.

## Accessibility and responsive behavior

- Use the ARIA tabs pattern with one tab stop, `aria-selected`, matching
  `aria-controls`/`aria-labelledby` relationships, and left/right arrow-key
  navigation in addition to normal pointer activation.
- Keep visible focus styles on every tab and the copy button.
- Logos are decorative because adjacent text provides the accessible name.
- The copy result uses `role="status"` and `aria-live="polite"`.
- Long commands remain readable and selectable without causing page-level
  horizontal overflow. The command box may scroll internally on narrow screens.
- The three tabs retain the requested order on every viewport and remain large
  enough for comfortable pointer use.

## Testing and verification

Frontend tests will establish that:

- exactly Claude Code, Codex, and Gemini CLI are offered, in that order;
- each client label is paired with its local logo asset;
- Claude Code is selected by default and its command is initially visible;
- clicking each other tab reveals the correct command and selected state;
- keyboard arrow navigation changes the selected client and focus correctly;
- the copy button writes the complete active command for all three clients;
- switching clients clears an earlier copy confirmation;
- clipboard failure leaves the command available with manual-copy guidance; and
- the former ChatGPT, Claude, Cursor, and GitHub Copilot compatibility list is
  absent.

Run the frontend Vitest suite and TypeScript/Vite production build. Visually
inspect desktop and mobile layouts for correct logo proportions, tab focus and
selection states, command overflow, copy feedback, and hero height.

## Scope boundaries

- Do not add marketplace or store links.
- Do not add more MCP clients.
- Do not add external runtime image requests or new frontend dependencies.
- Do not modify search relevance, MCP tools, authentication, or backend
  behavior.
