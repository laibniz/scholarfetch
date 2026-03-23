# Changelog

## v0.2.1 - 2026-03-23
- Added a stateful research scratchpad for MCP agents through named in-memory saved-paper collections.
- Added MCP tools for saved-list curation and export:
  - `scholarfetch_saved_add`
  - `scholarfetch_saved_list`
  - `scholarfetch_saved_remove`
  - `scholarfetch_saved_clear`
  - `scholarfetch_saved_export`
- Added export modes for curated research packets:
  - `citations`
  - `abstracts`
  - `bib`
  - `fulltext`
- Improved MCP tool descriptions so parameter options, engine subsets, and traversal workflows are explicit for agents.
- Added agent-facing documentation:
  - `AGENTS.md`
  - `SKILL.md`
  - `SKILLS.md`
- Expanded CLI research traversal:
  - tree-based picker navigation
  - references as paper-like nodes
  - author expansion with single-author or all-author branches
  - saved-paper workflow in interactive sessions
- Improved full-text and reference retrieval workflows across Elsevier and open-access sources.
- Added `server.json` and republished the public remote MCP metadata for the hosted Streamable HTTP endpoint.

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
