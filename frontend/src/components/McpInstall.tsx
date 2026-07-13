import { useState } from "react";


export const MCP_URL = "https://geoscope.kevinformatics.com/mcp";


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
          Copy this URL, then add it as a custom MCP server in ChatGPT, Claude,
          or another MCP-compatible client.
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
          <button type="button" onClick={copyUrl}>
            {status.startsWith("Copied") ? "Copied" : "Copy MCP URL"}
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
    </aside>
  );
}
