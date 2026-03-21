# Contributing to ScholarFetch

## Development Setup
1. Clone repository
2. Create virtual environment
3. Run CLI locally

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 scholarfetch.py
```

## Coding Guidelines
- Keep changes focused and small.
- Preserve backward compatibility when possible.
- Do not hardcode secrets/API keys.
- Prefer clear, defensive parsing for external APIs.
- Add/update docs when behavior changes.

## Testing Expectations
Before opening a PR:
1. `python3 -m py_compile scholarfetch_cli.py scholarfetch.py scholarfetch_mcp.py scholarfetch_fastmcp.py`
2. Manually test key flows:
   - `/search <query>`
   - `/author <name>` -> `/papers <index>`
   - `/doi <doi>` -> `/abstract <index>` or `/abstract <doi>`
   - `/config only ...` and `/config reset`
3. MCP server smoke test:
   - `python3 scholarfetch_mcp.py --self-test`
   - `python3 scholarfetch_fastmcp.py --self-test`
   - `timeout 3s python3 scholarfetch_fastmcp.py --transport streamable-http --host 127.0.0.1 --port 8000 --http-path /mcp`

## Pull Requests
Include:
- Problem statement
- What changed
- How tested (commands + observed behavior)
- Any API/provider caveats

## Scope Notes
Provider APIs differ in entitlement and rate limits. A PR should gracefully handle:
- Missing fields
- Unauthorized views
- Empty results
- Temporary errors
