# ScholarFetch

![ScholarFetch Logo](./assets/scholarfetch-logo.svg)

ScholarFetch is a terminal-first, multi-engine scholarly fetcher.

It aggregates metadata, abstracts, and reading links across multiple scholarly APIs with a single CLI workflow.

## What It Does
- Unified search across multiple engines in parallel
- Author investigation workflow with ranked candidates and deduplicated papers
- DOI lookup with cross-engine enrichment
- Abstract retrieval with fallback/ranking across sources
- Article/read-link discovery (including PDF links when available)
- Engine selection/configuration from CLI

## Engines
- Elsevier (Scopus/Abstract/Article APIs)
- OpenAlex
- Crossref
- arXiv
- Europe PMC
- Springer Nature (Metadata API + Open Access API)
- Semantic Scholar (DOI enrichment path)

## Installation
```bash
cd /home/andrea/VibeCodes/elsevier
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
scholarfetch
```

No external Python packages are required.

Installed console scripts:
- `scholarfetch`
- `scholarfetch-mcp`
- `scholarfetch-fastmcp`

Alternative (without installation):
```bash
python3 scholarfetch.py
```

If your environment is offline/restricted and `pip install -e .` fails, use:
```bash
python setup.py develop
scholarfetch
```

## Credentials
The CLI auto-loads credentials from:
1. `SCHOLARFETCH_ENV_FILE` (default: `.scholarfetch.env`)

Example `.scholarfetch.env`:
```bash
ELSEVIER_API_KEY=...
ELSEVIER_INSTTOKEN=...
SPRINGER_META_API_KEY=...
SPRINGER_OPENACCESS_API_KEY=...
```

Notes:
- `ELSEVIER_INSTTOKEN` is optional; the client retries without it when needed.
- Some endpoints/views are entitlement-restricted by providers.

## Quick Start Workflow
```text
/author andrea de mauro
/papers 1
/abstract 1
```

## Core Commands
- `/search <keywords|doi|person name>`
- `/author <name>`
- `/papers <author name|index> [filters]`
- `/open <index>`
- `/doi <doi>`
- `/abstract <doi|index>`
- `/article <doi>`
- `/engines`
- `/config`

## Paper Filters
Use with `/papers`:
- `year>=YYYY`, `year<=YYYY`, `year=YYYY`
- `has:abstract`, `has:doi`, `has:pdf`
- `venue:<text>`, `title:<text>`, `doi:<text>`

Examples:
```text
/papers 1 year>=2018 has:abstract
/papers andrea de mauro venue:marketing
```

## Engine Configuration
- `/config` (show current state + help)
- `/config only springer`
- `/config only openalex,elsevier`
- `/config add arxiv`
- `/config remove crossref`
- `/config reset`
- `/config save` (persists to `.scholarfetch_settings.json`)

Springer note:
- In `only springer` mode, non-DOI keyword/person search may return no results due to Springer premium restrictions on generic search.
- DOI-driven commands work (`/doi <doi>`, `/search <doi>`, `/abstract <doi|index>`).


## MCP Server
ScholarFetch includes an MCP server in this same repository.

- Classic MCP (stdio): `python3 scholarfetch_mcp.py`
- FastMCP:
  - stdio: `python3 scholarfetch_fastmcp.py --transport stdio`
  - SSE: `python3 scholarfetch_fastmcp.py --transport sse --host 127.0.0.1 --port 8000`
  - Streamable HTTP: `python3 scholarfetch_fastmcp.py --transport streamable-http --host 127.0.0.1 --port 8000 --http-path /mcp`
- Local tests:
  - `python3 scholarfetch_mcp.py --self-test`
  - `python3 scholarfetch_fastmcp.py --self-test`
- Docs: [MCP_SERVER.md](./MCP_SERVER.md)

MCP tools do not accept API keys in arguments. Credentials are loaded server-side from environment (`.scholarfetch.env` / process env) and sent upstream as provider headers.

## Repository Structure
- `scholarfetch.py`: main CLI entrypoint
- `scholarfetch_cli.py`: core CLI + engine integrations
- `scholarfetch_mcp.py`: MCP server entrypoint
- `scholarfetch_fastmcp.py`: FastMCP server entrypoint (`stdio`, `sse`, `streamable-http`)
- `MCP_SERVER.md`: MCP tool docs and integration notes
- `README.md`: project docs
- `CONTRIBUTING.md`: contributor guide
- `CODE_OF_CONDUCT.md`: community standards
- `SECURITY.md`: vulnerability reporting process

## Contributing
See [CONTRIBUTING.md](./CONTRIBUTING.md).

## Security
See [SECURITY.md](./SECURITY.md).

## License
MIT License. See [LICENSE](./LICENSE).
