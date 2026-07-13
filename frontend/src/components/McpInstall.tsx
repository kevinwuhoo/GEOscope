import { useState } from "react";


export const MCP_URL = "https://geoscope.kevinformatics.com/mcp";

const compatibleClients = [
  { name: "ChatGPT", mark: "✺", modifier: "chatgpt" },
  { name: "Claude", mark: "✦", modifier: "claude" },
  { name: "Cursor", mark: "↗", modifier: "cursor" },
  { name: "GitHub Copilot", mark: "∞", modifier: "copilot" },
];


export function McpInstall() {
  const [status, setStatus] = useState("");

  async function copyUrl() {
    try {
      if (!navigator.clipboard) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(MCP_URL);
      setStatus("Copied MCP URL.");
    } catch {
      setStatus("Select the URL and copy it manually.");
    }
  }

  return (
    <aside className="mcp-install" id="mcp" aria-labelledby="mcp-title">
      <div className="mcp-install__copy">
        <h3 id="mcp-title">Bring GEOscope to your agent.</h3>
        <p>
          Copy the server URL, then add it as a custom MCP server in your AI app.
        </p>
      </div>
      <div className="mcp-install__action">
        <label htmlFor="mcp-url">MCP server URL</label>
        <div className="mcp-install__copy-row">
          <input
            id="mcp-url"
            value={MCP_URL}
            readOnly
            aria-describedby="mcp-copy-status"
            onFocus={(event) => event.currentTarget.select()}
          />
          <button
            type="button"
            onClick={copyUrl}
            aria-label="Copy MCP URL"
            title="Copy MCP URL"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <rect x="8" y="7" width="10" height="12" rx="1" />
              <path d="M6 16H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v1" />
            </svg>
          </button>
        </div>
        <p
          className="mcp-install__status"
          id="mcp-copy-status"
          role="status"
          aria-live="polite"
        >
          {status}
        </p>
      </div>
      <div className="mcp-install__clients">
        <p>Works with</p>
        <ul aria-label="Compatible MCP clients">
          {compatibleClients.map((client) => (
            <li key={client.name}>
              <span className={`agent-client__mark agent-client__mark--${client.modifier}`} aria-hidden="true">
                {client.mark}
              </span>
              <span>{client.name}</span>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}
