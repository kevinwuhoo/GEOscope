# GEOscope MCP Client Install Tabs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hero's generic MCP URL card with accessible Claude Code, Codex, and Gemini CLI install tabs using official local logo assets and copyable commands.

**Architecture:** Keep the feature inside `McpInstall`: a typed client-data array drives a roving-tab selector and three linked tab panels, while one shared status region reports clipboard results. Import first-party logo files through Vite, and limit styling to the existing hero card.

**Tech Stack:** React 18, TypeScript, Vite, CSS, Vitest, Testing Library, user-event

## Global Constraints

- Offer exactly Claude Code, Codex, and Gemini CLI, in that order.
- Use `https://geoscope.kevinformatics.com/mcp` in every command.
- Use first-party product logo assets stored under `frontend/src/assets/mcp-clients/`.
- Do not add marketplace links, external runtime images, frontend dependencies, backend changes, or search behavior changes.
- Preserve all unrelated working-tree changes.

---

### Task 1: Add the tested MCP client selector

**Files:**
- Create: `frontend/src/assets/mcp-clients/claude-code.png`
- Create: `frontend/src/assets/mcp-clients/codex.png`
- Create: `frontend/src/assets/mcp-clients/gemini-cli.svg`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/components/McpInstall.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: the existing `MCP_URL` export and `McpInstall` placement in `App`.
- Produces: `MCP_CLIENTS`, an ordered readonly client array; accessible tabs/panels; and `copyCommand(command, clientName)` clipboard behavior.

- [ ] **Step 1: Import the official logo assets**

Copy the installed first-party Claude and Codex application icons without altering their pixels:

```bash
mkdir -p frontend/src/assets/mcp-clients
cp /Applications/Claude.app/Contents/Resources/ion-dist/images/claude_app_icon.png frontend/src/assets/mcp-clients/claude-code.png
cp /Applications/ChatGPT.app/Contents/Resources/icon-codex-dark-color.png frontend/src/assets/mcp-clients/codex.png
```

Download the official full-color Gemini CLI icon SVG from the Gemini CLI brand kit, inspect it as text, and add that exact SVG as `frontend/src/assets/mcp-clients/gemini-cli.svg`.

- [ ] **Step 2: Write failing behavior tests**

Replace the old generic compatibility/copy assertions in `frontend/src/App.test.tsx` with focused tests equivalent to:

```tsx
test("offers the requested MCP clients with official local logos", () => {
  render(<App />);
  const tabs = screen.getAllByRole("tab");
  expect(tabs.map((tab) => tab.textContent?.trim())).toEqual([
    "Claude Code",
    "Codex",
    "Gemini CLI",
  ]);
  expect(tabs[0]).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText(CLAUDE_COMMAND)).toBeVisible();
  expect(tabs.map((tab) => tab.querySelector("img")?.getAttribute("src"))).toEqual([
    expect.stringContaining("claude-code"),
    expect.stringContaining("codex"),
    expect.stringContaining("gemini-cli"),
  ]);
  expect(screen.queryByRole("list", { name: /compatible mcp clients/i })).not.toBeInTheDocument();
});

test("switches MCP instructions with pointer and keyboard input", async () => {
  const user = userEvent.setup();
  render(<App />);
  const tabs = screen.getAllByRole("tab");

  await user.click(tabs[1]);
  expect(tabs[1]).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText(CODEX_COMMAND)).toBeVisible();

  tabs[1].focus();
  await user.keyboard("{ArrowRight}");
  expect(tabs[2]).toHaveFocus();
  expect(tabs[2]).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText(GEMINI_COMMAND)).toBeVisible();
});

test("copies each selected MCP command and clears stale feedback", async () => {
  const user = userEvent.setup();
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  render(<App />);

  await user.click(screen.getByRole("button", { name: /copy command/i }));
  expect(writeText).toHaveBeenLastCalledWith(CLAUDE_COMMAND);
  expect(screen.getByRole("status")).toHaveTextContent(/copied claude code command/i);

  await user.click(screen.getByRole("tab", { name: /codex/i }));
  expect(screen.getByRole("status")).toBeEmptyDOMElement();
  await user.click(screen.getByRole("button", { name: /copy command/i }));
  expect(writeText).toHaveBeenLastCalledWith(CODEX_COMMAND);
});
```

Keep the existing clipboard-failure test, but target `Copy command` and expect “Select the command and copy it manually.”

