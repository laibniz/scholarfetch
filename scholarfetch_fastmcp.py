#!/usr/bin/env python3
"""ScholarFetch MCP server powered by FastMCP."""

from __future__ import annotations

import argparse
import os
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from scholarfetch_mcp import ScholarFetchService


SERVICE: Optional[ScholarFetchService] = None


TOOL_NOTE = (
    "Credentials are loaded server-side from environment (.scholarfetch.env by default). "
    "Do not pass API keys as tool arguments."
)


def _load_local_env() -> None:
    env_file = os.getenv("SCHOLARFETCH_ENV_FILE", ".scholarfetch.env")
    if not os.path.exists(env_file):
        return
    try:
        with open(env_file, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        return


def _service() -> ScholarFetchService:
    global SERVICE
    if SERVICE is None:
        SERVICE = ScholarFetchService()
    return SERVICE


def build_server(host: str, port: int, streamable_http_path: str) -> FastMCP:
    mcp = FastMCP(
        name="ScholarFetch FastMCP",
        instructions=(
            "Multi-engine scholarly fetch server. Use tools for search, DOI enrichment, "
            "author disambiguation, deduplicated papers, and abstract retrieval."
        ),
        host=host,
        port=port,
        streamable_http_path=streamable_http_path,
    )

    @mcp.tool(
        name="scholarfetch_search",
        description=(
            "Unified scholarly search across enabled engines. Supports keyword, DOI, and person-name query routing. "
            "Parameter `engines` is a comma-separated list (e.g. 'openalex,crossref,springer'). "
            + TOOL_NOTE
        ),
    )
    def scholarfetch_search(
        query: str,
        limit: int = 20,
        engines: str = "",
    ) -> Dict[str, Any]:
        return _service().search(
            {
                "query": query,
                "limit": limit,
                "engines": engines,
            }
        )

    @mcp.tool(
        name="scholarfetch_doi_lookup",
        description=(
            "Cross-engine DOI enrichment. Returns metadata, abstract availability, and links. "
            "Parameter `engines` is a comma-separated list. "
            + TOOL_NOTE
        ),
    )
    def scholarfetch_doi_lookup(
        doi: str,
        engines: str = "",
    ) -> Dict[str, Any]:
        return _service().doi_lookup(
            {
                "doi": doi,
                "engines": engines,
            }
        )

    @mcp.tool(
        name="scholarfetch_author_candidates",
        description=(
            "Resolve and rank author candidates (OpenAlex-based) for identity disambiguation. "
            "Parameter `engines` is comma-separated and must include openalex. "
            + TOOL_NOTE
        ),
    )
    def scholarfetch_author_candidates(
        name: str,
        limit: int = 10,
        engines: str = "",
    ) -> Dict[str, Any]:
        return _service().author_candidates(
            {
                "name": name,
                "limit": limit,
                "engines": engines,
            }
        )

    @mcp.tool(
        name="scholarfetch_author_papers",
        description=(
            "Fetch deduplicated papers for a selected author using author_id OR author_name + candidate_index. "
            "Use comma-separated `filters` (e.g. 'year>=2020,has:abstract,venue:marketing'). "
            "Parameter `engines` is comma-separated and must include openalex. "
            + TOOL_NOTE
        ),
    )
    def scholarfetch_author_papers(
        author_id: Optional[str] = None,
        author_name: Optional[str] = None,
        candidate_index: int = 1,
        limit: int = 50,
        filters: str = "",
        engines: str = "",
    ) -> Dict[str, Any]:
        return _service().author_papers(
            {
                "author_id": author_id,
                "author_name": author_name,
                "candidate_index": candidate_index,
                "limit": limit,
                "filters": filters,
                "engines": engines,
            }
        )

    @mcp.tool(
        name="scholarfetch_abstract",
        description=(
            "Retrieve best abstract by DOI OR via author flow (author_name + candidate_index + paper_index). "
            "Parameter `engines` is a comma-separated list. "
            + TOOL_NOTE
        ),
    )
    def scholarfetch_abstract(
        doi: Optional[str] = None,
        author_name: Optional[str] = None,
        candidate_index: int = 1,
        paper_index: int = 1,
        engines: str = "",
    ) -> Dict[str, Any]:
        return _service().abstract(
            {
                "doi": doi,
                "author_name": author_name,
                "candidate_index": candidate_index,
                "paper_index": paper_index,
                "engines": engines,
            }
        )

    return mcp


def self_test() -> None:
    engines = ["crossref", "openalex", "arxiv", "europepmc"]
    print("[fastmcp self-test] search")
    print(_service().search({"query": "10.1007/s43039-022-00057-w", "limit": 3, "engines": engines})["count"])
    print("[fastmcp self-test] candidates")
    print(_service().author_candidates({"name": "Andrea De Mauro", "limit": 3, "engines": ["openalex"]})["count"])
    print("[fastmcp self-test] abstract")
    out = _service().abstract({"doi": "10.1007/s43039-022-00057-w", "engines": engines})
    print(bool(out.get("abstract")), out.get("engine"))


def main() -> None:
    _load_local_env()
    parser = argparse.ArgumentParser(description="ScholarFetch FastMCP server")
    parser.add_argument("--self-test", action="store_true", help="Run local self-test and exit")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--http-path", default="/mcp", help="streamable-http path")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    server = build_server(args.host, args.port, args.http_path)
    if args.transport == "streamable-http":
        print(f"ScholarFetch FastMCP running on http://{args.host}:{args.port}{args.http_path}", flush=True)
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
