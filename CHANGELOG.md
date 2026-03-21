# Changelog

## v0.2.0 - 2026-03-21
- Rebranded the project fully to ScholarFetch.
- Added FastMCP server mode with `stdio`, `sse`, and `streamable-http` transports.
- Hardened MCP tool schemas for Langflow/Gemini compatibility (string CSV params for engines/filters).
- Enforced server-side credential handling for MCP tools; no API keys in tool arguments.
- Added author investigation workflow improvements and better deduped paper retrieval UX.
- Added project logo SVG and refreshed documentation.
- Added packaging entrypoints:
  - `scholarfetch`
  - `scholarfetch-mcp`
  - `scholarfetch-fastmcp`
- Added local Hugging Face Space scaffold (git-ignored) under `huggingface/`.

## v0.1.0 - 2026-03-21
- Initial multi-engine ScholarFetch CLI and MCP baseline.
