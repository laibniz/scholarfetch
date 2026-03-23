#!/usr/bin/env python3
"""ScholarFetch MCP server powered by FastMCP."""

from __future__ import annotations

import argparse
import logging
import os
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from starlette.requests import ClientDisconnect

from scholarfetch_mcp import ScholarFetchService


SERVICE: Optional[ScholarFetchService] = None

ENGINE_LIST = "elsevier, openalex, crossref, arxiv, europepmc, springer, semanticscholar"


class _IgnoreClientDisconnectFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info:
            exc = record.exc_info[1]
            if isinstance(exc, ClientDisconnect):
                return False
        message = ""
        try:
            message = record.getMessage()
        except Exception:
            pass
        if "Received exception from stream" in message:
            return False
        return True


def _configure_logging() -> None:
    filt = _IgnoreClientDisconnectFilter()
    for logger_name in ("mcp.server.streamable_http", "mcp.server.lowlevel.server"):
        logger = logging.getLogger(logger_name)
        logger.addFilter(filt)


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
            "Multi-engine scholarly research server. Use it to traverse from keyword -> paper -> author -> paper -> references, "
            "save interesting papers into an in-memory reading list, and export citations/abstracts/full-text corpora for downstream synthesis."
        ),
        host=host,
        port=port,
        streamable_http_path=streamable_http_path,
    )

    @mcp.tool(
        name="scholarfetch_search",
        description=(
            "Start a research traversal from keywords, a DOI, or a person name. "
            "Returns deduplicated paper records that you can inspect, save, expand through references, or use as seeds for author exploration. "
            f"If you pass `engines`, use a comma-separated subset of: {ENGINE_LIST}."
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
            "Enrich one known DOI with metadata, reading links, and full-text availability signals. "
            f"If you pass `engines`, use a comma-separated subset of: {ENGINE_LIST}."
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
            "Disambiguate a human author name into ranked identity candidates. "
            "Use this before `scholarfetch_author_papers` when the name is ambiguous and you need a stable `candidate_index`. "
            "If you pass `engines`, it must include `openalex`."
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
            "Expand one author into a deduplicated paper list. This is the main author->paper traversal tool and supports research filters. "
            "Use `author_id` when you already know the exact author, or `author_name` plus `candidate_index` after `scholarfetch_author_candidates`. "
            "Supported comma-separated `filters`: year>=YYYY, year<=YYYY, year=YYYY, has:abstract, has:doi, has:pdf, venue:<text>, title:<text>, doi:<text>. "
            "If you pass `engines`, it must include `openalex`."
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
            "Read the best abstract available for a paper. Use with a DOI or with author_name + candidate_index + paper_index after author_papers. "
            f"If you pass `engines`, use a comma-separated subset of: {ENGINE_LIST}."
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

    @mcp.tool(
        name="scholarfetch_article_text",
        description=(
            "Read full paper text when machine-readable content is recoverable. Use with a DOI or with author_name + candidate_index + paper_index. "
            "Uses Elsevier first, then open-access fallbacks such as Springer OA, Europe PMC, arXiv PDF, and generic PDF URLs when text is recoverable. "
            f"If you pass `engines`, use a comma-separated subset of: {ENGINE_LIST}."
        ),
    )
    def scholarfetch_article_text(
        doi: Optional[str] = None,
        author_name: Optional[str] = None,
        candidate_index: int = 1,
        paper_index: int = 1,
        engines: str = "",
    ) -> Dict[str, Any]:
        return _service().article_text(
            {
                "doi": doi,
                "author_name": author_name,
                "candidate_index": candidate_index,
                "paper_index": paper_index,
                "engines": engines,
            }
        )

    @mcp.tool(
        name="scholarfetch_references",
        description=(
            "Expand a paper into its references. Use with a DOI or with author_name + candidate_index + paper_index. "
            "This is the main edge-expansion tool for traversing the literature graph. "
            f"If you pass `engines`, use a comma-separated subset of: {ENGINE_LIST}."
        ),
    )
    def scholarfetch_references(
        doi: Optional[str] = None,
        author_name: Optional[str] = None,
        candidate_index: int = 1,
        paper_index: int = 1,
        engines: str = "",
    ) -> Dict[str, Any]:
        return _service().references(
            {
                "doi": doi,
                "author_name": author_name,
                "candidate_index": candidate_index,
                "paper_index": paper_index,
                "engines": engines,
            }
        )

    @mcp.tool(
        name="scholarfetch_saved_add",
        description=(
            "Add one paper to a named in-memory reading list on the MCP server. Best input is paper_json copied from another ScholarFetch tool result, "
            "but DOI, query+result_index, or author_name+candidate_index+paper_index also work. Reuse the same collection name across calls to keep one research session together."
        ),
    )
    def scholarfetch_saved_add(
        collection: str = "default",
        paper_json: Optional[str] = None,
        doi: Optional[str] = None,
        query: Optional[str] = None,
        result_index: int = 1,
        author_name: Optional[str] = None,
        candidate_index: int = 1,
        paper_index: int = 1,
        engines: str = "",
    ) -> Dict[str, Any]:
        return _service().saved_add(
            {
                "collection": collection,
                "paper_json": paper_json,
                "doi": doi,
                "query": query,
                "result_index": result_index,
                "author_name": author_name,
                "candidate_index": candidate_index,
                "paper_index": paper_index,
                "engines": engines,
            }
        )

    @mcp.tool(
        name="scholarfetch_saved_list",
        description=(
            "List all papers currently saved in a named in-memory reading list. Use this to inspect the working set before exporting or removing items."
        ),
    )
    def scholarfetch_saved_list(collection: str = "default") -> Dict[str, Any]:
        return _service().saved_list({"collection": collection})

    @mcp.tool(
        name="scholarfetch_saved_remove",
        description=(
            "Remove one paper from a named in-memory reading list by DOI or exact title."
        ),
    )
    def scholarfetch_saved_remove(
        collection: str = "default",
        doi: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        return _service().saved_remove({"collection": collection, "doi": doi, "title": title})

    @mcp.tool(
        name="scholarfetch_saved_clear",
        description=(
            "Clear all papers from a named in-memory reading list. Useful when restarting a research branch."
        ),
    )
    def scholarfetch_saved_clear(collection: str = "default") -> Dict[str, Any]:
        return _service().saved_clear({"collection": collection})

    @mcp.tool(
        name="scholarfetch_saved_export",
        description=(
            "Export the current reading list as citations, abstracts, BibTeX, or an aggregated full-text corpus. "
            "Valid `format` values: citations, abstracts, bib, fulltext. "
            "Valid `style` values when `format=citations`: harvard, apa, ieee. "
            "Use `include_references=true` with `format=fulltext` when you want a richer downstream synthesis corpus."
        ),
    )
    def scholarfetch_saved_export(
        collection: str = "default",
        format: str = "citations",
        style: str = "harvard",
        include_references: bool = False,
        engines: str = "",
    ) -> Dict[str, Any]:
        return _service().saved_export(
            {
                "collection": collection,
                "format": format,
                "style": style,
                "include_references": include_references,
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
    _configure_logging()
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
