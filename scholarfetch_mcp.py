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

from scholarfetch_cli import ElsevierClient, RetroCLI, UnifiedRecord


PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "scholarfetch-mcp"
SERVER_VERSION = "0.2.1"


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
    def __init__(self) -> None:
        self.saved_collections: Dict[str, List[Dict[str, Any]]] = {}

    def _effective_env(self) -> Dict[str, str]:
        env = dict(os.environ)
        env_file = env.get("SCHOLARFETCH_ENV_FILE", ".scholarfetch.env")
        if os.path.exists(env_file):
            try:
                with open(env_file, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip().strip("'").strip('"')
                        if key and key not in env:
                            env[key] = value
            except Exception:
                pass
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

    @staticmethod
    def _collection_name(raw: Any) -> str:
        name = str(raw or "default").strip()
        return name or "default"

    def _get_collection(self, name: str) -> List[Dict[str, Any]]:
        key = self._collection_name(name)
        return self.saved_collections.setdefault(key, [])

    @staticmethod
    def _record_key(payload: Dict[str, Any]) -> str:
        doi = str(payload.get("doi") or "").strip().lower()
        if doi:
            return doi
        return str(payload.get("title") or payload.get("raw_id") or "").strip().lower()

    def _select_best_record(self, cli: RetroCLI, rows: List[Any]) -> Optional[Dict[str, Any]]:
        records = [r for r in rows if r]
        if not records:
            return None
        records = cli._dedupe_records(records)
        cli._prefetch_fulltext_status(records, limit=min(12, len(records)))
        records.sort(
            key=lambda r: (
                cli._fulltext_rank(r),
                0 if (r.abstract or "").strip() else 1,
                -cli._record_year_int(r),
                (r.title or "").lower(),
            )
        )
        best = records[0]
        return best.__dict__

    def _resolve_record_payload(self, cli: RetroCLI, args: Dict[str, Any]) -> Dict[str, Any]:
        paper_json = args.get("paper_json")
        if isinstance(paper_json, str) and paper_json.strip():
            payload = json.loads(paper_json)
            if not isinstance(payload, dict):
                raise ValueError("paper_json must decode to an object")
            return payload

        doi = (args.get("doi") or "").strip()
        if doi:
            payload = self._select_best_record(cli, cli._parallel_doi_lookup(doi))
            if payload:
                return payload
            return {"title": doi, "doi": doi, "year": "", "authors": "", "venue": "", "abstract": "", "url": "", "pdf_url": "", "engine": "", "raw_id": doi}

        query = (args.get("query") or "").strip()
        if query:
            result_index = _safe_int(args.get("result_index", 1), 1, 1, 1000) - 1
            rows = cli._parallel_doi_lookup(query) if cli._looks_like_doi(query) else cli._parallel_search(query, limit_per_engine=6)
            rows = cli._dedupe_records(rows)
            if result_index >= len(rows):
                raise ValueError("result_index out of range")
            return rows[result_index].__dict__

        author_name = (args.get("author_name") or "").strip()
        if author_name:
            candidate_index = _safe_int(args.get("candidate_index", 1), 1, 1, 100) - 1
            paper_index = _safe_int(args.get("paper_index", 1), 1, 1, 1000) - 1
            cands = cli._openalex_author_candidates(author_name, per_page=25)
            if not cands:
                raise ValueError("author_name did not resolve to any author candidate")
            if candidate_index >= len(cands):
                raise ValueError("candidate_index out of range")
            papers = cli._openalex_works_for_author(cands[candidate_index]["author_id"], max_results=200)
            papers = cli._dedupe_records(papers)
            papers.sort(key=lambda r: (0 if (r.abstract or "").strip() else 1, -cli._record_year_int(r), (r.title or "").lower()))
            if paper_index >= len(papers):
                raise ValueError("paper_index out of range")
            return papers[paper_index].__dict__

        raise ValueError("Provide one of: paper_json, doi, query+result_index, or author_name+candidate_index+paper_index")

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
        fulltext_status = cli._elsevier_article_entitlement(doi)
        return {
            "doi": doi,
            "engines_used": cli.enabled_engines,
            "elsevier_fulltext_status": fulltext_status,
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

    def article_text(self, args: Dict[str, Any]) -> Dict[str, Any]:
        engines = _parse_csv_list(args.get("engines"))
        cli = self._runtime(engines)

        doi = (args.get("doi") or "").strip()
        if not doi:
            author_name = (args.get("author_name") or "").strip()
            candidate_index = _safe_int(args.get("candidate_index", 1), 1, 1, 100)
            paper_index = _safe_int(args.get("paper_index", 1), 1, 1, 1000)
            if not author_name:
                raise ValueError("doi or author_name is required")

            cands = cli._openalex_author_candidates(author_name, per_page=25)
            if not cands:
                return {"author_name": author_name, "found": False, "article_text": ""}
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
            doi = rec.doi or ""
            if not doi:
                if rec.engine == "arxiv":
                    resolved = cli._resolve_fulltext("", seed_record=rec)
                    return {
                        "author_name": author_name,
                        "candidate_index": candidate_index,
                        "paper_index": paper_index,
                        "title": rec.title,
                        "found": bool(resolved.get("found")),
                        "engine": resolved.get("engine", ""),
                        "source": resolved.get("source", ""),
                        "elsevier_fulltext_status": resolved.get("elsevier_fulltext_status", "UNKNOWN"),
                        "article_text": resolved.get("text", ""),
                    }
                return {
                    "author_name": author_name,
                    "candidate_index": candidate_index,
                    "paper_index": paper_index,
                    "title": rec.title,
                    "found": False,
                    "article_text": "",
                }

        resolved = cli._resolve_fulltext(doi)
        if not resolved.get("found"):
            return {
                "doi": doi,
                "found": False,
                "title": resolved.get("title") or doi,
                "engine": resolved.get("engine", ""),
                "source": resolved.get("source", ""),
                "elsevier_fulltext_status": resolved.get("elsevier_fulltext_status"),
                "article_text": "",
                "results": [r.__dict__ for r in (resolved.get("results") or [])],
            }

        return {
            "doi": doi,
            "found": True,
            "engine": resolved.get("engine", ""),
            "source": resolved.get("source", ""),
            "title": resolved.get("title") or doi,
            "elsevier_fulltext_status": resolved.get("elsevier_fulltext_status"),
            "article_text": resolved.get("text", ""),
        }

    def references(self, args: Dict[str, Any]) -> Dict[str, Any]:
        engines = _parse_csv_list(args.get("engines"))
        cli = self._runtime(engines)

        doi = (args.get("doi") or "").strip()
        if not doi:
            author_name = (args.get("author_name") or "").strip()
            candidate_index = _safe_int(args.get("candidate_index", 1), 1, 1, 100)
            paper_index = _safe_int(args.get("paper_index", 1), 1, 1, 1000)
            if not author_name:
                raise ValueError("doi or author_name is required")
            cands = cli._openalex_author_candidates(author_name, per_page=25)
            if not cands:
                return {"author_name": author_name, "count": 0, "references": []}
            cidx = candidate_index - 1
            if cidx >= len(cands):
                raise ValueError("candidate_index out of range")
            papers = cli._openalex_works_for_author(cands[cidx]["author_id"], max_results=200)
            papers = cli._dedupe_records(papers)
            papers.sort(key=lambda r: (0 if r.abstract else 1, -cli._record_year_int(r), (r.title or "").lower()))
            pidx = paper_index - 1
            if pidx >= len(papers):
                raise ValueError("paper_index out of range")
            rec = papers[pidx]
            doi = rec.doi or ""
            if not doi:
                return {
                    "author_name": author_name,
                    "candidate_index": candidate_index,
                    "paper_index": paper_index,
                    "title": rec.title,
                    "count": 0,
                    "references": [],
                }
            resolved = cli._resolve_references(doi, seed_record=rec)
        else:
            resolved = cli._resolve_references(doi)

        return {
            "doi": doi,
            "title": resolved.get("title") or doi,
            "source": resolved.get("source", ""),
            "count": resolved.get("count", 0),
            "references": resolved.get("references", []),
        }

    def saved_add(self, args: Dict[str, Any]) -> Dict[str, Any]:
        engines = _parse_csv_list(args.get("engines"))
        cli = self._runtime(engines)
        collection = self._collection_name(args.get("collection"))
        payload = self._resolve_record_payload(cli, args)
        bucket = self._get_collection(collection)
        key = self._record_key(payload)
        if any(self._record_key(item) == key for item in bucket):
            return {"collection": collection, "added": False, "reason": "already_present", "count": len(bucket), "paper": payload}
        bucket.append(payload)
        return {"collection": collection, "added": True, "count": len(bucket), "paper": payload}

    def saved_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        collection = self._collection_name(args.get("collection"))
        bucket = list(self._get_collection(collection))
        return {"collection": collection, "count": len(bucket), "results": bucket}

    def saved_remove(self, args: Dict[str, Any]) -> Dict[str, Any]:
        collection = self._collection_name(args.get("collection"))
        doi = str(args.get("doi") or "").strip().lower()
        title = str(args.get("title") or "").strip().lower()
        if not doi and not title:
            raise ValueError("doi or title is required")
        bucket = self._get_collection(collection)
        kept: List[Dict[str, Any]] = []
        removed: Optional[Dict[str, Any]] = None
        for item in bucket:
            item_doi = str(item.get("doi") or "").strip().lower()
            item_title = str(item.get("title") or "").strip().lower()
            if removed is None and ((doi and item_doi == doi) or (title and item_title == title)):
                removed = item
                continue
            kept.append(item)
        self.saved_collections[collection] = kept
        return {"collection": collection, "removed": bool(removed), "count": len(kept), "paper": removed}

    def saved_clear(self, args: Dict[str, Any]) -> Dict[str, Any]:
        collection = self._collection_name(args.get("collection"))
        removed = len(self._get_collection(collection))
        self.saved_collections[collection] = []
        return {"collection": collection, "cleared": True, "removed_count": removed, "count": 0}

    def saved_export(self, args: Dict[str, Any]) -> Dict[str, Any]:
        engines = _parse_csv_list(args.get("engines"))
        cli = self._runtime(engines)
        collection = self._collection_name(args.get("collection"))
        fmt = str(args.get("format") or "citations").strip().lower()
        if fmt == "text":
            fmt = "citations"
        style = str(args.get("style") or "harvard").strip().lower() or "harvard"
        include_refs = bool(args.get("include_references", False))
        bucket = list(self._get_collection(collection))
        recs = [
            UnifiedRecord(
                engine=str(item.get("engine") or ""),
                title=str(item.get("title") or ""),
                doi=str(item.get("doi") or ""),
                year=str(item.get("year") or ""),
                authors=str(item.get("authors") or ""),
                venue=str(item.get("venue") or ""),
                abstract=str(item.get("abstract") or ""),
                url=str(item.get("url") or ""),
                pdf_url=str(item.get("pdf_url") or ""),
                raw_id=str(item.get("raw_id") or item.get("doi") or item.get("title") or ""),
            )
            for item in bucket
        ]

        if fmt == "bib":
            content = "\n\n".join(cli._bibtex_entry(rec, i) for i, rec in enumerate(recs, start=1)) + ("\n" if recs else "")
        elif fmt == "citations":
            if style not in {"harvard", "apa", "ieee"}:
                style = "harvard"
            content = "\n".join(cli._citation_text(rec, style=style) for rec in recs) + ("\n" if recs else "")
        elif fmt == "abstracts":
            blocks: List[str] = []
            for i, rec in enumerate(recs, start=1):
                blocks.append(
                    "\n".join(
                        [
                            "=" * 80,
                            f"ITEM {i}",
                            "=" * 80,
                            f"Title: {rec.title or '-'}",
                            f"Authors: {rec.authors or '-'}",
                            f"Year: {rec.year or '-'}",
                            f"Venue: {rec.venue or '-'}",
                            f"DOI: {rec.doi or '-'}",
                            f"Engine: {rec.engine or '-'}",
                            f"URL: {rec.url or '-'}",
                            f"PDF: {rec.pdf_url or '-'}",
                            f"FullTextStatus: {cli._record_fulltext_status(rec).upper()}",
                            "",
                            "ABSTRACT",
                            "-" * 80,
                            rec.abstract or "(none)",
                            "",
                        ]
                    )
                )
            content = "\n".join(blocks)
        elif fmt == "fulltext":
            blocks: List[str] = []
            for i, rec in enumerate(recs, start=1):
                resolved = cli._resolve_fulltext(rec.doi, seed_record=rec)
                refs = cli._resolve_references(rec.doi, seed_record=rec) if include_refs and rec.doi else {"references": []}
                sections = [
                    "=" * 80,
                    f"ITEM {i}",
                    "=" * 80,
                    f"Title: {rec.title or '-'}",
                    f"Authors: {rec.authors or '-'}",
                    f"Year: {rec.year or '-'}",
                    f"Venue: {rec.venue or '-'}",
                    f"DOI: {rec.doi or '-'}",
                    f"Engine: {rec.engine or '-'}",
                    f"URL: {rec.url or '-'}",
                    f"PDF: {rec.pdf_url or '-'}",
                    f"FullTextStatus: {cli._record_fulltext_status(rec).upper()}",
                    "",
                    "ABSTRACT",
                    "-" * 80,
                    rec.abstract or "(none)",
                    "",
                    "FULL TEXT",
                    "-" * 80,
                    str(resolved.get("text") or "(not available)"),
                    "",
                ]
                if include_refs:
                    sections.extend(["REFERENCES", "-" * 80])
                    for ref in (refs.get("references") or []):
                        line = ref.get("text") or ""
                        if ref.get("doi"):
                            line += f" | doi={ref['doi']}"
                        sections.append(line)
                    if not (refs.get("references") or []):
                        sections.append("(none)")
                    sections.append("")
                blocks.append("\n".join(sections))
            content = "\n".join(blocks)
        else:
            raise ValueError("format must be one of: bib, citations, abstracts, fulltext")

        return {
            "collection": collection,
            "format": fmt,
            "style": style,
            "include_references": include_refs,
            "count": len(recs),
            "content": content,
        }

ENGINE_LIST = "elsevier, openalex, crossref, arxiv, europepmc, springer, semanticscholar"


TOOLS = [
    {
        "name": "scholarfetch_search",
        "description": "Start a research traversal from keywords, a DOI, or a person name. Returns deduplicated paper records that you can inspect, save, expand through references, or use as seeds for author exploration.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query, DOI, or person name."},
                "limit": {"type": "integer", "description": "Max results (1-100).", "default": 20},
                "engines": {"type": "string", "description": f"Optional comma-separated subset of: {ENGINE_LIST}."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "scholarfetch_doi_lookup",
        "description": "Enrich one known DOI with metadata, reading links, and full-text availability signals. Use this after search, references, or external inputs when you already know the DOI.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doi": {"type": "string"},
                "engines": {"type": "string", "description": f"Optional comma-separated subset of: {ENGINE_LIST}."},
            },
            "required": ["doi"],
        },
    },
    {
        "name": "scholarfetch_author_candidates",
        "description": "Disambiguate a human author name into ranked identity candidates. Use this before author_papers whenever the author name is ambiguous and you need a stable candidate_index for later calls.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
                "engines": {"type": "string", "description": "Optional comma-separated subset of engines. Must include openalex."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "scholarfetch_author_papers",
        "description": "Expand one author into a deduplicated paper list. This is the main way to traverse from an author node to a paper node. Use author_id when you already know the exact author, or author_name plus candidate_index after author_candidates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "author_id": {"type": "string"},
                "author_name": {"type": "string"},
                "candidate_index": {"type": "integer", "default": 1},
                "limit": {"type": "integer", "default": 50},
                "filters": {"type": "string", "description": "Optional comma-separated filters: year>=YYYY, year<=YYYY, year=YYYY, has:abstract, has:doi, has:pdf, venue:<text>, title:<text>, doi:<text>"},
                "engines": {"type": "string", "description": "Optional comma-separated subset of engines. Must include openalex."},
            },
        },
    },
    {
        "name": "scholarfetch_abstract",
        "description": "Read the best abstract available for a target paper. Use with a DOI or with author_name + candidate_index + paper_index after author_papers. Good for fast triage before saving or fetching full text.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doi": {"type": "string"},
                "author_name": {"type": "string"},
                "candidate_index": {"type": "integer", "default": 1},
                "paper_index": {"type": "integer", "default": 1},
                "engines": {"type": "string", "description": f"Optional comma-separated subset of: {ENGINE_LIST}."},
            },
        },
    },
    {
        "name": "scholarfetch_article_text",
        "description": "Read full paper text when machine-readable content is recoverable. Use with a DOI or with author_name + candidate_index + paper_index. This is the main reading tool for agents building a literature corpus.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doi": {"type": "string"},
                "author_name": {"type": "string"},
                "candidate_index": {"type": "integer", "default": 1},
                "paper_index": {"type": "integer", "default": 1},
                "engines": {"type": "string", "description": f"Optional comma-separated subset of: {ENGINE_LIST}."}
            },
        },
    },
    {
        "name": "scholarfetch_references",
        "description": "Expand a paper into its references. Use with a DOI or with author_name + candidate_index + paper_index. This is the main edge-expansion tool for traversing the research tree from one paper into prior literature.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doi": {"type": "string"},
                "author_name": {"type": "string"},
                "candidate_index": {"type": "integer", "default": 1},
                "paper_index": {"type": "integer", "default": 1},
                "engines": {"type": "string", "description": f"Optional comma-separated subset of: {ENGINE_LIST}."}
            },
        },
    },
    {
        "name": "scholarfetch_saved_add",
        "description": "Add one paper to a named in-memory reading list on the MCP server. Best used with paper_json copied from previous tool results, but DOI, query+result_index, or author workflow are also supported. Use the same collection name across calls to build a session-level research set.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "Optional reading-list name. Default: default."},
                "paper_json": {"type": "string", "description": "Optional JSON object for one paper record returned by another ScholarFetch tool."},
                "doi": {"type": "string"},
                "query": {"type": "string"},
                "result_index": {"type": "integer", "default": 1},
                "author_name": {"type": "string"},
                "candidate_index": {"type": "integer", "default": 1},
                "paper_index": {"type": "integer", "default": 1},
                "engines": {"type": "string", "description": f"Optional comma-separated subset of: {ENGINE_LIST}."}
            },
        },
    },
    {
        "name": "scholarfetch_saved_list",
        "description": "List all papers currently saved in a named in-memory reading list. Use this to inspect the agent's working set before exporting or removing items.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "Optional reading-list name. Default: default."}
            },
        },
    },
    {
        "name": "scholarfetch_saved_remove",
        "description": "Remove one paper from a named in-memory reading list by DOI or exact title. Use after review when a candidate is no longer relevant.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "Optional reading-list name. Default: default."},
                "doi": {"type": "string"},
                "title": {"type": "string"}
            },
        },
    },
    {
        "name": "scholarfetch_saved_clear",
        "description": "Clear all papers from a named in-memory reading list. Use when restarting a research branch or ending a session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "Optional reading-list name. Default: default."}
            },
        },
    },
    {
        "name": "scholarfetch_saved_export",
        "description": "Export the current reading list as citations, abstracts, BibTeX, or an aggregated full-text corpus. This is the handoff tool for downstream synthesis agents.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "Optional reading-list name. Default: default."},
                "format": {"type": "string", "description": "One of: citations, abstracts, bib, fulltext.", "default": "citations"},
                "style": {"type": "string", "description": "For citations only: harvard, apa, ieee.", "default": "harvard"},
                "include_references": {"type": "boolean", "description": "When format=fulltext, include the reference lists too.", "default": False},
                "engines": {"type": "string", "description": f"Optional comma-separated subset of: {ENGINE_LIST}."}
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
    if name == "scholarfetch_article_text":
        return SERVICE.article_text(args)
    if name == "scholarfetch_references":
        return SERVICE.references(args)
    if name == "scholarfetch_saved_add":
        return SERVICE.saved_add(args)
    if name == "scholarfetch_saved_list":
        return SERVICE.saved_list(args)
    if name == "scholarfetch_saved_remove":
        return SERVICE.saved_remove(args)
    if name == "scholarfetch_saved_clear":
        return SERVICE.saved_clear(args)
    if name == "scholarfetch_saved_export":
        return SERVICE.saved_export(args)
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
