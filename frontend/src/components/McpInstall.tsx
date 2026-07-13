import { useRef, useState, type KeyboardEvent } from "react";

import claudeCodeLogo from "../assets/mcp-clients/claude-code.png";
import codexLogo from "../assets/mcp-clients/codex.png";
import geminiCliLogo from "../assets/mcp-clients/gemini-cli.svg";


export const MCP_URL = "https://geoscope.kevinformatics.com/mcp";

export const MCP_CLIENTS = [
  {
    key: "claude-code",
    name: "Claude Code",
    logo: claudeCodeLogo,
    instruction:
      "Run this in your terminal to add GEOscope for every Claude Code project.",
    command: `claude mcp add --scope user --transport http geoscope ${MCP_URL}`,
  },
  {
    key: "codex",
    name: "Codex",
    logo: codexLogo,
    instruction:
      "Run this in your terminal to add GEOscope to your Codex configuration.",
    command: `codex mcp add geoscope --url ${MCP_URL}`,
  },
  {
    key: "gemini-cli",
    name: "Gemini CLI",
    logo: geminiCliLogo,
    instruction:
      "Run this in your terminal to add GEOscope for every Gemini CLI project.",
    command: `gemini mcp add --scope user --transport http geoscope ${MCP_URL}`,
  },
] as const;


export function McpInstall() {
  const [activeIndex, setActiveIndex] = useState(0);
  const [status, setStatus] = useState("");
  const tabsRef = useRef<Array<HTMLButtonElement | null>>([]);

  function selectClient(index: number, moveFocus = false) {
    setActiveIndex(index);
    setStatus("");
    if (moveFocus) tabsRef.current[index]?.focus();
  }

  function handleTabKeyDown(
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) {
    let nextIndex: number | undefined;

    switch (event.key) {
      case "ArrowRight":
        nextIndex = (index + 1) % MCP_CLIENTS.length;
        break;
      case "ArrowLeft":
        nextIndex = (index - 1 + MCP_CLIENTS.length) % MCP_CLIENTS.length;
        break;
      case "Home":
        nextIndex = 0;
        break;
      case "End":
        nextIndex = MCP_CLIENTS.length - 1;
        break;
      default:
        return;
    }

    event.preventDefault();
    selectClient(nextIndex, true);
  }

  async function copyCommand(client: (typeof MCP_CLIENTS)[number]) {
    try {
      if (!navigator.clipboard) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(client.command);
      setStatus(`Copied ${client.name} command.`);
    } catch {
      setStatus("Select the command and copy it manually.");
    }
  }

  return (
    <aside className="mcp-install" id="mcp" aria-labelledby="mcp-title">
      <div className="mcp-install__copy">
        <h3 id="mcp-title">Bring GEOscope to your agent.</h3>
        <p>Choose your coding agent, then copy its GEOscope MCP setup command.</p>
      </div>

      <div
        className="mcp-install__tabs"
        role="tablist"
        aria-label="MCP client installation"
      >
        {MCP_CLIENTS.map((client, index) => {
          const isActive = index === activeIndex;

          return (
            <button
              className="mcp-install__tab"
              id={`mcp-tab-${client.key}`}
              key={client.key}
              type="button"
              role="tab"
              aria-selected={isActive}
              aria-controls={`mcp-panel-${client.key}`}
              tabIndex={isActive ? 0 : -1}
              ref={(node) => {
                tabsRef.current[index] = node;
              }}
              onClick={() => selectClient(index)}
              onKeyDown={(event) => handleTabKeyDown(event, index)}
            >
              <img
                className="mcp-install__logo"
                src={client.logo}
                alt=""
                aria-hidden="true"
              />
              <span>{client.name}</span>
            </button>
          );
        })}
      </div>

      {MCP_CLIENTS.map((client, index) => {
        const isActive = index === activeIndex;

        return (
          <div
            className="mcp-install__panel"
            id={`mcp-panel-${client.key}`}
            key={client.key}
            role="tabpanel"
            aria-labelledby={`mcp-tab-${client.key}`}
            hidden={!isActive}
          >
            <p className="mcp-install__instruction">{client.instruction}</p>
            <pre className="mcp-install__command">
              <code>{client.command}</code>
            </pre>
            <button
              className="mcp-install__copy-command"
              type="button"
              aria-describedby="mcp-copy-status"
              onClick={() => copyCommand(client)}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <rect x="8" y="7" width="10" height="12" rx="1" />
                <path d="M6 16H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v1" />
              </svg>
              <span>Copy command</span>
            </button>
          </div>
        );
      })}

      <p
        className="mcp-install__status"
        id="mcp-copy-status"
        role="status"
        aria-live="polite"
      >
        {status}
      </p>
    </aside>
  );
}
