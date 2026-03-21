# ScholarFetch MCP Server

ScholarFetch ships with two MCP server modes:
- Classic MCP server: `scholarfetch_mcp.py` (stdio JSON-RPC framing)
- FastMCP server: `scholarfetch_fastmcp.py` (`stdio`, `sse`, `streamable-http`)

## Run
```bash
# Classic stdio MCP
python3 scholarfetch_mcp.py

# FastMCP stdio
python3 scholarfetch_fastmcp.py --transport stdio

# FastMCP SSE
python3 scholarfetch_fastmcp.py --transport sse --host 127.0.0.1 --port 8000

# FastMCP Streamable HTTP
python3 scholarfetch_fastmcp.py --transport streamable-http --host 127.0.0.1 --port 8000 --http-path /mcp
```

## Quick Local Validation
```bash
python3 scholarfetch_mcp.py --self-test
python3 scholarfetch_fastmcp.py --self-test
```

## Tools Exposed

### `scholarfetch_search`
Unified scholarly search across enabled engines.

Inputs:
- `query` (required)
- `limit` (optional)
- `engines` (optional comma-separated subset override, e.g. `openalex,crossref,springer`)

### `scholarfetch_doi_lookup`
Cross-engine DOI enrichment.

Inputs:
- `doi` (required)
- `engines` (optional comma-separated subset)

### `scholarfetch_author_candidates`
Ranked author identity candidates (OpenAlex-based).

Inputs:
- `name` (required)
- `limit` (optional)
- `engines` (optional comma-separated subset; must include `openalex`)

### `scholarfetch_author_papers`
Deduplicated papers for a selected author.

Inputs:
- `author_id` OR `author_name`
- `candidate_index` (optional, default `1`)
- `limit` (optional)
- `filters` (optional comma-separated filters)
- `engines` (optional comma-separated subset; must include `openalex`)

Supported filters:
- `year>=YYYY`, `year<=YYYY`, `year=YYYY`
- `has:abstract`, `has:doi`, `has:pdf`
- `venue:<text>`, `title:<text>`, `doi:<text>`

### `scholarfetch_abstract`
Best abstract by DOI OR by author flow.

Inputs:
- path A: `doi`
- path B: `author_name`, `candidate_index`, `paper_index`
- `engines` (optional comma-separated subset)

## Credentials and Security
API credentials are loaded server-side from environment only:
- `ELSEVIER_API_KEY`
- `ELSEVIER_INSTTOKEN` (optional)
- `SPRINGER_META_API_KEY`
- `SPRINGER_OPENACCESS_API_KEY`
- `SCHOLARFETCH_ENV_FILE` (optional path override)

Do not pass API keys in MCP tool arguments.
Elsevier credentials are always sent upstream in HTTP headers by the server:
- `X-ELS-APIKey`
- `X-ELS-Insttoken` (when configured)

## Example MCP Client Config
Use one of these in your MCP-compatible host/client configuration.

Classic stdio:

```json
{
  "mcpServers": {
    "scholarfetch": {
      "command": "python3",
      "args": ["/absolute/path/to/scholarfetch_mcp.py"]
    }
  }
}
```

FastMCP stdio:

```json
{
  "mcpServers": {
    "scholarfetch-fastmcp-stdio": {
      "command": "python3",
      "args": ["/absolute/path/to/scholarfetch_fastmcp.py", "--transport", "stdio"]
    }
  }
}
```

FastMCP streamable-http (via proxy):

```json
{
  "mcpServers": {
    "scholarfetch-fastmcp-http": {
      "command": "wsl",
      "args": [
        "uvx",
        "mcp-proxy",
        "--transport",
        "streamablehttp",
        "http://127.0.0.1:8000/mcp"
      ]
    }
  }
}
```

## Notes
- The server is stateless per call.
- Engine subset can be forced per tool call using `engines`.
- Provider entitlement/rate-limit restrictions still apply.
