#!/usr/bin/env python3
"""ScholarFetch MCP server (stdio, JSON-RPC 2.0).

Implements core MCP methods:
- initialize
- tools/list
- tools/call

Tools are stateless and use server-side environment credentials only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

from scholarfetch_cli import ElsevierClient, RetroCLI


PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "scholarfetch-mcp"
SERVER_VERSION = "0.1.0"


def _safe_int(value: Any, default: int, min_v: int, max_v: int) -> int:
    try:
        x = int(value)
    except Exception:
        return default
    return max(min_v, min(max_v, x))


def _parse_csv_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if not isinstance(value, str):
        return []
    return [chunk.strip() for chunk in value.split(",") if chunk.strip()]


class ScholarFetchService:
    def _effective_env(self) -> Dict[str, str]:
        env = dict(os.environ)
        return env

    def _runtime(self, engines: Optional[List[str]]) -> RetroCLI:
        env = self._effective_env()
        api_key = (env.get("ELSEVIER_API_KEY") or "").strip()
        inst = (env.get("ELSEVIER_INSTTOKEN") or "").strip()

        # Keep Elsevier optional for non-Elsevier-only workflows.
        client = ElsevierClient(api_key or "MISSING_API_KEY", inst)
        cli = RetroCLI(client)

        # Override springer keys directly from effective env.
        cli.springer_meta_key = (env.get("SPRINGER_META_API_KEY") or "").strip()
        cli.springer_oa_key = (env.get("SPRINGER_OPENACCESS_API_KEY") or "").strip()

        # Deterministic engine set for MCP: start from defaults, not saved local profile.
        cli.enabled_engines = cli.default_engines[:]
        if engines:
            picked = [e for e in engines if e in cli.available_engines]
            if picked:
                cli.enabled_engines = picked
        return cli

    def search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        query = (args.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        limit = _safe_int(args.get("limit", 20), 20, 1, 100)
        engines = _parse_csv_list(args.get("engines"))
        cli = self._runtime(engines)
        if cli._looks_like_doi(query):
            rows = cli._parallel_doi_lookup(query)
        elif cli._looks_like_person_name(query) and "openalex" in cli.enabled_engines:
            cands = cli._openalex_author_candidates(query, per_page=10)
            if cands:
                rows = cli._openalex_works_for_author(cands[0]["author_id"], max_results=max(limit, 30))
                rows = cli._dedupe_records(rows)
            else:
                rows = []
        else:
            rows = cli._parallel_search(query, limit_per_engine=max(2, min(20, limit // max(1, len(cli.enabled_engines)) + 1)))

        rows = cli._dedupe_records(rows)[:limit]
        return {
            "query": query,
            "engines_used": cli.enabled_engines,
            "count": len(rows),
            "results": [r.__dict__ for r in rows],
        }

    def doi_lookup(self, args: Dict[str, Any]) -> Dict[str, Any]:
        doi = (args.get("doi") or "").strip()
        if not doi:
            raise ValueError("doi is required")
        engines = _parse_csv_list(args.get("engines"))
        cli = self._runtime(engines)
        rows = cli._parallel_doi_lookup(doi)
        return {
            "doi": doi,
            "engines_used": cli.enabled_engines,
            "count": len(rows),
            "results": [r.__dict__ for r in rows],
        }

    def author_candidates(self, args: Dict[str, Any]) -> Dict[str, Any]:
        name = (args.get("name") or "").strip()
        if not name:
            raise ValueError("name is required")
        limit = _safe_int(args.get("limit", 10), 10, 1, 50)
        engines = _parse_csv_list(args.get("engines"))
        cli = self._runtime(engines)
        if "openalex" not in cli.enabled_engines:
            raise ValueError("openalex engine must be enabled for author candidate resolution")
        cands = cli._openalex_author_candidates(name, per_page=max(10, limit))[:limit]
        return {
            "name": name,
            "count": len(cands),
            "candidates": cands,
        }

    def author_papers(self, args: Dict[str, Any]) -> Dict[str, Any]:
        engines = _parse_csv_list(args.get("engines"))
        cli = self._runtime(engines)
        if "openalex" not in cli.enabled_engines:
            raise ValueError("openalex engine must be enabled for author papers")

        author_id = (args.get("author_id") or "").strip()
        author_name = (args.get("author_name") or "").strip()
        candidate_index = _safe_int(args.get("candidate_index", 1), 1, 1, 100)
        limit = _safe_int(args.get("limit", 50), 50, 1, 300)
        filters = _parse_csv_list(args.get("filters"))

        if not author_id:
            if not author_name:
                raise ValueError("author_id or author_name is required")
            cands = cli._openalex_author_candidates(author_name, per_page=25)
            if not cands:
                return {"author_name": author_name, "count": 0, "results": []}
            idx = candidate_index - 1
            if idx >= len(cands):
                raise ValueError("candidate_index out of range")
            selected = cands[idx]
            author_id = selected["author_id"]
            selected_meta = selected
        else:
            selected_meta = {"author_id": author_id}

        rows = cli._openalex_works_for_author(author_id, max_results=max(limit, 120))
        rows = cli._dedupe_records(rows)
        rows.sort(key=lambda r: (0 if r.abstract else 1, -cli._record_year_int(r), (r.title or "").lower()))
        if filters:
            rows = cli._apply_paper_filters(rows, filters)
        rows = rows[:limit]
        return {
            "author": selected_meta,
            "filters": filters,
            "count": len(rows),
            "results": [r.__dict__ for r in rows],
        }

    def abstract(self, args: Dict[str, Any]) -> Dict[str, Any]:
        engines = _parse_csv_list(args.get("engines"))
        cli = self._runtime(engines)

        doi = (args.get("doi") or "").strip()
        if doi:
            rows = cli._parallel_doi_lookup(doi)
            with_abs = [r for r in rows if (r.abstract or "").strip()]
            if not with_abs:
                return {"doi": doi, "found": False, "abstract": ""}
            best = max(with_abs, key=cli._abstract_quality_score)
            return {
                "doi": doi,
                "found": True,
                "engine": best.engine,
                "title": best.title,
                "abstract": best.abstract,
            }

        author_name = (args.get("author_name") or "").strip()
        candidate_index = _safe_int(args.get("candidate_index", 1), 1, 1, 100)
        paper_index = _safe_int(args.get("paper_index", 1), 1, 1, 1000)
        if not author_name:
            raise ValueError("doi or author_name is required")

        cands = cli._openalex_author_candidates(author_name, per_page=25)
        if not cands:
            return {"author_name": author_name, "found": False, "abstract": ""}
        cidx = candidate_index - 1
        if cidx >= len(cands):
            raise ValueError("candidate_index out of range")
        author_id = cands[cidx]["author_id"]
        papers = cli._openalex_works_for_author(author_id, max_results=200)
        papers = cli._dedupe_records(papers)
        papers.sort(key=lambda r: (0 if r.abstract else 1, -cli._record_year_int(r), (r.title or "").lower()))
        pidx = paper_index - 1
        if pidx >= len(papers):
            raise ValueError("paper_index out of range")
        rec = papers[pidx]
        if rec.abstract:
            return {
                "author_name": author_name,
                "candidate_index": candidate_index,
                "paper_index": paper_index,
                "title": rec.title,
                "engine": rec.engine,
                "abstract": rec.abstract,
            }
        if rec.doi:
            rows = cli._parallel_doi_lookup(rec.doi)
            with_abs = [r for r in rows if (r.abstract or "").strip()]
            if with_abs:
                best = max(with_abs, key=cli._abstract_quality_score)
                return {
                    "author_name": author_name,
                    "candidate_index": candidate_index,
                    "paper_index": paper_index,
                    "title": rec.title,
                    "engine": best.engine,
                    "abstract": best.abstract,
                }
        return {
            "author_name": author_name,
            "candidate_index": candidate_index,
            "paper_index": paper_index,
            "title": rec.title,
            "engine": rec.engine,
            "abstract": "",
        }


TOOLS = [
    {
        "name": "scholarfetch_search",
        "description": "Unified scholarly search across enabled engines. Supports keyword, DOI, and person-name query routing. Returns deduplicated records.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query, DOI, or person name."},
                "limit": {"type": "integer", "description": "Max results (1-100).", "default": 20},
                "engines": {"type": "string", "description": "Optional comma-separated engines, e.g. 'openalex,crossref,springer'."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "scholarfetch_doi_lookup",
        "description": "Cross-engine DOI enrichment. Returns all matched records from enabled engines (metadata, abstract availability, links).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doi": {"type": "string"},
                "engines": {"type": "string", "description": "Optional comma-separated engines."},
            },
            "required": ["doi"],
        },
    },
    {
        "name": "scholarfetch_author_candidates",
        "description": "Resolve and rank author identity candidates (OpenAlex-based), including works count/citations/affiliation for disambiguation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
                "engines": {"type": "string", "description": "Optional comma-separated engines. Must include openalex."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "scholarfetch_author_papers",
        "description": "Fetch deduplicated papers for a selected author. Use either author_id directly or author_name + candidate_index.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "author_id": {"type": "string"},
                "author_name": {"type": "string"},
                "candidate_index": {"type": "integer", "default": 1},
                "limit": {"type": "integer", "default": 50},
                "filters": {"type": "string", "description": "Optional comma-separated filters: year>=YYYY, year<=YYYY, year=YYYY, has:abstract, has:doi, has:pdf, venue:<text>, title:<text>, doi:<text>"},
                "engines": {"type": "string", "description": "Optional comma-separated engines. Must include openalex."},
            },
        },
    },
    {
        "name": "scholarfetch_abstract",
        "description": "Retrieve best abstract by DOI OR via author workflow (author_name + candidate_index + paper_index).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doi": {"type": "string"},
                "author_name": {"type": "string"},
                "candidate_index": {"type": "integer", "default": 1},
                "paper_index": {"type": "integer", "default": 1},
                "engines": {"type": "string", "description": "Optional comma-separated engines."},
            },
        },
    },
]


SERVICE = ScholarFetchService()


def handle_tool_call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if name == "scholarfetch_search":
        return SERVICE.search(args)
    if name == "scholarfetch_doi_lookup":
        return SERVICE.doi_lookup(args)
    if name == "scholarfetch_author_candidates":
        return SERVICE.author_candidates(args)
    if name == "scholarfetch_author_papers":
        return SERVICE.author_papers(args)
    if name == "scholarfetch_abstract":
        return SERVICE.abstract(args)
    raise ValueError(f"Unknown tool: {name}")


def read_message() -> Optional[Dict[str, Any]]:
    headers: Dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line = line.decode("utf-8", errors="replace").strip()
        if line == "":
            break
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    if not body:
        return None
    return json.loads(body.decode("utf-8", errors="replace"))


def write_message(payload: Dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(data)}\r\n\r\n".encode("utf-8")
    sys.stdout.buffer.write(header)
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def write_response(msg_id: Any, result: Dict[str, Any]) -> None:
    write_message({"jsonrpc": "2.0", "id": msg_id, "result": result})


def write_error(msg_id: Any, code: int, message: str) -> None:
    write_message(
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": code, "message": message},
        }
    )


def handle_request(msg: Dict[str, Any]) -> None:
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        write_response(
            msg_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
        return

    if method == "initialized":
        return

    if method == "ping":
        write_response(msg_id, {})
        return

    if method == "tools/list":
        write_response(msg_id, {"tools": TOOLS})
        return

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            out = handle_tool_call(name, args)
            write_response(
                msg_id,
                {
                    "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}],
                    "isError": False,
                },
            )
        except Exception as exc:
            write_response(
                msg_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )
        return

    if msg_id is not None:
        write_error(msg_id, -32601, f"Method not found: {method}")


def serve_stdio() -> None:
    while True:
        msg = read_message()
        if msg is None:
            break
        try:
            handle_request(msg)
        except Exception as exc:
            msg_id = msg.get("id") if isinstance(msg, dict) else None
            if msg_id is not None:
                write_error(msg_id, -32000, f"Server error: {exc}")


def self_test() -> None:
    print("[self-test] scholarfetch_search")
    s = SERVICE.search({"query": "10.1007/s43039-022-00057-w", "limit": 5})
    print("count:", s.get("count"), "engines:", s.get("engines_used"))

    print("[self-test] author candidates")
    a = SERVICE.author_candidates({"name": "Andrea De Mauro", "limit": 3})
    print("candidates:", a.get("count"))

    print("[self-test] abstract by doi")
    ab = SERVICE.abstract({"doi": "10.1007/s43039-022-00057-w"})
    print("found:", ab.get("found", bool(ab.get("abstract"))), "engine:", ab.get("engine"))


def main() -> None:
    parser = argparse.ArgumentParser(description="ScholarFetch MCP server")
    parser.add_argument("--self-test", action="store_true", help="Run local self-test instead of MCP stdio loop")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return
    serve_stdio()


if __name__ == "__main__":
    main()
