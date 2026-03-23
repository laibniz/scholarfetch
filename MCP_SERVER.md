# ScholarFetch MCP Server

ScholarFetch MCP exposes a research-traversal workflow for agents.

The server is useful when an agent needs to:
- search papers
- disambiguate authors
- expand author paper sets
- inspect abstracts and full text
- expand references
- keep an in-memory reading list during the same MCP session
- export a curated research corpus for downstream synthesis

ScholarFetch therefore acts like a research workspace, not just a search endpoint.

## Server Modes
- Classic MCP server: `scholarfetch_mcp.py` (stdio JSON-RPC)
- FastMCP server: `scholarfetch_fastmcp.py`
  - `stdio`
  - `sse`
  - `streamable-http`

## Run
```bash
python3 scholarfetch_mcp.py
python3 scholarfetch_fastmcp.py --transport stdio
python3 scholarfetch_fastmcp.py --transport sse --host 127.0.0.1 --port 8000
python3 scholarfetch_fastmcp.py --transport streamable-http --host 127.0.0.1 --port 8000 --http-path /mcp
```

## Local Validation
```bash
python3 scholarfetch_mcp.py --self-test
python3 scholarfetch_fastmcp.py --self-test
```

## Credential Model
Credentials are loaded server-side only.

Supported variables:
- `ELSEVIER_API_KEY`
- `ELSEVIER_INSTTOKEN` (optional)
- `SPRINGER_META_API_KEY`
- `SPRINGER_OPENACCESS_API_KEY`
- `SCHOLARFETCH_ENV_FILE` (optional env-file path)

Do not pass API keys as MCP tool arguments.

## Core Research Tools

### `scholarfetch_search`
Purpose:
- start a research traversal from keywords, DOI, or person name

Returns:
- deduplicated paper records across engines

Typical next step:
- `scholarfetch_doi_lookup`
- `scholarfetch_abstract`
- `scholarfetch_article_text`
- `scholarfetch_saved_add`

### `scholarfetch_doi_lookup`
Purpose:
- enrich one DOI before reading or saving it

Returns:
- cross-engine metadata
- links
- full-text availability hints

Typical next step:
- `scholarfetch_article_text`
- `scholarfetch_references`
- `scholarfetch_saved_add`

### `scholarfetch_author_candidates`
Purpose:
- disambiguate a human author name into stable candidate identities

Returns:
- ranked candidates with metadata for selection

Typical next step:
- `scholarfetch_author_papers`

### `scholarfetch_author_papers`
Purpose:
- expand a chosen author into a deduplicated paper list

Supports:
- `author_id`
- or `author_name + candidate_index`
- optional filters

Typical next step:
- `scholarfetch_abstract`
- `scholarfetch_article_text`
- `scholarfetch_references`
- `scholarfetch_saved_add`

### `scholarfetch_abstract`
Purpose:
- retrieve the best available abstract for one target paper

Input styles:
- `doi`
- or `author_name + candidate_index + paper_index`

Typical next step:
- save the paper if relevant
- fetch full text if still promising

### `scholarfetch_article_text`
Purpose:
- retrieve full text when machine-readable content is available

Input styles:
- `doi`
- or `author_name + candidate_index + paper_index`

Fallback model:
- Elsevier first
- then open-access fallbacks such as Springer OA, Europe PMC, arXiv, and generic PDF extraction when recoverable

Typical next step:
- `scholarfetch_saved_add`
- `scholarfetch_references`

### `scholarfetch_references`
Purpose:
- expand a paper into its references

Input styles:
- `doi`
- or `author_name + candidate_index + paper_index`

Returns:
- numbered references
- parsed DOI when detectable

Typical next step:
- loop over returned DOIs/text with `scholarfetch_doi_lookup`, `scholarfetch_abstract`, or `scholarfetch_article_text`
- save promising cited papers

## Reading List Tools
These tools are stateful within the MCP server process.

Use the same `collection` name across calls to maintain one coherent reading list for the current agent session.

### `scholarfetch_saved_add`
Purpose:
- add one paper into the current reading list

Best input:
- `paper_json` copied from another ScholarFetch result object

Alternative inputs:
- `doi`
- `query + result_index`
- `author_name + candidate_index + paper_index`

### `scholarfetch_saved_list`
Purpose:
- inspect the current reading list

### `scholarfetch_saved_remove`
Purpose:
- prune papers that are no longer relevant

### `scholarfetch_saved_clear`
Purpose:
- reset the current research branch

### `scholarfetch_saved_export`
Purpose:
- export the reading list into a reusable artifact

Formats:
- `citations`
- `abstracts`
- `bib`
- `fulltext`

Special option:
- `include_references=true` with `format=fulltext`

This is especially useful when another downstream agent needs a compact research corpus rather than live API traversal.

## Recommended Agent Loop
1. Run `scholarfetch_search` on a topic.
2. Save promising papers with `scholarfetch_saved_add`.
3. Use `scholarfetch_author_candidates` and `scholarfetch_author_papers` to expand author branches.
4. Use `scholarfetch_references` to expand citation branches.
5. Read abstracts with `scholarfetch_abstract`.
6. Read full text with `scholarfetch_article_text` when warranted.
7. Keep refining the reading list.
8. Export with `scholarfetch_saved_export` once the set is coherent.

## Example Client Config
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
    "scholarfetch-fastmcp": {
      "command": "python3",
      "args": ["/absolute/path/to/scholarfetch_fastmcp.py", "--transport", "stdio"]
    }
  }
}
```

FastMCP streamable HTTP:

```json
{
  "mcpServers": {
    "scholarfetch-http": {
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
- Engine subsets can be forced per call using `engines`.
- Provider entitlements still apply.
- Reading-list memory is process-local and in-memory.
- If you need stable persistence across restarts, export the collection and rehydrate it externally.
