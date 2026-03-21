# ScholarFetch

![ScholarFetch Logo](./assets/scholarfetch-logo.svg)

ScholarFetch is a multi-engine academic paper search and abstract retrieval toolkit with:
- a terminal-first Python CLI
- a classic MCP server (stdio)
- a FastMCP server (`stdio`, `sse`, `streamable-http`)

It aggregates metadata, abstracts, DOI enrichment, and reading links across major scholarly APIs in one workflow.

## Live Demo
- Web demo (Hugging Face Space): https://huggingface.co/spaces/Laibniz/ScholarFetch_Web

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

## Why ScholarFetch
- Research API aggregation: one interface over multiple scholarly databases
- Better recall: parallel retrieval + deduplication across providers
- Better precision: DOI-first retrieval and author disambiguation workflow
- Agent-ready: MCP tools for LLM systems and automation pipelines

## Installation
```bash
git clone https://github.com/laibniz/scholarfetch.git
cd scholarfetch
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
python3 setup.py develop
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
/author Albert Einstein
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

## Common Research Flows
```text
# topic search
/search graph neural networks

# author investigation
/author Albert Einstein
/papers 1 year>=2018 has:abstract
/abstract 1

# DOI enrichment
/doi 10.1007/s43039-022-00057-w
/article 10.1007/s43039-022-00057-w
```

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

## Discovery Keywords
Academic search API, paper metadata API, abstract retrieval API, DOI lookup tool, OpenAlex API client, Crossref API search, Elsevier Scopus API CLI, Springer Nature Metadata API integration, Europe PMC search, arXiv paper search, Semantic Scholar enrichment, MCP server for research, FastMCP scholarly tools, Python research automation, literature review tooling.

## Project Profile
- Primary entity: `ScholarFetch`
- Category: `Multi-engine scholarly search CLI + MCP server`
- Core intents:
  - Find papers by keyword, author, DOI
  - Retrieve and rank abstracts across providers
  - Export structured research results
  - Integrate scholarly retrieval tools in LLM agents via MCP
- Canonical links:
  - GitHub repo: `https://github.com/laibniz/scholarfetch`
  - Live web demo: `https://huggingface.co/spaces/Laibniz/ScholarFetch_Web`

## FAQ
- Why are some abstracts missing?
Provider entitlements differ. ScholarFetch ranks and falls back across engines, but access still depends on source availability and licensing.

- Can I force a single engine?
Yes, via CLI (`/config only <engine>`) or MCP tool argument (`engines` subset).

- Can I use it with Langflow or other agent builders?
Yes. Use `scholarfetch_mcp.py` or `scholarfetch_fastmcp.py`. See [MCP_SERVER.md](./MCP_SERVER.md).

- Is there a web UI?
Yes, the Space demo is available at https://huggingface.co/spaces/Laibniz/ScholarFetch_Web.

## Contributing
See [CONTRIBUTING.md](./CONTRIBUTING.md).

## Security
See [SECURITY.md](./SECURITY.md).

## License
MIT License. See [LICENSE](./LICENSE).