- [ ] **Step 3: Run the tests and verify RED**

Run:

```bash
pnpm test -- --run src/App.test.tsx
```

Working directory: `frontend`

Expected: FAIL because no tabs, official logo images, or client-specific commands exist yet.

- [ ] **Step 4: Implement the minimal component behavior**

In `McpInstall.tsx`, import the three local assets and define the ordered data:

```tsx
export const MCP_CLIENTS = [
  {
    key: "claude-code",
    name: "Claude Code",
    logo: claudeCodeLogo,
    instruction: "Run this in your terminal to add GEOscope for every Claude Code project.",
    command: `claude mcp add --scope user --transport http geoscope ${MCP_URL}`,
  },
  {
    key: "codex",
    name: "Codex",
    logo: codexLogo,
    instruction: "Run this in your terminal to add GEOscope to your Codex configuration.",
    command: `codex mcp add geoscope --url ${MCP_URL}`,
  },
  {
    key: "gemini-cli",
    name: "Gemini CLI",
    logo: geminiCliLogo,
    instruction: "Run this in your terminal to add GEOscope for every Gemini CLI project.",
    command: `gemini mcp add --scope user --transport http geoscope ${MCP_URL}`,
  },
] as const;
```

Use `useRef` for the three tab buttons. On click, set the active key and clear status. On `ArrowLeft`, `ArrowRight`, `Home`, or `End`, select and focus the correct tab with wraparound. Render all three linked `role="tabpanel"` elements and hide the inactive panels. Each active panel contains its instruction, a scrollable `<pre><code>`, and a visible `Copy command` button. Copy the selected command and announce either `Copied <client> command.` or `Select the command and copy it manually.`

- [ ] **Step 5: Style the tabs and command panel**

Replace the obsolete URL-field, compatibility-list, and fake-mark rules in `styles.css` with:

```css
.mcp-install__tabs { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin-top: 28px; }
.mcp-install__tab { display: grid; place-items: center; gap: 7px; min-width: 0; min-height: 72px; padding: 10px 6px; border: 1px solid var(--carbon); border-radius: 0; background: rgb(252 255 253 / .55); color: var(--carbon); font: 650 9px/1.15 var(--mono); cursor: pointer; }
.mcp-install__tab + .mcp-install__tab { margin-left: -1px; }
.mcp-install__tab[aria-selected="true"] { position: relative; z-index: 1; background: var(--carbon); color: var(--white); }
.mcp-install__logo { width: 28px; height: 28px; object-fit: contain; }
.mcp-install__panel { padding-top: 16px; }
.mcp-install__instruction { min-height: 38px; margin: 0 0 10px; font: 500 11px/1.45 var(--mono); }
.mcp-install__command { margin: 0; padding: 14px; overflow-x: auto; border: 2px solid var(--carbon); background: var(--white); color: var(--cobalt); font: 600 10px/1.5 var(--mono); white-space: pre; }
.mcp-install__copy-command { display: inline-flex; align-items: center; justify-content: center; gap: 9px; width: 100%; min-height: 48px; margin-top: -2px; border: 2px solid var(--carbon); border-radius: 0; background: var(--carbon); color: var(--white); font: 650 10px/1 var(--mono); text-transform: uppercase; cursor: pointer; }
.mcp-install__copy-command:hover { background: var(--cobalt); }
.mcp-install__status { min-height: 18px; margin: 10px 0 0; font: 600 10px/1.4 var(--mono); }
```

Retain existing global focus-visible treatment and remove the obsolete 390px URL-field overrides.

- [ ] **Step 6: Run the focused tests and verify GREEN**

Run:

```bash
pnpm test -- --run src/App.test.tsx
```

Working directory: `frontend`

Expected: all `App.test.tsx` tests pass with no warnings.

- [ ] **Step 7: Run full frontend verification**

Run:

```bash
pnpm test -- --run
pnpm build
```

Working directory: `frontend`

Expected: the complete Vitest suite passes and the Vite production build exits successfully.

- [ ] **Step 8: Inspect the final diff**

Run:

```bash
git diff --check
git status --short
git diff -- frontend/src/App.test.tsx frontend/src/components/McpInstall.tsx frontend/src/styles.css
```

Expected: no whitespace errors; only the intended MCP files, new logo assets, plan, and pre-existing unrelated user changes appear.
