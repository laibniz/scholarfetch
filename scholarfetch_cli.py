#!/usr/bin/env python3
"""ScholarFetch: multi-engine scholarly search CLI.

Docs referenced:
- https://dev.elsevier.com/tecdoc_api_authentication.html
- https://dev.elsevier.com/documentation/SCOPUSSearchAPI.wadl
- https://dev.elsevier.com/documentation/AuthorSearchAPI.wadl
- https://dev.elsevier.com/documentation/AbstractRetrievalAPI.wadl
- https://dev.elsevier.com/documentation/ArticleRetrievalAPI.wadl
"""

from __future__ import annotations

import contextlib
import atexit
import curses
import getpass
import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import textwrap
import tty
import termios
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple


def restore_terminal_state() -> None:
    try:
        if sys.stdout:
            sys.stdout.write(ANSI.RESET if "ANSI" in globals() else "\033[0m")
            sys.stdout.flush()
    except Exception:
        pass
    try:
        if sys.stdin.isatty():
            subprocess.run(
                ["stty", "sane"],
                stdin=sys.stdin,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    except Exception:
        pass


class ANSI:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    BRIGHT_BLACK = "\033[90m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_MAGENTA = "\033[95m"


atexit.register(restore_terminal_state)


RETRO_BANNER = r"""
 ███████╗  ██████╗ ██╗  ██╗  ██████╗  ██╗       █████╗  ██████╗  ███████╗ ███████╗ ████████╗  ██████╗ ██╗  ██╗
 ██╔════╝ ██╔════╝ ██║  ██║ ██╔═══██╗ ██║      ██╔══██╗ ██╔══██╗ ██╔════╝ ██╔════╝ ╚══██╔══╝ ██╔════╝ ██║  ██║
 ███████╗ ██║      ███████║ ██║   ██║ ██║      ███████║ ██████╔╝ █████╗   █████╗      ██║    ██║      ███████║
 ╚════██║ ██║      ██╔══██║ ██║   ██║ ██║      ██╔══██║ ██╔══██╗ ██╔══╝   ██╔══╝      ██║    ██║      ██╔══██║
 ███████║ ╚██████╗ ██║  ██║ ╚██████╔╝ ███████╗ ██║  ██║ ██║  ██║ ██║      ███████╗    ██║    ╚██████╗ ██║  ██║
 ╚══════╝  ╚═════╝ ╚═╝  ╚═╝  ╚═════╝  ╚══════╝ ╚═╝  ╚═╝ ╚═╝  ╚═╝ ╚═╝      ╚══════╝    ╚═╝     ╚═════╝ ╚═╝  ╚═╝
"""


@dataclass
class ArticleEntry:
    title: str
    doi: str
    date: str
    source: str
    creator: str
    description: str
    eid: str


@dataclass
class UnifiedRecord:
    engine: str
    title: str
    doi: str
    year: str
    authors: str
    venue: str
    abstract: str
    url: str
    pdf_url: str
    raw_id: str


class ElsevierAPIError(RuntimeError):
    pass


class ElsevierClient:
    base_url = "https://api.elsevier.com"

    def __init__(self, api_key: str, inst_token: str = "", timeout: int = 30):
        self.api_key = api_key.strip()
        self.inst_token = inst_token.strip()
        self.timeout = timeout

    def _headers(self, accept: str) -> Dict[str, str]:
        headers = {
            "X-ELS-APIKey": self.api_key,
            "Accept": accept,
            "User-Agent": "ScholarFetchCLI/1.0",
        }
        if self.inst_token:
            headers["X-ELS-Insttoken"] = self.inst_token
        return headers

    def _request(
        self, path: str, params: Optional[Dict[str, Any]] = None, accept: str = "application/json"
    ) -> Tuple[bytes, Dict[str, str]]:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        url = f"{self.base_url}{path}{query}"
        headers = self._headers(accept)
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read(), dict(resp.headers.items())
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", errors="replace")
            if (
                err.code == 401
                and self.inst_token
                and "Institution Token is not associated with API Key" in body
            ):
                fallback_headers = dict(headers)
                fallback_headers.pop("X-ELS-Insttoken", None)
                fallback_req = urllib.request.Request(url, headers=fallback_headers, method="GET")
                try:
                    with urllib.request.urlopen(fallback_req, timeout=self.timeout) as resp:
                        return resp.read(), dict(resp.headers.items())
                except urllib.error.HTTPError as fallback_err:
                    fallback_body = fallback_err.read().decode("utf-8", errors="replace")
                    raise ElsevierAPIError(
                        f"HTTP {fallback_err.code}: {fallback_body[:400]}"
                    ) from fallback_err
            raise ElsevierAPIError(f"HTTP {err.code}: {body[:400]}") from err
        except urllib.error.URLError as err:
            raise ElsevierAPIError(f"Network error: {err.reason}") from err

    def scopus_search(self, query: str, count: int = 10) -> List[ArticleEntry]:
        raw, _ = self._request(
            "/content/search/scopus",
            params={"query": query, "count": count, "view": "STANDARD"},
            accept="application/json",
        )
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        entries = payload.get("search-results", {}).get("entry", [])
        if isinstance(entries, dict):
            entries = [entries]

        parsed: List[ArticleEntry] = []
        for item in entries:
            title = (item.get("dc:title") or "").strip()
            doi = (item.get("prism:doi") or "").strip()
            date = (item.get("prism:coverDate") or "").strip()
            source = (item.get("prism:publicationName") or "").strip()
            creator = (item.get("dc:creator") or "").strip()
            description = (item.get("dc:description") or "").strip()
            eid = (item.get("eid") or "").strip()
            if not any([title, doi, creator, eid, source]):
                continue
            parsed.append(
                ArticleEntry(
                    title=title or "(no title)",
                    doi=doi,
                    date=date,
                    source=source,
                    creator=creator,
                    description=description,
                    eid=eid,
                )
            )
        return parsed

    def author_search(self, author_query: str, count: int = 10) -> Dict[str, Any]:
        raw, _ = self._request(
            "/content/search/author",
            params={"query": author_query, "count": count},
            accept="application/json",
        )
        return json.loads(raw.decode("utf-8", errors="replace"))

    def abstract_xml_by_doi(self, doi: str) -> str:
        path = f"/content/abstract/doi/{urllib.parse.quote(doi, safe='')}"
        raw, _ = self._request(path, accept="text/xml")
        return raw.decode("utf-8", errors="replace")

    def article_text_by_doi(self, doi: str) -> str:
        path = f"/content/article/doi/{urllib.parse.quote(doi, safe='')}"
        raw, _ = self._request(path, params={"view": "FULL"}, accept="text/plain")
        return raw.decode("utf-8", errors="replace")

    def article_xml_by_doi(self, doi: str) -> str:
        path = f"/content/article/doi/{urllib.parse.quote(doi, safe='')}"
        raw, _ = self._request(path, params={"view": "FULL"}, accept="text/xml")
        return raw.decode("utf-8", errors="replace")

    def article_entitlement_by_doi(self, doi: str) -> str:
        path = f"/content/article/doi/{urllib.parse.quote(doi, safe='')}"
        raw, _ = self._request(path, params={"view": "ENTITLED"}, accept="text/xml")
        return raw.decode("utf-8", errors="replace")

    def references_xml_by_doi(self, doi: str, startref: int = 1, refcount: int = 200) -> str:
        path = f"/content/abstract/doi/{urllib.parse.quote(doi, safe='')}"
        params: Dict[str, Any] = {"view": "REF"}
        # Elsevier REF paging rejects larger refcount values; plain REF often returns all references.
        if 1 <= int(refcount) <= 25:
            params.update({"startref": int(startref), "refcount": int(refcount)})
        raw, _ = self._request(path, params=params, accept="text/xml")
        return raw.decode("utf-8", errors="replace")


class RetroCLI:
    def __init__(self, client: ElsevierClient):
        self.client = client
        self.last_results: List[ArticleEntry] = []
        self.last_unified_results: List[UnifiedRecord] = []
        self.last_author_candidates: List[Dict[str, Any]] = []
        self.last_references: List[Dict[str, str]] = []
        self.last_list_kind: str = ""
        self.pick_sticky: bool = False
        self.pick_nav_stack: List[Dict[str, Any]] = []
        self.pick_root_state: Optional[Dict[str, Any]] = None
        self.pick_status_message: str = ""
        self.pick_selected_index: int = 0
        self.pick_current_action: str = ""
        self.pick_exit_reason: str = ""
        self.pick_path: List[str] = []
        self.saved_records: List[UnifiedRecord] = []
        self.input_history: List[str] = []
        self.entitlement_cache: Dict[str, str] = {}
        self.available_engines = [
            "elsevier",
            "openalex",
            "crossref",
            "arxiv",
            "europepmc",
            "springer",
            "semanticscholar",
        ]
        self.default_engines = ["elsevier", "openalex", "crossref", "arxiv", "europepmc", "springer"]
        self.enabled_engines = self.default_engines[:]
        self.commands = [
            "/config",
            "/search",
            "/author",
            "/papers",
            "/engines",
            "/doi",
            "/abstract",
            "/article",
            "/refs",
            "/ref",
            "/export",
            "/import",
            "/saved",
            "/open",
            "/clear",
            "/help",
            "/quit",
            "/exit",
        ]
        self.auto_pick_after_list: bool = True
        self.springer_meta_key = os.getenv("SPRINGER_META_API_KEY", "").strip()
        self.springer_oa_key = os.getenv("SPRINGER_OPENACCESS_API_KEY", "").strip()
        self.config_path = os.getenv("SCHOLARFETCH_SETTINGS_FILE", ".scholarfetch_settings.json")
        self._load_engine_settings()

    def _load_engine_settings(self) -> None:
        if not os.path.exists(self.config_path):
            return
        try:
            with open(self.config_path, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
            engines = cfg.get("enabled_engines", [])
            if isinstance(engines, list):
                clean = [e for e in engines if e in self.available_engines]
                if clean:
                    self.enabled_engines = clean
        except Exception:
            pass

    def _save_engine_settings(self) -> None:
        payload = {"enabled_engines": self.enabled_engines}
        try:
            with open(self.config_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
        except Exception:
            pass

    @staticmethod
    def _retro(text: str, color: str = ANSI.BRIGHT_GREEN, bold: bool = False) -> str:
        prefix = color + (ANSI.BOLD if bold else "")
        return f"{prefix}{text}{ANSI.RESET}"

    @staticmethod
    def _gradient_banner(text: str) -> str:
        palette = [
            "\033[96m",
            "\033[92m",
            "\033[36m",
            "\033[96m",
            "\033[93m",
        ]
        out_lines: List[str] = []
        for raw_line in text.splitlines():
            if not raw_line.strip():
                out_lines.append("")
                continue
            painted: List[str] = []
            visible = max(1, len(raw_line))
            for idx, ch in enumerate(raw_line):
                if ch == " ":
                    painted.append(ch)
                    continue
                color = palette[min(len(palette) - 1, idx * len(palette) // visible)]
                painted.append(f"{color}{ANSI.BOLD}{ch}{ANSI.RESET}")
            out_lines.append("".join(painted))
        return "\n".join(out_lines)

    @staticmethod
    def _panel(title: str, lines: List[str], color: str = ANSI.BRIGHT_GREEN) -> str:
        width = 92
        top = f"+{'=' * (width - 2)}+"
        title_line = f"| {title[: width - 4].ljust(width - 4)} |"
        body = []
        for line in lines:
            wrapped = textwrap.wrap(line, width=width - 4) or [""]
            for w in wrapped:
                body.append(f"| {w.ljust(width - 4)} |")
        return "\n".join(
            [
                color + top + ANSI.RESET,
                color + title_line + ANSI.RESET,
                color + top + ANSI.RESET,
                *body,
                color + top + ANSI.RESET,
            ]
        )

    @staticmethod
    def _extract_text_from_xml(xml_str: str, max_chars: int = 5000) -> str:
        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError:
            return xml_str[:max_chars]

        chunks: List[str] = []
        for elem in root.iter():
            if elem.text:
                text = re.sub(r"\s+", " ", elem.text.strip())
                if text and len(text) > 1:
                    chunks.append(text)
        merged = "\n".join(chunks)
        return merged[:max_chars]

    @staticmethod
    def _extract_elsevier_body_text(xml_str: str, max_chars: int = 200000) -> str:
        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError:
            return ""

        body = None
        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            if tag == "body":
                body = elem
                break
        if body is None:
            return ""

        chunks: List[str] = []
        for elem in body.iter():
            if elem.tag.endswith("section-title"):
                title = re.sub(r"\s+", " ", "".join(elem.itertext()).strip())
                if title:
                    chunks.append("\n" + title.upper())
            elif elem.tag.endswith(("para", "simple-para")):
                text = re.sub(r"\s+", " ", "".join(elem.itertext()).strip())
                if text and len(text) > 1:
                    chunks.append(text)

        merged = "\n\n".join(chunk.strip() for chunk in chunks if chunk.strip())
        return merged[:max_chars]

    @staticmethod
    def _extract_elsevier_references(xml_str: str, max_refs: int = 200) -> List[str]:
        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError:
            return []
        refs: List[str] = []
        for elem in root.iter():
            if elem.tag.split("}")[-1] != "reference":
                continue
            authors: List[str] = []
            parts: List[str] = []
            for child in elem.iter():
                tag = child.tag.split("}")[-1]
                text = re.sub(r"\s+", " ", "".join(child.itertext()).strip())
                if not text:
                    continue
                if tag == "indexed-name" and text not in authors:
                    authors.append(text)
                elif tag in {"title", "sourcetitle", "publicationyear", "doi"}:
                    if text not in parts:
                        parts.append(text)
            if authors:
                parts.insert(0, ", ".join(authors[:6]))
            if parts:
                refs.append(" | ".join(parts))
            if len(refs) >= max_refs:
                break
        return refs

    @staticmethod
    def _extract_jats_body_text(xml_str: str, max_chars: int = 200000) -> str:
        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError:
            return ""
        body = None
        for elem in root.iter():
            if elem.tag.split("}")[-1] == "body":
                body = elem
                break
        if body is None:
            return ""
        chunks: List[str] = []
        for elem in body.iter():
            tag = elem.tag.split("}")[-1]
            if tag == "title":
                text = re.sub(r"\s+", " ", "".join(elem.itertext()).strip())
                if text:
                    chunks.append("\n" + text.upper())
            elif tag in {"p", "sec"}:
                text = re.sub(r"\s+", " ", "".join(elem.itertext()).strip())
                if text and len(text) > 1:
                    chunks.append(text)
        merged = "\n\n".join(chunk.strip() for chunk in chunks if chunk.strip())
        return merged[:max_chars]

    @staticmethod
    def _extract_jats_references(xml_str: str, max_refs: int = 200) -> List[str]:
        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError:
            return []
        refs: List[str] = []
        for elem in root.iter():
            if elem.tag.split("}")[-1] != "ref":
                continue
            text = re.sub(r"\s+", " ", "".join(elem.itertext()).strip())
            if text:
                refs.append(text)
            if len(refs) >= max_refs:
                break
        return refs

    @staticmethod
    def _fulltext_available_status(status: str) -> bool:
        return (status or "").upper() in {"ENTITLED", "OPEN_ACCESS"}

    @staticmethod
    def _looks_like_elsevier_fulltext_doi(doi: str) -> bool:
        value = (doi or "").strip().lower()
        return value.startswith("10.1016/")

    @staticmethod
    def _make_author_query(author_name: str) -> str:
        author_name = author_name.strip()
        if "(" in author_name and ")" in author_name:
            return author_name

        parts = [p for p in re.split(r"\s+", author_name) if p]
        if not parts:
            return ""
        if len(parts) == 1:
            return f"authlast({parts[0]})"

        first = parts[0]
        last = parts[-1]
        return f"authlast({last}) and authfirst({first})"

    @staticmethod
    def _normalize_person_name(name: str) -> str:
        cleaned = re.sub(r"[^a-zA-ZÀ-ÿ ]+", " ", (name or "").lower())
        return re.sub(r"\s+", " ", cleaned).strip()

    def _openalex_author_candidates(self, author_name: str, per_page: int = 12) -> List[Dict[str, Any]]:
        try:
            url = "https://api.openalex.org/authors?" + urllib.parse.urlencode(
                {"search": author_name, "per-page": str(per_page)}
            )
            data = self._safe_get_json(url)
        except Exception:
            return []
        wanted = self._normalize_person_name(author_name)
        wanted_tokens = set(wanted.split())
        out: List[Dict[str, Any]] = []
        for item in data.get("results", []) or []:
            display = (item.get("display_name") or "").strip()
            norm = self._normalize_person_name(display)
            tokens = set(norm.split())
            overlap = len(wanted_tokens & tokens)
            score = overlap * 10
            if norm == wanted:
                score += 200
            elif norm.startswith(wanted) or wanted.startswith(norm):
                score += 80
            score += min(int(item.get("works_count") or 0), 5000) // 100
            out.append(
                {
                    "engine": "openalex",
                    "author_id": item.get("id", ""),
                    "display_name": display,
                    "orcid": (item.get("orcid") or "").replace("https://orcid.org/", ""),
                    "works_count": int(item.get("works_count") or 0),
                    "cited_by_count": int(item.get("cited_by_count") or 0),
                    "affiliation": (((item.get("last_known_institutions") or [{}])[0]).get("display_name", "")),
                    "score": score,
                }
            )
        out.sort(key=lambda x: (x["score"], x["works_count"], x["cited_by_count"]), reverse=True)
        return out

    def _openalex_works_for_author(self, author_id: str, max_results: int = 80) -> List[UnifiedRecord]:
        out: List[UnifiedRecord] = []
        if not author_id:
            return out
        per_page = 25
        page = 1
        while len(out) < max_results:
            try:
                url = "https://api.openalex.org/works?" + urllib.parse.urlencode(
                    {
                        "filter": f"authorships.author.id:{author_id}",
                        "per-page": str(per_page),
                        "page": str(page),
                        "sort": "publication_year:desc",
                    }
                )
                data = self._safe_get_json(url)
            except Exception:
                break
            rows = data.get("results", []) or []
            if not rows:
                break
            for item in rows:
                authorships = item.get("authorships", []) or []
                authors = ", ".join(
                    a.get("author", {}).get("display_name", "")
                    for a in authorships[:6]
                    if a.get("author", {}).get("display_name")
                )
                out.append(
                    UnifiedRecord(
                        engine="openalex-author",
                        title=(item.get("display_name") or item.get("title") or "").strip(),
                        doi=((item.get("doi") or "").replace("https://doi.org/", "").replace("http://doi.org/", "")),
                        year=str(item.get("publication_year") or ""),
                        authors=authors,
                        venue=((item.get("primary_location") or {}).get("source") or {}).get("display_name", ""),
                        abstract=self._openalex_abstract_to_text(item.get("abstract_inverted_index")),
                        url=item.get("id", ""),
                        pdf_url=((item.get("primary_location") or {}).get("pdf_url") or ""),
                        raw_id=item.get("id", ""),
                    )
                )
                if len(out) >= max_results:
                    break
            if len(rows) < per_page:
                break
            page += 1
        return [r for r in out if r.title]

    @staticmethod
    def _dedupe_records(records: List[UnifiedRecord]) -> List[UnifiedRecord]:
        seen = set()
        out: List[UnifiedRecord] = []
        for r in records:
            key = (r.doi.lower().strip() if r.doi else "") or r.title.lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out

    @staticmethod
    def _extract_author_id(entry: Dict[str, Any]) -> str:
        raw = (entry.get("dc:identifier") or "").strip()
        if ":" in raw:
            return raw.split(":")[-1]
        return raw

    @staticmethod
    def _looks_like_person_name(text: str) -> bool:
        parts = [p for p in re.split(r"\s+", text.strip()) if p]
        if len(parts) < 2 or len(parts) > 4:
            return False
        if not all(re.fullmatch(r"[A-Za-zÀ-ÿ'`.-]+", p) is not None for p in parts):
            return False
        capitals = sum(1 for p in parts if p[:1].isupper())
        return capitals >= max(2, len(parts) - 1)

    @staticmethod
    def _is_advanced_query(text: str) -> bool:
        markers = ["TITLE-ABS-KEY(", "ALL(", "AU-ID(", "AUTH(", "DOI(", "AND ", " OR "]
        upper = text.upper()
        return any(m in upper for m in markers)

    def _author_query_variants(self, author_name: str) -> List[str]:
        author_name = author_name.strip()
        parts = [p for p in re.split(r"\s+", author_name) if p]
        if not parts:
            return []
        if len(parts) == 1:
            return [f"authlast({parts[0]})"]

        first = parts[0]
        last = parts[-1]
        first_initial = first[:1]
        variants = [
            f"authlast({last}) and authfirst({first})",
            f"authlast({last}) and authfirst({first_initial})",
            f"authlast({last})",
            author_name,
        ]
        out: List[str] = []
        for v in variants:
            if v and v not in out:
                out.append(v)
        return out

    def _resolve_author_candidates(self, author_name: str, max_candidates: int = 5) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        for q in self._author_query_variants(author_name):
            try:
                payload = self.client.author_search(q, count=10)
            except ElsevierAPIError:
                continue
            entries = payload.get("search-results", {}).get("entry", [])
            if isinstance(entries, dict):
                entries = [entries]
            for e in entries:
                author_id = self._extract_author_id(e)
                if not author_id:
                    continue
                indexed_name = e.get("preferred-name", {}).get("indexed-name", "")
                docs = int(e.get("document-count", "0") or 0)
                cand = {
                    "author_id": author_id,
                    "indexed_name": indexed_name,
                    "document_count": docs,
                    "raw": e,
                }
                if author_id not in {c["author_id"] for c in candidates}:
                    candidates.append(cand)
            if candidates:
                break
        if not candidates:
            fallback_id = self._resolve_author_id_from_abstracts(author_name)
            if fallback_id:
                candidates.append(
                    {
                        "author_id": fallback_id,
                        "indexed_name": author_name,
                        "document_count": 0,
                        "raw": {
                            "preferred-name": {"indexed-name": f"{author_name} (fallback)"},
                            "affiliation-current": {},
                        },
                    }
                )
        candidates.sort(key=lambda c: c["document_count"], reverse=True)
        return candidates[:max_candidates]

    def _resolve_author_id_from_abstracts(self, author_name: str) -> str:
        try:
            seed_results = self.client.scopus_search(author_name, count=12)
        except ElsevierAPIError:
            return ""
        surname = (author_name.strip().split()[-1] if author_name.strip() else "").lower()
        for item in seed_results:
            creator = (item.creator or "").lower()
            if surname and surname not in creator:
                continue
            if not item.doi:
                continue
            try:
                xml = self.client.abstract_xml_by_doi(item.doi)
            except ElsevierAPIError:
                continue
            ids = re.findall(r"/author/author_id/(\\d+)", xml)
            if ids:
                return ids[0]
        return ""

    def _build_search_queries(self, arg: str) -> List[str]:
        q = arg.strip()
        out: List[str] = []
        if not q:
            return out
        out.append(q)
        if not self._is_advanced_query(q):
            quoted = q.replace('"', "")
            out.append(f'TITLE-ABS-KEY("{quoted}")')
            out.append(f'ALL("{quoted}")')
            if self._looks_like_person_name(q):
                candidates = self._resolve_author_candidates(q, max_candidates=1)
                if candidates:
                    out.insert(0, f"AU-ID({candidates[0]['author_id']})")
        deduped: List[str] = []
        for item in out:
            if item not in deduped:
                deduped.append(item)
        return deduped

    @staticmethod
    def _strip_html(text: str) -> str:
        text = re.sub(r"<[^>]+>", " ", text or "")
        text = html.unescape(text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _openalex_abstract_to_text(inv: Any) -> str:
        if not isinstance(inv, dict):
            return ""
        items: List[Tuple[int, str]] = []
        for word, positions in inv.items():
            if not isinstance(positions, list):
                continue
            for p in positions:
                if isinstance(p, int):
                    items.append((p, word))
        if not items:
            return ""
        return " ".join(w for _, w in sorted(items, key=lambda x: x[0]))

    @staticmethod
    def _safe_get_json(url: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        req = urllib.request.Request(url, headers=headers or {"User-Agent": "ScholarFetchCLI/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    @staticmethod
    def _safe_get_text(url: str, headers: Optional[Dict[str, str]] = None) -> str:
        req = urllib.request.Request(url, headers=headers or {"User-Agent": "ScholarFetchCLI/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")

    @staticmethod
    def _looks_like_doi(value: str) -> bool:
        return re.fullmatch(r"10\.\d{4,9}/\S+", value.strip(), flags=re.IGNORECASE) is not None

    @staticmethod
    def _springer_abstract_to_text(val: Any) -> str:
        if isinstance(val, dict):
            parts = []
            for k in ("h1", "p"):
                v = val.get(k)
                if isinstance(v, str) and v.strip():
                    parts.append(v.strip())
            return " ".join(parts).strip()
        if isinstance(val, str):
            return val.strip()
        return ""

    @staticmethod
    def _extract_xml_fragment(xml_text: str, tag: str) -> str:
        pattern = rf"<{tag}\\b[^>]*>(.*?)</{tag}>"
        m = re.search(pattern, xml_text, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            return ""
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(m.group(1)))).strip()

    def _springer_record_to_unified(self, record: Dict[str, Any], source: str) -> UnifiedRecord:
        creators = record.get("creators", []) or []
        authors = ", ".join(
            (c.get("creator") or "").strip() for c in creators[:6] if isinstance(c, dict) and c.get("creator")
        )
        urls = record.get("url", []) or []
        best_url = ""
        pdf_url = ""
        for u in urls:
            if not isinstance(u, dict):
                continue
            val = (u.get("value") or "").strip()
            fmt = (u.get("format") or "").lower()
            if not best_url and val:
                best_url = val
            if "pdf" in fmt and val:
                pdf_url = val
        return UnifiedRecord(
            engine=source,
            title=(record.get("title") or "").strip(),
            doi=(record.get("doi") or "").strip(),
            year=((record.get("publicationDate") or "")[:4]),
            authors=authors,
            venue=(record.get("publicationName") or "").strip(),
            abstract=self._springer_abstract_to_text(record.get("abstract")),
            url=best_url,
            pdf_url=pdf_url,
            raw_id=(record.get("identifier") or record.get("doi") or "").strip(),
        )

    def _search_elsevier(self, query: str, limit: int = 5) -> List[UnifiedRecord]:
        out: List[UnifiedRecord] = []
        try:
            rows = self.client.scopus_search(query, count=limit)
        except ElsevierAPIError:
            return out
        for r in rows:
            out.append(
                UnifiedRecord(
                    engine="elsevier",
                    title=r.title,
                    doi=r.doi,
                    year=(r.date[:4] if r.date else ""),
                    authors=r.creator,
                    venue=r.source,
                    abstract=r.description,
                    url="",
                    pdf_url="",
                    raw_id=r.eid,
                )
            )
        return out

    def _search_openalex(self, query: str, limit: int = 5) -> List[UnifiedRecord]:
        out: List[UnifiedRecord] = []
        try:
            url = "https://api.openalex.org/works?" + urllib.parse.urlencode({"search": query, "per-page": str(limit)})
            data = self._safe_get_json(url)
        except Exception:
            return out
        for item in data.get("results", [])[:limit]:
            authorships = item.get("authorships", []) or []
            authors = ", ".join(
                a.get("author", {}).get("display_name", "")
                for a in authorships[:4]
                if a.get("author", {}).get("display_name")
            )
            out.append(
                UnifiedRecord(
                    engine="openalex",
                    title=(item.get("display_name") or item.get("title") or "").strip(),
                    doi=((item.get("doi") or "").replace("https://doi.org/", "").replace("http://doi.org/", "")),
                    year=str(item.get("publication_year") or ""),
                    authors=authors,
                    venue=((item.get("primary_location") or {}).get("source") or {}).get("display_name", ""),
                    abstract=self._openalex_abstract_to_text(item.get("abstract_inverted_index")),
                    url=item.get("id", ""),
                    pdf_url=((item.get("primary_location") or {}).get("pdf_url") or ""),
                    raw_id=item.get("id", ""),
                )
            )
        return [x for x in out if x.title]

    def _search_crossref(self, query: str, limit: int = 5) -> List[UnifiedRecord]:
        out: List[UnifiedRecord] = []
        try:
            url = "https://api.crossref.org/works?" + urllib.parse.urlencode({"query": query, "rows": str(limit)})
            data = self._safe_get_json(url)
        except Exception:
            return out
        for item in (data.get("message", {}).get("items", []) or [])[:limit]:
            titles = item.get("title") or []
            title = titles[0].strip() if titles else ""
            doi = (item.get("DOI") or "").strip()
            author_list = item.get("author", []) or []
            authors = ", ".join(
                f"{a.get('family', '')} {a.get('given', '')}".strip() for a in author_list[:4] if a
            )
            year = ""
            for block in ("published-print", "published-online", "issued"):
                date_parts = item.get(block, {}).get("date-parts", [])
                if date_parts and date_parts[0]:
                    year = str(date_parts[0][0])
                    break
            venue = ""
            container = item.get("container-title") or []
            if container:
                venue = container[0]
            link = ""
            links = item.get("link", []) or []
            if links:
                link = links[0].get("URL", "")
            out.append(
                UnifiedRecord(
                    engine="crossref",
                    title=title,
                    doi=doi,
                    year=year,
                    authors=authors,
                    venue=venue,
                    abstract=self._strip_html(item.get("abstract", "")),
                    url=f"https://doi.org/{doi}" if doi else link,
                    pdf_url=link,
                    raw_id=doi,
                )
            )
        return [x for x in out if x.title]

    def _search_arxiv(self, query: str, limit: int = 5) -> List[UnifiedRecord]:
        out: List[UnifiedRecord] = []
        params = {"search_query": f"all:{query}", "start": "0", "max_results": str(limit)}
        url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
        try:
            xml = self._safe_get_text(url)
            root = ET.fromstring(xml)
        except Exception:
            return out
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("a:entry", ns):
            title = (entry.findtext("a:title", "", ns) or "").strip()
            summary = (entry.findtext("a:summary", "", ns) or "").strip()
            published = (entry.findtext("a:published", "", ns) or "")
            year = published[:4] if len(published) >= 4 else ""
            eid = (entry.findtext("a:id", "", ns) or "").strip()
            links = entry.findall("a:link", ns)
            pdf = ""
            for l in links:
                if l.attrib.get("title") == "pdf":
                    pdf = l.attrib.get("href", "")
            authors = ", ".join(
                (a.findtext("a:name", "", ns) or "").strip() for a in entry.findall("a:author", ns)[:4]
            )
            out.append(
                UnifiedRecord(
                    engine="arxiv",
                    title=title,
                    doi="",
                    year=year,
                    authors=authors,
                    venue="arXiv",
                    abstract=summary,
                    url=eid,
                    pdf_url=pdf,
                    raw_id=eid,
                )
            )
        return [x for x in out if x.title]

    def _search_europepmc(self, query: str, limit: int = 5) -> List[UnifiedRecord]:
        out: List[UnifiedRecord] = []
        params = {"query": query, "format": "json", "resultType": "core", "pageSize": str(limit)}
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(params)
        try:
            data = self._safe_get_json(url)
        except Exception:
            return out
        for item in data.get("resultList", {}).get("result", [])[:limit]:
            title = (item.get("title") or "").strip()
            doi = (item.get("doi") or "").strip()
            authors = (item.get("authorString") or "").strip()
            year = str(item.get("pubYear") or "")
            venue = (item.get("journalTitle") or "").strip()
            abstract = (item.get("abstractText") or "").strip()
            pmid = (item.get("pmid") or "").strip()
            pmcid = (item.get("pmcid") or "").strip()
            link = ""
            if pmcid:
                link = f"https://europepmc.org/article/PMC/{pmcid}"
            elif pmid:
                link = f"https://europepmc.org/article/MED/{pmid}"
            out.append(
                UnifiedRecord(
                    engine="europepmc",
                    title=title,
                    doi=doi,
                    year=year,
                    authors=authors,
                    venue=venue,
                    abstract=abstract,
                    url=link,
                    pdf_url="",
                    raw_id=pmid or pmcid or doi,
                )
            )
        return [x for x in out if x.title]

    def _lookup_europepmc_doi(self, doi: str) -> List[UnifiedRecord]:
        out: List[UnifiedRecord] = []
        query = f'DOI:"{doi}"'
        params = {"query": query, "format": "json", "resultType": "core", "pageSize": "10"}
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(params)
        try:
            data = self._safe_get_json(url)
        except Exception:
            return out
        for item in data.get("resultList", {}).get("result", []) or []:
            title = (item.get("title") or "").strip()
            authors = (item.get("authorString") or "").strip()
            year = str(item.get("pubYear") or "")
            venue = (item.get("journalTitle") or "").strip()
            abstract = (item.get("abstractText") or "").strip()
            pmid = (item.get("pmid") or "").strip()
            pmcid = (item.get("pmcid") or "").strip()
            link = ""
            if pmcid:
                link = f"https://europepmc.org/article/PMC/{pmcid}"
            elif pmid:
                link = f"https://europepmc.org/article/MED/{pmid}"
            out.append(
                UnifiedRecord(
                    engine="europepmc",
                    title=title,
                    doi=(item.get("doi") or doi),
                    year=year,
                    authors=authors,
                    venue=venue,
                    abstract=abstract,
                    url=link,
                    pdf_url="",
                    raw_id=pmcid or pmid or doi,
                )
            )
        return [x for x in out if x.title]

    def _search_springer(self, query: str, limit: int = 5) -> List[UnifiedRecord]:
        out: List[UnifiedRecord] = []
        # With current Springer keys, keyword/title search is premium-restricted.
        # DOI lookups are available and reliable.
        if not self._looks_like_doi(query):
            return out
        out.extend(self._lookup_springer_doi(query))
        return out

    def _lookup_elsevier_doi(self, doi: str) -> List[UnifiedRecord]:
        try:
            xml = self.client.abstract_xml_by_doi(doi)
        except ElsevierAPIError:
            return []
        text = self._extract_text_from_xml(xml, max_chars=5000)
        title_match = re.search(r"<dc:title>(.*?)</dc:title>", xml)
        title = self._strip_html(title_match.group(1)) if title_match else doi
        return [
            UnifiedRecord(
                engine="elsevier",
                title=title or doi,
                doi=doi,
                year="",
                authors="",
                venue="",
                abstract=text if len(text) > 160 else "",
                url="",
                pdf_url="",
                raw_id=doi,
            )
        ]

    def _elsevier_article_entitlement(self, doi: str) -> str:
        key = (doi or "").strip().lower()
        if not key:
            return ""
        if key in self.entitlement_cache:
            return self.entitlement_cache[key]
        status = "UNKNOWN"
        for attempt in range(3):
            try:
                xml = self.client.article_entitlement_by_doi(doi)
                m = re.search(r"<status>(.*?)</status>", xml)
                status = (m.group(1).strip().upper() if m else "UNKNOWN")
                if status != "UNKNOWN":
                    break
            except ElsevierAPIError as err:
                message = str(err)
                if "HTTP 404" in message:
                    status = "UNAVAILABLE"
                    break
                if attempt < 2:
                    time.sleep(0.35 * (attempt + 1))
                    continue
                status = "UNKNOWN"
        if status != "UNKNOWN":
            self.entitlement_cache[key] = status
        return status

    def _elsevier_full_article_text(self, doi: str, max_chars: int = 200000) -> Tuple[str, str]:
        status = self._elsevier_article_entitlement(doi)
        if status not in {"", "UNKNOWN"} and not self._fulltext_available_status(status):
            return "", ""
        try:
            xml = self.client.article_xml_by_doi(doi)
        except ElsevierAPIError:
            return "", ""

        text = self._extract_elsevier_body_text(xml, max_chars=max_chars)
        title_match = re.search(r"<dc:title>(.*?)</dc:title>", xml)
        title = self._strip_html(title_match.group(1)) if title_match else doi

        if len(text.strip()) < 1200:
            return title, ""
        return title, text

    def _prefetch_fulltext_status(self, records: List[UnifiedRecord], limit: int = 20) -> None:
        checked = 0
        for rec in records:
            if checked >= limit:
                break
            doi = (rec.doi or "").strip()
            if not doi or not self._looks_like_elsevier_fulltext_doi(doi):
                continue
            key = doi.lower()
            if key in self.entitlement_cache:
                continue
            self._elsevier_article_entitlement(doi)
            checked += 1

    @staticmethod
    def _pdf_text_from_url(pdf_url: str, max_chars: int = 200000) -> str:
        if not pdf_url:
            return ""
        try:
            from pypdf import PdfReader
        except Exception:
            return ""
        try:
            with urllib.request.urlopen(pdf_url, timeout=45) as resp:
                data = resp.read()
        except Exception:
            return ""
        tmp_path = "/tmp/scholarfetch_pdf_tmp.pdf"
        try:
            with open(tmp_path, "wb") as fh:
                fh.write(data)
            reader = PdfReader(tmp_path)
            chunks = []
            for page in reader.pages:
                text = page.extract_text() or ""
                if text:
                    chunks.append(text)
                if sum(len(x) for x in chunks) >= max_chars:
                    break
            merged = "\n\n".join(chunks)
            return re.sub(r"\s+\n", "\n", merged)[:max_chars]
        except Exception:
            return ""

    def _springer_oa_fulltext_by_doi(self, doi: str, max_chars: int = 200000) -> Tuple[str, str]:
        if not self.springer_oa_key or not doi:
            return "", ""
        query = urllib.parse.urlencode({"q": f"doi:{doi}", "api_key": self.springer_oa_key})
        try:
            xml = self._safe_get_text("https://api.springernature.com/openaccess/jats?" + query)
        except Exception:
            return "", ""
        text = self._extract_jats_body_text(xml, max_chars=max_chars)
        if len(text.strip()) < 1200:
            return "", ""
        title = self._extract_xml_fragment(xml, "article-title") or doi
        return title, text

    def _springer_oa_references_by_doi(self, doi: str, max_refs: int = 200) -> List[str]:
        if not self.springer_oa_key or not doi:
            return []
        query = urllib.parse.urlencode({"q": f"doi:{doi}", "api_key": self.springer_oa_key})
        try:
            xml = self._safe_get_text("https://api.springernature.com/openaccess/jats?" + query)
        except Exception:
            return []
        return self._extract_jats_references(xml, max_refs=max_refs)

    def _arxiv_fulltext_by_record(self, rec: Optional[UnifiedRecord], max_chars: int = 200000) -> Tuple[str, str]:
        if not rec:
            return "", ""
        pdf_url = (rec.pdf_url or "").strip()
        if not pdf_url and rec.engine == "arxiv":
            raw = (rec.raw_id or rec.url or "").strip().rstrip("/")
            if raw:
                pdf_url = raw.replace("/abs/", "/pdf/") + ".pdf"
        if not pdf_url and "arxiv.org/abs/" in (rec.url or ""):
            pdf_url = rec.url.replace("/abs/", "/pdf/") + ".pdf"
        text = self._pdf_text_from_url(pdf_url, max_chars=max_chars)
        if len(text.strip()) < 1200:
            return "", ""
        return rec.title or "arXiv paper", text

    def _arxiv_fulltext_by_doi(self, doi: str, max_chars: int = 200000) -> Tuple[str, str]:
        value = (doi or "").strip()
        m = re.fullmatch(r"10\.48550/arXiv\.(.+)", value, flags=re.IGNORECASE)
        if not m:
            return "", ""
        arxiv_id = m.group(1)
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        text = self._pdf_text_from_url(pdf_url, max_chars=max_chars)
        if len(text.strip()) < 1200:
            return "", ""
        return f"arXiv:{arxiv_id}", text

    def _europepmc_fulltext_by_doi(self, doi: str, max_chars: int = 200000) -> Tuple[str, str]:
        rows = self._lookup_europepmc_doi(doi)
        for rec in rows:
            raw = (rec.raw_id or "").strip()
            if not raw.upper().startswith("PMC"):
                continue
            url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{raw}/fullTextXML"
            try:
                xml = self._safe_get_text(url)
            except Exception:
                continue
            text = self._extract_jats_body_text(xml, max_chars=max_chars)
            if len(text.strip()) < 1200:
                continue
            return rec.title or doi, text
        return "", ""

    def _europepmc_references_by_doi(self, doi: str, max_refs: int = 200) -> List[str]:
        rows = self._lookup_europepmc_doi(doi)
        for rec in rows:
            raw = (rec.raw_id or "").strip()
            if not raw.upper().startswith("PMC"):
                continue
            url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{raw}/fullTextXML"
            try:
                xml = self._safe_get_text(url)
            except Exception:
                continue
            refs = self._extract_jats_references(xml, max_refs=max_refs)
            if refs:
                return refs
        return []

    def _crossref_references_by_doi(self, doi: str, max_refs: int = 200) -> List[str]:
        try:
            data = self._safe_get_json("https://api.crossref.org/works/" + urllib.parse.quote(doi, safe=""))
        except Exception:
            return []
        refs: List[str] = []
        for item in (data.get("message", {}).get("reference", []) or [])[:max_refs]:
            if not isinstance(item, dict):
                continue
            parts: List[str] = []
            author = (item.get("author") or "").strip()
            year = str(item.get("year") or "").strip()
            article_title = (item.get("article-title") or item.get("volume-title") or item.get("series-title") or "").strip()
            journal = (item.get("journal-title") or item.get("container-title") or "").strip()
            doi_ref = (item.get("DOI") or item.get("doi") or "").strip()
            for piece in (author, year, article_title, journal):
                if piece:
                    parts.append(piece)
            if doi_ref:
                parts.append(f"doi:{doi_ref}")
            if parts:
                refs.append(" | ".join(parts))
        return refs

    @staticmethod
    def _extract_doi_from_text(text: str) -> str:
        m = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b", text or "", flags=re.IGNORECASE)
        return m.group(0).rstrip(".,);]") if m else ""

    def _resolve_fulltext(
        self, doi: str, seed_record: Optional[UnifiedRecord] = None, max_chars: int = 200000
    ) -> Dict[str, Any]:
        rows = self._parallel_doi_lookup(doi) if doi else []
        preferred_rows: List[UnifiedRecord] = []
        if seed_record:
            preferred_rows.append(seed_record)
        preferred_rows.extend(rows)

        status = self._elsevier_article_entitlement(doi) if doi else ""
        title, text = self._elsevier_full_article_text(doi, max_chars=max_chars) if doi else ("", "")
        if text:
            return {
                "found": True,
                "engine": "elsevier",
                "source": "elsevier_article_xml",
                "doi": doi,
                "title": title or doi,
                "text": text,
                "elsevier_fulltext_status": status or "UNKNOWN",
                "results": rows,
            }

        title, text = self._springer_oa_fulltext_by_doi(doi, max_chars=max_chars) if doi else ("", "")
        if text:
            return {
                "found": True,
                "engine": "springer-oa",
                "source": "springer_openaccess_jats",
                "doi": doi,
                "title": title or doi,
                "text": text,
                "elsevier_fulltext_status": status or "UNKNOWN",
                "results": rows,
            }

        title, text = self._europepmc_fulltext_by_doi(doi, max_chars=max_chars) if doi else ("", "")
        if text:
            return {
                "found": True,
                "engine": "europepmc",
                "source": "europepmc_fulltextxml",
                "doi": doi,
                "title": title or doi,
                "text": text,
                "elsevier_fulltext_status": status or "UNKNOWN",
                "results": rows,
            }

        title, text = self._arxiv_fulltext_by_doi(doi, max_chars=max_chars) if doi else ("", "")
        if text:
            return {
                "found": True,
                "engine": "arxiv",
                "source": "arxiv_doi_pdf",
                "doi": doi,
                "title": title or doi,
                "text": text,
                "elsevier_fulltext_status": status or "UNKNOWN",
                "results": rows,
            }

        arxiv_rec = next((r for r in preferred_rows if r.engine == "arxiv"), None)
        title, text = self._arxiv_fulltext_by_record(arxiv_rec, max_chars=max_chars)
        if text:
            return {
                "found": True,
                "engine": "arxiv",
                "source": "arxiv_pdf",
                "doi": doi,
                "title": title or doi,
                "text": text,
                "elsevier_fulltext_status": status or "UNKNOWN",
                "results": rows,
            }

        pdf_rec = next((r for r in preferred_rows if (r.pdf_url or "").strip()), None)
        if pdf_rec:
            text = self._pdf_text_from_url(pdf_rec.pdf_url, max_chars=max_chars)
            if len(text.strip()) >= 1200:
                return {
                    "found": True,
                    "engine": pdf_rec.engine,
                    "source": "generic_pdf_url",
                    "doi": doi,
                    "title": pdf_rec.title or doi,
                    "text": text,
                    "elsevier_fulltext_status": status or "UNKNOWN",
                    "results": rows,
                }

        return {
            "found": False,
            "engine": "",
            "source": "",
            "doi": doi,
            "title": seed_record.title if seed_record and seed_record.title else doi,
            "text": "",
            "elsevier_fulltext_status": status or "UNKNOWN",
            "results": rows,
        }

    def _resolve_references(
        self, doi: str, seed_record: Optional[UnifiedRecord] = None, max_refs: int = 200
    ) -> Dict[str, Any]:
        rows = self._parallel_doi_lookup(doi) if doi else []
        refs = self._crossref_references_by_doi(doi, max_refs=max_refs) if doi else []
        source = "crossref"

        if doi:
            try:
                xml = self.client.references_xml_by_doi(doi, startref=1, refcount=max_refs)
                elsevier_refs = self._extract_elsevier_references(xml, max_refs=max_refs)
                if elsevier_refs:
                    refs = elsevier_refs
                    source = "elsevier_ref_view"
            except ElsevierAPIError:
                pass

        springer_refs = self._springer_oa_references_by_doi(doi, max_refs=max_refs) if doi else []
        if springer_refs:
            refs = springer_refs
            source = "springer_openaccess_jats"

        epmc_refs = self._europepmc_references_by_doi(doi, max_refs=max_refs) if doi else []
        if epmc_refs:
            refs = epmc_refs
            source = "europepmc_fulltextxml"

        parsed = [{"index": str(i), "text": ref, "doi": self._extract_doi_from_text(ref)} for i, ref in enumerate(refs, start=1)]
        return {
            "doi": doi,
            "title": seed_record.title if seed_record and seed_record.title else doi,
            "source": source if refs else "",
            "count": len(parsed),
            "references": parsed,
            "results": rows,
        }

    def _reference_preview_record(self, ref: Dict[str, str]) -> Optional[UnifiedRecord]:
        doi = (ref.get("doi") or "").strip()
        rows: List[UnifiedRecord]
        if doi:
            rows = self._parallel_doi_lookup(doi)
        else:
            text = (ref.get("text") or "").strip()
            rows = self._parallel_search(text[:220], limit_per_engine=2) if text else []
        rows = self._dedupe_records(rows)
        if not rows:
            return None
        self._prefetch_fulltext_status(rows, limit=min(6, len(rows)))
        rows.sort(
            key=lambda r: (
                self._fulltext_rank(r),
                0 if (r.abstract or "").strip() else 1,
                -self._record_year_int(r),
                (r.title or "").lower(),
            )
        )
        return rows[0] if rows else None

    def _reference_preview_from_entry(self, ref: Dict[str, Any]) -> Optional[UnifiedRecord]:
        cached = ref.get("preview_record")
        if isinstance(cached, dict):
            try:
                return UnifiedRecord(**cached)
            except Exception:
                pass
        preview = self._reference_preview_record(ref)
        if preview:
            ref["preview_record"] = asdict(preview)
        return preview

    @staticmethod
    def _cached_reference_preview(ref: Dict[str, Any]) -> Optional[UnifiedRecord]:
        cached = ref.get("preview_record")
        if isinstance(cached, dict):
            try:
                return UnifiedRecord(**cached)
            except Exception:
                return None
        return None

    def _enrich_references_for_picker(self, refs: List[Dict[str, str]], max_items: int = 30) -> Tuple[List[Dict[str, str]], bool]:
        enriched = [dict(ref) for ref in refs]
        limit = min(max_items, len(enriched))
        interrupted = False
        progress_started = False
        i = -1

        def render_progress(done: int, total: int, current: str = "", interrupted_now: bool = False) -> None:
            nonlocal progress_started
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                return
            width = max(30, shutil.get_terminal_size((100, 30)).columns - 2)
            bar_w = max(10, min(28, width // 4))
            filled = 0 if total <= 0 else int((done / max(1, total)) * bar_w)
            bar = "[" + ("=" * filled) + (" " * (bar_w - filled)) + "]"
            status = "interrupted; keeping partial results" if interrupted_now else "Ctrl-C keeps partial results"
            line1 = f"Resolving reference previews {bar} {done}/{total}  {status}"
            current_label = self._short_label(current or "waiting for first resolvable reference", max_len=max(20, width - 10))
            line2 = f"Current: {current_label}"
            if progress_started:
                sys.stdout.write("\033[2F")
            sys.stdout.write("\033[2K" + ANSI.DIM + ANSI.BRIGHT_BLACK + line1[:width] + ANSI.RESET + "\n")
            sys.stdout.write("\033[2K" + ANSI.DIM + ANSI.BRIGHT_BLACK + line2[:width] + ANSI.RESET + "\n")
            sys.stdout.flush()
            progress_started = True

        if limit > 0:
            render_progress(0, limit)
        try:
            for i in range(limit):
                ref = enriched[i]
                preview = self._reference_preview_record(ref)
                if not preview:
                    ref["preview_title"] = ref.get("text", "")
                    ref["preview_engine"] = ""
                    ref["preview_doi"] = ref.get("doi", "")
                    ref["abstract_status"] = "unknown"
                    ref["fulltext_status"] = "unknown"
                else:
                    ref["preview_title"] = preview.title or ref.get("text", "")
                    ref["preview_engine"] = preview.engine or ""
                    ref["preview_doi"] = preview.doi or ref.get("doi", "")
                    ref["abstract_status"] = "yes" if (preview.abstract or "").strip() else "no"
                    ref["fulltext_status"] = self._record_fulltext_status(preview)
                    ref["preview_year"] = preview.year or ""
                    ref["preview_record"] = asdict(preview)
                render_progress(i + 1, limit, ref.get("preview_title") or ref.get("text") or "")
        except KeyboardInterrupt:
            interrupted = True
            current_ref = ""
            if enriched and i >= 0:
                current_ref = enriched[min(i, len(enriched) - 1)].get("preview_title") or enriched[min(i, len(enriched) - 1)].get("text") or ""
            render_progress(min(limit, max(0, i + 1)), limit, current_ref, interrupted_now=True)
        return enriched, interrupted

    def _lookup_openalex_doi(self, doi: str) -> List[UnifiedRecord]:
        try:
            data = self._safe_get_json(
                "https://api.openalex.org/works/https://doi.org/" + urllib.parse.quote(doi, safe="")
            )
        except Exception:
            return []
        authorships = data.get("authorships", []) or []
        authors = ", ".join(
            a.get("author", {}).get("display_name", "") for a in authorships[:5] if a.get("author")
        )
        return [
            UnifiedRecord(
                engine="openalex",
                title=(data.get("display_name") or data.get("title") or doi).strip(),
                doi=((data.get("doi") or "").replace("https://doi.org/", "").replace("http://doi.org/", "")),
                year=str(data.get("publication_year") or ""),
                authors=authors,
                venue=((data.get("primary_location") or {}).get("source") or {}).get("display_name", ""),
                abstract=self._openalex_abstract_to_text(data.get("abstract_inverted_index")),
                url=data.get("id", ""),
                pdf_url=((data.get("primary_location") or {}).get("pdf_url") or ""),
                raw_id=data.get("id", ""),
            )
        ]

    def _lookup_crossref_doi(self, doi: str) -> List[UnifiedRecord]:
        try:
            data = self._safe_get_json("https://api.crossref.org/works/" + urllib.parse.quote(doi, safe=""))
        except Exception:
            return []
        item = data.get("message", {})
        title = (item.get("title") or [doi])[0]
        authors = ", ".join(
            f"{a.get('family', '')} {a.get('given', '')}".strip() for a in (item.get("author", []) or [])[:5]
        )
        year = ""
        for block in ("published-print", "published-online", "issued"):
            date_parts = item.get(block, {}).get("date-parts", [])
            if date_parts and date_parts[0]:
                year = str(date_parts[0][0])
                break
        links = item.get("link", []) or []
        return [
            UnifiedRecord(
                engine="crossref",
                title=title,
                doi=(item.get("DOI") or doi),
                year=year,
                authors=authors,
                venue=((item.get("container-title") or [""])[0]),
                abstract=self._strip_html(item.get("abstract", "")),
                url=f"https://doi.org/{item.get('DOI') or doi}",
                pdf_url=(links[0].get("URL", "") if links else ""),
                raw_id=(item.get("DOI") or doi),
            )
        ]

    def _lookup_semanticscholar_doi(self, doi: str) -> List[UnifiedRecord]:
        params = {"fields": "title,abstract,year,authors,externalIds,url,openAccessPdf,journal"}
        url = (
            "https://api.semanticscholar.org/graph/v1/paper/DOI:"
            + urllib.parse.quote(doi, safe="")
            + "?"
            + urllib.parse.urlencode(params)
        )
        try:
            data = self._safe_get_json(url)
        except Exception:
            return []
        authors = ", ".join((a.get("name") or "") for a in (data.get("authors", []) or [])[:5])
        pdf_url = (data.get("openAccessPdf") or {}).get("url", "")
        journal = (data.get("journal") or {}).get("name", "")
        return [
            UnifiedRecord(
                engine="semanticscholar",
                title=(data.get("title") or doi),
                doi=((data.get("externalIds") or {}).get("DOI") or doi),
                year=str(data.get("year") or ""),
                authors=authors,
                venue=journal,
                abstract=(data.get("abstract") or ""),
                url=(data.get("url") or ""),
                pdf_url=pdf_url,
                raw_id=(data.get("paperId") or doi),
            )
        ]

    def _lookup_arxiv_by_doi(self, doi: str) -> List[UnifiedRecord]:
        params = {"search_query": f"all:{doi}", "start": "0", "max_results": "3"}
        url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
        try:
            xml = self._safe_get_text(url)
            root = ET.fromstring(xml)
        except Exception:
            return []
        ns = {"a": "http://www.w3.org/2005/Atom"}
        out: List[UnifiedRecord] = []
        doi_l = doi.lower().strip()
        for entry in root.findall("a:entry", ns):
            title = (entry.findtext("a:title", "", ns) or "").strip()
            summary = (entry.findtext("a:summary", "", ns) or "").strip()
            eid = (entry.findtext("a:id", "", ns) or "").strip()
            published = (entry.findtext("a:published", "", ns) or "")
            year = published[:4] if len(published) >= 4 else ""
            blob = " ".join([title.lower(), summary.lower(), eid.lower()])
            if doi_l not in blob:
                continue
            out.append(
                UnifiedRecord(
                    engine="arxiv",
                    title=title,
                    doi="",
                    year=year,
                    authors="",
                    venue="arXiv",
                    abstract=summary,
                    url=eid,
                    pdf_url="",
                    raw_id=eid,
                )
            )
        return out

    def _lookup_springer_doi(self, doi: str) -> List[UnifiedRecord]:
        out: List[UnifiedRecord] = []
        q = f"doi:{doi}"

        if self.springer_meta_key:
            try:
                url = "https://api.springernature.com/meta/v2/json?" + urllib.parse.urlencode(
                    {"q": q, "api_key": self.springer_meta_key}
                )
                data = self._safe_get_json(url)
                for rec in data.get("records", []) or []:
                    if isinstance(rec, dict):
                        out.append(self._springer_record_to_unified(rec, "springer-meta"))
            except Exception:
                pass

        if self.springer_oa_key:
            try:
                url = "https://api.springernature.com/openaccess/json?" + urllib.parse.urlencode(
                    {"q": q, "api_key": self.springer_oa_key}
                )
                data = self._safe_get_json(url)
                for rec in data.get("records", []) or []:
                    if isinstance(rec, dict):
                        out.append(self._springer_record_to_unified(rec, "springer-oa"))
            except Exception:
                pass

            # JATS fallback can expose abstract text even when JSON abstract is sparse.
            try:
                url = "https://api.springernature.com/openaccess/jats?" + urllib.parse.urlencode(
                    {"q": q, "api_key": self.springer_oa_key}
                )
                xml = self._safe_get_text(url)
                jats_abs = self._extract_xml_fragment(xml, "abstract")
                if jats_abs:
                    for rec in out:
                        if rec.engine.startswith("springer") and not rec.abstract:
                            rec.abstract = jats_abs
            except Exception:
                pass

        return [r for r in out if r.title]

    def _parallel_search(self, query: str, limit_per_engine: int = 4) -> List[UnifiedRecord]:
        tasks = {}
        with ThreadPoolExecutor(max_workers=8) as ex:
            if "elsevier" in self.enabled_engines:
                tasks[ex.submit(self._search_elsevier, query, limit_per_engine)] = "elsevier"
            if "openalex" in self.enabled_engines:
                tasks[ex.submit(self._search_openalex, query, limit_per_engine)] = "openalex"
            if "crossref" in self.enabled_engines:
                tasks[ex.submit(self._search_crossref, query, limit_per_engine)] = "crossref"
            if "arxiv" in self.enabled_engines:
                tasks[ex.submit(self._search_arxiv, query, limit_per_engine)] = "arxiv"
            if "europepmc" in self.enabled_engines:
                tasks[ex.submit(self._search_europepmc, query, limit_per_engine)] = "europepmc"
            if "springer" in self.enabled_engines:
                tasks[ex.submit(self._search_springer, query, limit_per_engine)] = "springer"

            merged: List[UnifiedRecord] = []
            for fut in as_completed(tasks):
                try:
                    merged.extend(fut.result())
                except Exception:
                    continue

        seen = set()
        deduped: List[UnifiedRecord] = []
        for r in merged:
            key = (r.doi.lower().strip() if r.doi else "") or r.title.lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(r)
        deduped.sort(key=lambda x: (x.abstract == "", x.engine, x.year), reverse=False)
        return deduped

    def _parallel_doi_lookup(self, doi: str) -> List[UnifiedRecord]:
        tasks = {}
        with ThreadPoolExecutor(max_workers=8) as ex:
            if "elsevier" in self.enabled_engines:
                tasks[ex.submit(self._lookup_elsevier_doi, doi)] = "elsevier"
            if "openalex" in self.enabled_engines:
                tasks[ex.submit(self._lookup_openalex_doi, doi)] = "openalex"
            if "crossref" in self.enabled_engines:
                tasks[ex.submit(self._lookup_crossref_doi, doi)] = "crossref"
            if "semanticscholar" in self.enabled_engines:
                tasks[ex.submit(self._lookup_semanticscholar_doi, doi)] = "semanticscholar"
            if "arxiv" in self.enabled_engines:
                tasks[ex.submit(self._lookup_arxiv_by_doi, doi)] = "arxiv"
            if "europepmc" in self.enabled_engines:
                tasks[ex.submit(self._lookup_europepmc_doi, doi)] = "europepmc"
            if "springer" in self.enabled_engines:
                tasks[ex.submit(self._lookup_springer_doi, doi)] = "springer"
            merged: List[UnifiedRecord] = []
            for fut in as_completed(tasks):
                try:
                    merged.extend(fut.result())
                except Exception:
                    continue
        seen = set()
        out: List[UnifiedRecord] = []
        for r in merged:
            key = (r.engine, r.raw_id or r.doi or r.title)
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        out.sort(key=lambda x: (x.abstract == "", x.engine))
        return out

    def print_welcome(self) -> None:
        print(self._gradient_banner(RETRO_BANNER))
        print(
            self._panel(
                "SCHOLARFETCH :: READY",
                [
                    "Multi-engine scholarly retrieval from one terminal.",
                    "Examples: /search graph neural networks | /author Albert Einstein | /doi 10.1016/S0014-5793(01)03313-0",
                    "Tab on an empty prompt toggles auto-picker after list-producing commands. Use /pick for manual opening.",
                    "Picker: Left/Right switches action, S saves papers, X removes them, Backspace goes back, Esc exits to prompt, q exits picker mode.",
                    "Key features: multi-engine search, author disambiguation, abstract/full-text reading, references navigation, saved paper lists, BibTeX/citations/abstracts/fulltext export, BibTeX import.",
                    "Main commands: /search /author /papers /doi /abstract /article /refs /saved /export /import /help /quit",
                ],
                color=ANSI.BRIGHT_GREEN,
            )
        )

    def _command_hint(self, line: str) -> str:
        if not line.startswith("/"):
            return ""
        token = line.split(" ", 1)[0]
        matches = [c for c in self.commands if c.startswith(token)]
        if line == "/":
            matches = self.commands[:]
        if not matches:
            return "  no-match (/help)"
        shown = " ".join(matches[:6])
        if len(matches) > 6:
            shown += " ..."
        return f"  {shown}"

    def _selection_hint(self, line: str) -> str:
        if not line.startswith("/"):
            return ""
        cmd, _, arg = line.partition(" ")
        selector = arg.strip()
        if not selector.isdigit():
            return ""
        idx = int(selector) - 1

        if cmd == "/papers" and 0 <= idx < len(self.last_author_candidates):
            cand = self.last_author_candidates[idx]
            label = cand.get("display_name") or cand.get("author_id") or "author"
            aff = cand.get("affiliation") or "-"
            return f"  -> {label} | {aff}"

        if cmd in {"/open", "/abstract", "/article", "/refs"} and 0 <= idx < len(self.last_unified_results):
            rec = self.last_unified_results[idx]
            title = rec.title or rec.doi or "paper"
            year = rec.year or "-"
            return f"  -> {title[:80]} | {year}"

        if cmd == "/ref" and 0 <= idx < len(self.last_references):
            ref = self.last_references[idx]
            doi = ref.get("doi") or "-"
            text = ref.get("text") or "reference"
            return f"  -> {text[:68]} | doi={doi}"

        return ""

    def _has_current_browsable_list(self) -> bool:
        if self.last_list_kind == "authors":
            return bool(self.last_author_candidates)
        if self.last_list_kind == "references":
            return bool(self.last_references)
        if self.last_list_kind in {"papers", "saved"}:
            return bool(self.last_unified_results)
        return False

    def _record_fulltext_status(self, rec: UnifiedRecord) -> str:
        doi = (rec.doi or "").strip()
        engine = (rec.engine or "").strip().lower()
        raw_id = (rec.raw_id or "").strip().upper()
        url = (rec.url or "").strip().lower()
        pdf_url = (rec.pdf_url or "").strip().lower()

        if pdf_url:
            return "yes"
        if engine == "arxiv" or doi.lower().startswith("10.48550/arxiv.") or "arxiv.org" in url or "arxiv.org" in pdf_url:
            return "yes"
        if engine == "springer-oa":
            return "yes"
        if engine == "europepmc" and (raw_id.startswith("PMC") or "/article/pmc/" in url):
            return "yes"
        if engine == "imported":
            return "unknown"
        if not doi:
            return "n/a"
        if not self._looks_like_elsevier_fulltext_doi(doi):
            return "n/a"
        status = self.entitlement_cache.get(doi.lower())
        if not status:
            return "unknown"
        if self._fulltext_available_status(status):
            return "yes"
        if status == "UNAVAILABLE":
            return "no"
        return "unknown"

    def _availability_badge(self, label: str, status: str) -> str:
        normalized = (status or "unknown").lower()
        if normalized == "yes":
            color = ANSI.BRIGHT_GREEN
            value = "YES"
        elif normalized == "no":
            color = ANSI.RED
            value = "NO"
        elif normalized == "n/a":
            color = ANSI.BRIGHT_BLACK
            value = "N/A"
        else:
            color = ANSI.YELLOW
            value = "UNKNOWN"
        return f"[{label}:{color}{ANSI.BOLD}{value}{ANSI.RESET}]"

    @staticmethod
    def _status_label(status: str) -> str:
        normalized = (status or "unknown").lower()
        if normalized == "yes":
            return "YES"
        if normalized == "no":
            return "NO"
        if normalized == "n/a":
            return "N/A"
        return "UNKNOWN"

    @staticmethod
    def _record_key(rec: UnifiedRecord) -> str:
        return ((rec.doi or "").strip().lower() or (rec.title or "").strip().lower())

    def _is_saved_record(self, rec: UnifiedRecord) -> bool:
        key = self._record_key(rec)
        return any(self._record_key(x) == key for x in self.saved_records)

    def _toggle_saved_record(self, rec: UnifiedRecord) -> str:
        key = self._record_key(rec)
        for idx, item in enumerate(self.saved_records):
            if self._record_key(item) == key:
                del self.saved_records[idx]
                return "Removed from saved list"
        self.saved_records.append(rec)
        return "Saved paper selected for export"

    def _remove_saved_record(self, rec: UnifiedRecord) -> str:
        key = self._record_key(rec)
        for idx, item in enumerate(self.saved_records):
            if self._record_key(item) == key:
                del self.saved_records[idx]
                return "Removed from saved list"
        return "Paper was not in saved list"

    def _snapshot_view(self, selected: int = 0, action: str = "") -> Dict[str, Any]:
        return {
            "kind": self.last_list_kind,
            "selected": selected,
            "action": action,
            "path": list(self.pick_path),
            "unified": list(self.last_unified_results),
            "authors": [dict(x) for x in self.last_author_candidates],
            "references": [dict(x) for x in self.last_references],
        }

    def _restore_view(self, state: Dict[str, Any]) -> None:
        self.last_list_kind = state.get("kind", "")
        self.last_unified_results = list(state.get("unified", []))
        self.last_author_candidates = [dict(x) for x in state.get("authors", [])]
        self.last_references = [dict(x) for x in state.get("references", [])]
        self.pick_selected_index = int(state.get("selected", 0))
        self.pick_current_action = str(state.get("action", ""))
        self.pick_path = list(state.get("path", []))

    def _push_current_view(self, selected: int, action: str) -> None:
        self.pick_nav_stack.append(self._snapshot_view(selected, action))

    def _set_picker_root(self, kind: str, label: str = "") -> None:
        self.pick_path = [kind.upper()]
        if label:
            self.pick_path.append(self._short_label(label, max_len=40))

    def _format_breadcrumb(self) -> str:
        if not self.pick_path:
            return "HOME"
        parts: List[str] = []
        for part in self.pick_path:
            clean = re.sub(r"\s+", " ", str(part or "").strip())
            if not clean:
                continue
            if ":" in clean:
                left, right = clean.split(":", 1)
                clean = f"{left.upper()}:{right}"
            else:
                clean = clean.upper()
            parts.append(clean)
        return " > ".join(parts) if parts else "HOME"

    @staticmethod
    def _short_label(text: str, max_len: int = 28) -> str:
        clean = re.sub(r"\s+", " ", (text or "").strip())
        if len(clean) <= max_len:
            return clean
        return clean[: max_len - 3] + "..."

    def _navigate_to_author_papers(self, selected_index: int, action: str = "papers") -> bool:
        if selected_index < 0 or selected_index >= len(self.last_author_candidates):
            self.pick_status_message = "Author selection out of range"
            return False
        self._push_current_view(selected_index, action)
        best = self.last_author_candidates[selected_index]
        seed_record = None
        seed_payload = best.get("seed_record")
        if isinstance(seed_payload, dict):
            try:
                seed_record = UnifiedRecord(**seed_payload)
            except Exception:
                seed_record = None

        if best.get("selection_mode") == "related_author":
            author_specs = best.get("author_specs") or []
            if best.get("author_id") == "__ALL__":
                works: List[UnifiedRecord] = []
                seen_author_ids = set()
                for spec in author_specs:
                    author_id = (spec.get("author_id") or "").strip()
                    if not author_id or author_id in seen_author_ids:
                        continue
                    seen_author_ids.add(author_id)
                    works.extend(self._openalex_works_for_author(author_id, max_results=80))
                works = self._dedupe_records(works)
                self._prefetch_fulltext_status(works, limit=min(35, len(works)))
                if seed_record:
                    works.sort(
                        key=lambda r: (
                            -self._similarity_score(seed_record, r),
                            self._fulltext_rank(r),
                            0 if r.abstract else 1,
                            -self._record_year_int(r),
                        )
                    )
                else:
                    works.sort(
                        key=lambda r: (
                            self._fulltext_rank(r),
                            0 if r.abstract else 1,
                            -self._record_year_int(r),
                            (r.title or "").lower(),
                        )
                    )
            else:
                works = self._openalex_works_for_author(best["author_id"], max_results=120)
                works = self._dedupe_records(works)
                self._prefetch_fulltext_status(works, limit=min(25, len(works)))
                if seed_record:
                    works.sort(
                        key=lambda r: (
                            -self._similarity_score(seed_record, r),
                            self._fulltext_rank(r),
                            0 if r.abstract else 1,
                            -self._record_year_int(r),
                        )
                    )
                else:
                    works.sort(
                        key=lambda r: (
                            self._fulltext_rank(r),
                            0 if r.abstract else 1,
                            -self._record_year_int(r),
                            (r.title or "").lower(),
                        )
                    )
        else:
            works = self._openalex_works_for_author(best["author_id"], max_results=120)
            works = self._dedupe_records(works)
            self._prefetch_fulltext_status(works, limit=min(25, len(works)))
            works.sort(
                key=lambda r: (
                    self._fulltext_rank(r),
                    0 if r.abstract else 1,
                    -self._record_year_int(r),
                    (r.title or "").lower(),
                )
            )
        if not works:
            self.pick_nav_stack.pop()
            self.pick_status_message = "No papers found for selected author"
            return False
        self.last_unified_results = works
        self.last_list_kind = "papers"
        self.pick_selected_index = 0
        self.pick_current_action = "open"
        self.pick_path.append(f"papers:{self._short_label(best.get('display_name') or 'author')}")
        self.pick_status_message = ""
        return True

    def _navigate_to_refs(self, selected_index: int, action: str = "refs") -> bool:
        if selected_index < 0 or selected_index >= len(self.last_unified_results):
            self.pick_status_message = "Paper selection out of range"
            return False
        rec = self.last_unified_results[selected_index]
        doi = (rec.doi or "").strip()
        if not doi:
            self.pick_status_message = "References not available: selected paper has no DOI"
            return False
        resolved = self._resolve_references(doi, seed_record=rec)
        refs = resolved.get("references") or []
        if not refs:
            self.pick_status_message = "References not available for the selected paper"
            return False
        refs, interrupted = self._enrich_references_for_picker(refs)
        self._push_current_view(selected_index, action)
        self.last_references = refs
        self.last_list_kind = "references"
        self.pick_selected_index = 0
        self.pick_current_action = "open"
        self.pick_path.append(f"refs:{self._short_label(rec.title or doi)}")
        self.pick_status_message = (
            f"Reference previews interrupted; showing partial results ({len(refs)} refs)"
            if interrupted
            else ""
        )
        return True

    def _navigate_to_ref(self, selected_index: int, action: str = "open") -> bool:
        if selected_index < 0 or selected_index >= len(self.last_references):
            self.pick_status_message = "Reference selection out of range"
            return False
        ref = self.last_references[selected_index]
        doi = (ref.get("doi") or "").strip()
        if doi:
            rows = self._parallel_doi_lookup(doi)
        else:
            text = (ref.get("text") or "").strip()
            rows = self._parallel_search(text[:220], limit_per_engine=3) if text else []
        rows = self._dedupe_records(rows)
        if not rows:
            self.pick_status_message = "Reference did not resolve to a paper list"
            return False
        self._push_current_view(selected_index, action)
        self.last_unified_results = rows
        self.last_list_kind = "papers"
        self._prefetch_fulltext_status(rows, limit=min(12, len(rows)))
        self.pick_selected_index = 0
        self.pick_current_action = action if action in {"open", "abstract", "text", "refs", "author"} else "open"
        self.pick_path.append(f"ref:{self._short_label((ref.get('text') or doi or 'reference'))}")
        self.pick_status_message = ""
        return True

    @staticmethod
    def _similarity_score(seed: UnifiedRecord, other: UnifiedRecord) -> float:
        def tokens(text: str) -> set:
            return {
                t
                for t in re.findall(r"[a-z0-9]{3,}", (text or "").lower())
                if t not in {"with", "from", "that", "this", "their", "through", "using", "into"}
            }
        a = tokens((seed.title or "") + " " + (seed.abstract or ""))
        b = tokens((other.title or "") + " " + (other.abstract or ""))
        if not a or not b:
            return 0.0
        return len(a & b) / max(1, len(a | b))

    def _build_related_author_candidates(self, rec: UnifiedRecord) -> List[Dict[str, Any]]:
        authors = self._split_authors(rec.authors)
        if not authors:
            return []
        out: List[Dict[str, Any]] = []
        author_specs: List[Dict[str, Any]] = []
        seen_ids = set()
        for author_name in authors[:8]:
            candidates = self._openalex_author_candidates(author_name, per_page=5)
            best = candidates[0] if candidates else None
            author_id = (best or {}).get("author_id", "")
            if author_id and author_id in seen_ids:
                continue
            if author_id:
                seen_ids.add(author_id)
            spec = {
                "author_name": author_name,
                "author_id": author_id,
                "display_name": (best or {}).get("display_name", author_name) or author_name,
                "works_count": int((best or {}).get("works_count", 0) or 0),
                "cited_by_count": int((best or {}).get("cited_by_count", 0) or 0),
                "affiliation": (best or {}).get("affiliation", ""),
            }
            author_specs.append(spec)
            out.append(
                {
                    "engine": "related-author",
                    "author_id": author_id,
                    "display_name": spec["display_name"],
                    "orcid": "",
                    "works_count": spec["works_count"],
                    "cited_by_count": spec["cited_by_count"],
                    "affiliation": spec["affiliation"],
                    "score": 0,
                    "selection_mode": "related_author",
                    "author_specs": author_specs,
                    "seed_record": asdict(rec),
                }
            )
        if len(author_specs) > 1:
            out.insert(
                0,
                {
                    "engine": "related-author",
                    "author_id": "__ALL__",
                    "display_name": "ALL AUTHORS",
                    "orcid": "",
                    "works_count": sum(int(spec.get("works_count", 0) or 0) for spec in author_specs),
                    "cited_by_count": sum(int(spec.get("cited_by_count", 0) or 0) for spec in author_specs),
                    "affiliation": "Combined papers from all listed authors",
                    "score": 0,
                    "selection_mode": "related_author",
                    "author_specs": author_specs,
                    "seed_record": asdict(rec),
                },
            )
        return out

    def _open_related_author_selector(self, rec: UnifiedRecord, selected_index: int, action: str = "author") -> bool:
        candidates = self._build_related_author_candidates(rec)
        if not candidates:
            self.pick_status_message = "Selected paper has no resolvable author list"
            return False
        self._push_current_view(selected_index, action)
        self.last_author_candidates = candidates
        self.last_list_kind = "authors"
        self.pick_selected_index = 0
        self.pick_current_action = "papers"
        self.pick_path.append(f"authors:{self._short_label(rec.title or rec.doi or 'paper')}")
        self.pick_status_message = "Select one author or ALL AUTHORS to build a related paper list"
        return True

    def _navigate_to_author_related_papers(self, selected_index: int, action: str = "author") -> bool:
        if selected_index < 0 or selected_index >= len(self.last_unified_results):
            self.pick_status_message = "Paper selection out of range"
            return False
        rec = self.last_unified_results[selected_index]
        return self._open_related_author_selector(rec, selected_index, action=action)

    def _navigate_to_author_related_from_record(self, rec: UnifiedRecord, selected_index: int, action: str = "author") -> bool:
        return self._open_related_author_selector(rec, selected_index, action=action)

    def _navigate_to_reference_refs(self, selected_index: int, action: str = "refs") -> bool:
        if selected_index < 0 or selected_index >= len(self.last_references):
            self.pick_status_message = "Reference selection out of range"
            return False
        ref = self.last_references[selected_index]
        rec = self._reference_preview_from_entry(ref)
        doi = ((rec.doi if rec else "") or ref.get("doi") or "").strip()
        if not doi:
            self.pick_status_message = "References not available: selected reference has no DOI"
            return False
        resolved = self._resolve_references(doi, seed_record=rec)
        refs = resolved.get("references") or []
        if not refs:
            self.pick_status_message = "References not available for the selected reference"
            return False
        refs, interrupted = self._enrich_references_for_picker(refs)
        self._push_current_view(selected_index, action)
        self.last_references = refs
        self.last_list_kind = "references"
        self.pick_selected_index = 0
        self.pick_current_action = "open"
        self.pick_path.append(f"refs:{self._short_label((rec.title if rec else doi) or doi)}")
        self.pick_status_message = (
            f"Reference previews interrupted; showing partial results ({len(refs)} refs)"
            if interrupted
            else ""
        )
        return True

    def _current_picker_actions(self) -> List[str]:
        if self.last_list_kind == "authors":
            return ["papers"]
        if self.last_list_kind == "references":
            return ["open", "abstract", "text", "refs", "author"]
        if self.last_list_kind in {"papers", "saved"}:
            return ["open", "abstract", "text", "refs", "author"]
        return []

    def _pick_current_item(self, initial_action: str = "") -> Optional[Tuple[str, int]]:
        initial_actions = self._current_picker_actions()
        if not initial_actions:
            print(self._retro("No current list to browse. Run /author, /search, /doi, /papers, or /refs first.", ANSI.YELLOW))
            return None
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print(self._retro("Interactive picker requires a TTY terminal.", ANSI.YELLOW))
            return None

        def fallback_prompt() -> Optional[Tuple[str, int]]:
            actions = self._current_picker_actions()
            if not actions:
                return None
            current = initial_action if initial_action in actions else actions[0]
            print(self._retro(f"Picker fallback active. Actions: {', '.join(actions)}", ANSI.YELLOW))
            choice = input(self._retro(f"Action [{current}]: ", ANSI.BRIGHT_CYAN)).strip().lower() or current
            if choice == "article":
                choice = "text"
            if choice not in actions:
                print(self._retro("Invalid action.", ANSI.YELLOW))
                return None
            raw = input(self._retro("Select index (empty to cancel): ", ANSI.BRIGHT_CYAN)).strip()
            if not raw:
                return None
            if not raw.isdigit():
                print(self._retro("Invalid index.", ANSI.YELLOW))
                return None
            return choice, int(raw) - 1

        def run_picker(stdscr: Any) -> Optional[int]:
            try:
                curses.start_color()
            except Exception:
                pass
            try:
                curses.use_default_colors()
            except Exception:
                pass
            try:
                curses.curs_set(0)
            except Exception:
                pass
            stdscr.keypad(True)
            try:
                curses.init_pair(1, curses.COLOR_GREEN, -1)
                curses.init_pair(2, curses.COLOR_CYAN, -1)
                curses.init_pair(3, curses.COLOR_YELLOW, -1)
                curses.init_pair(4, curses.COLOR_RED, -1)
                curses.init_pair(5, curses.COLOR_WHITE, -1)
                curses.init_pair(6, curses.COLOR_MAGENTA, -1)
            except Exception:
                pass

            if self.pick_root_state is None:
                self.pick_root_state = self._snapshot_view(0, initial_action)
            selected = max(0, self.pick_selected_index)
            offset = 0
            actions = list(initial_actions)
            preferred_action = self.pick_current_action or initial_action
            action_idx = actions.index(preferred_action) if preferred_action in actions else 0

            def fit(text: str, width: int) -> List[str]:
                clean = re.sub(r"\s+", " ", text).strip()
                return textwrap.wrap(clean, width=max(20, width), break_long_words=True, break_on_hyphens=False) or [""]

            def add_status_line(row: int, width: int, abstract_status: str, full_status: str, selected_row: bool) -> None:
                label_attr = curses.A_DIM
                if selected_row:
                    label_attr |= curses.A_BOLD
                try:
                    stdscr.addnstr(row, 0, "    ABSTRACT:", width, label_attr)
                    x = min(width - 1, 14)
                    stdscr.addnstr(row, x, self._status_label(abstract_status), max(0, width - x), self._status_attr(abstract_status, selected_row))
                    x = min(width - 1, 20)
                    stdscr.addnstr(row, x, "  FULLTEXT:", max(0, width - x), label_attr)
                    x = min(width - 1, 32)
                    stdscr.addnstr(row, x, self._status_label(full_status), max(0, width - x), self._status_attr(full_status, selected_row))
                except curses.error:
                    pass

            while True:
                stdscr.erase()
                height, width_total = stdscr.getmaxyx()
                width = max(30, width_total - 2)
                body_height = max(4, height - 7)
                actions = self._current_picker_actions()
                if not actions:
                    return None
                if action_idx >= len(actions):
                    action_idx = 0
                if self.last_list_kind == "authors":
                    total = len(self.last_author_candidates)
                elif self.last_list_kind == "references":
                    total = len(self.last_references)
                else:
                    total = len(self.last_unified_results)
                if total == 0:
                    return None
                selected = max(0, min(selected, total - 1))

                current_action = actions[action_idx]
                title = f"PICK {self.last_list_kind.upper() or 'LIST'} FOR /{current_action}  [{selected + 1}/{total}]"
                breadcrumb = self._format_breadcrumb()
                try:
                    stdscr.addnstr(0, 0, title, width, curses.color_pair(2) | curses.A_BOLD)
                    stdscr.addnstr(1, 0, breadcrumb, width, curses.A_DIM)
                    stdscr.addnstr(2, 0, "-" * min(width, max(len(title), len(breadcrumb))), width, curses.color_pair(2))
                except curses.error:
                    pass
                action_row = 3
                x = 0
                for idx_action, action_name in enumerate(actions):
                    label = f"[{action_name.upper()}]"
                    attr = curses.A_BOLD
                    if idx_action == action_idx:
                        attr |= curses.color_pair(6)
                    else:
                        attr |= curses.A_DIM
                    try:
                        if x < width:
                            stdscr.addnstr(action_row, x, label, max(0, width - x), attr)
                    except curses.error:
                        pass
                    x += len(label) + 1

                blocks: List[List[str]] = []
                line_meta: List[Tuple[str, str]] = []
                if self.last_list_kind == "authors":
                    for pos, cand in enumerate(self.last_author_candidates):
                        marker = ">" if pos == selected else " "
                        head = f"{marker} [{pos + 1}] {cand.get('display_name') or cand.get('author_id') or 'author'}"
                        meta = f"    works={cand.get('works_count', 0)} | cites={cand.get('cited_by_count', 0)} | affiliation={cand.get('affiliation') or '-'}"
                        blocks.append(fit(head, width) + fit(meta, width))
                        line_meta.append(("", ""))
                elif self.last_list_kind == "references":
                    for pos, ref in enumerate(self.last_references):
                        marker = ">" if pos == selected else " "
                        doi = ref.get("preview_doi") or ref.get("doi") or "-"
                        engine = ref.get("preview_engine") or "-"
                        year = ref.get("preview_year") or "-"
                        title = ref.get("preview_title") or ref.get("text") or "reference"
                        preview = self._cached_reference_preview(ref)
                        saved_mark = " [S]" if preview and self._is_saved_record(preview) else ""
                        abstract_status = ref.get("abstract_status") or "unknown"
                        full_status = ref.get("fulltext_status") or "unknown"
                        head = f"{marker} [{pos + 1}] {title}{saved_mark}"
                        meta = f"    {year} | {engine} | doi={doi}"
                        blocks.append(fit(head, width) + fit(meta, width) + ["__STATUS__"])
                        line_meta.append((abstract_status, full_status))
                else:
                    for pos, rec in enumerate(self.last_unified_results):
                        marker = ">" if pos == selected else " "
                        full_status = self._record_fulltext_status(rec)
                        abstract_status = "yes" if (rec.abstract or "").strip() else "no"
                        saved_mark = " [S]" if self._is_saved_record(rec) else ""
                        head = f"{marker} [{pos + 1}] {rec.title or rec.doi or '-'}{saved_mark}"
                        meta = f"    {rec.year or '-'} | {rec.engine} | doi={rec.doi or '-'}"
                        blocks.append(fit(head, width) + fit(meta, width) + ["__STATUS__"])
                        line_meta.append((abstract_status, full_status))

                if selected < offset:
                    offset = selected
                used = 0
                probe = offset
                while probe < total:
                    needed = len(blocks[probe])
                    if used + needed > body_height:
                        break
                    used += needed
                    probe += 1
                if probe <= selected:
                    offset = selected

                row = 5
                pos = offset
                while pos < total and row < height - 1:
                    block = blocks[pos]
                    if row + len(block) > height - 1:
                        break
                    for line_idx, segment in enumerate(block):
                        if segment == "__STATUS__":
                            abstract_status, full_status = line_meta[pos]
                            add_status_line(row, width, abstract_status, full_status, pos == selected)
                        else:
                            attr = curses.A_NORMAL
                            if pos == selected and line_idx == 0:
                                attr = curses.color_pair(1) | curses.A_BOLD
                            elif self.last_list_kind == "papers" and line_idx == 0 and self._is_saved_record(self.last_unified_results[pos]):
                                attr = curses.color_pair(6) | curses.A_BOLD
                            elif line_idx > 0:
                                attr = curses.A_DIM
                            try:
                                stdscr.addnstr(row, 0, segment, width, attr)
                            except curses.error:
                                pass
                        row += 1
                    pos += 1

                status_line = self.pick_status_message or "Enter run | s save | x remove | Backspace parent | Esc prompt | q exit picker"
                footer = f"Use Up/Down, j/k, Left/Right actions, Enter to run /{current_action}"
                status_row = max(0, height - 2)
                status_attr = curses.A_DIM
                if self.pick_status_message:
                    lowered = self.pick_status_message.lower()
                    if any(token in lowered for token in ["not available", "error", "out of range", "no doi", "could not"]):
                        status_attr = curses.color_pair(3) | curses.A_BOLD
                try:
                    stdscr.addnstr(status_row, 0, status_line, width, status_attr)
                    stdscr.addnstr(height - 1, 0, footer, width, curses.A_DIM)
                except curses.error:
                    pass
                stdscr.refresh()

                key = stdscr.getch()
                if key in (ord("q"), ord("Q"), 3):
                    self.pick_exit_reason = "cancel"
                    return None
                if key in (curses.KEY_ENTER, 10, 13):
                    self.pick_selected_index = selected
                    self.pick_current_action = current_action
                    self.pick_status_message = ""
                    self.pick_exit_reason = ""
                    return (current_action, selected)
                if key in (27,):
                    self.pick_exit_reason = "home"
                    return None
                if key in (curses.KEY_BACKSPACE, 127, 8):
                    if self.pick_nav_stack:
                        prev = self.pick_nav_stack.pop()
                        selected = int(prev.get("selected", 0))
                        action = str(prev.get("action", ""))
                        self._restore_view(prev)
                        actions = self._current_picker_actions()
                        action_idx = actions.index(action) if action in actions else 0
                        self.pick_status_message = "Returned to previous step"
                    else:
                        self.pick_status_message = "Already at root"
                    continue
                if key in (ord("s"), ord("S")):
                    if self.last_list_kind == "papers" and 0 <= selected < len(self.last_unified_results):
                        self.pick_status_message = self._toggle_saved_record(self.last_unified_results[selected])
                        continue
                    if self.last_list_kind == "references" and 0 <= selected < len(self.last_references):
                        preview = self._reference_preview_from_entry(self.last_references[selected])
                        if not preview:
                            self.pick_status_message = "Reference could not be resolved to a savable paper preview"
                        else:
                            self.pick_status_message = self._toggle_saved_record(preview)
                        continue
                if key in (ord("x"), ord("X")):
                    if self.last_list_kind in {"papers", "saved"} and 0 <= selected < len(self.last_unified_results):
                        rec = self.last_unified_results[selected]
                        self.pick_status_message = self._remove_saved_record(rec)
                        if self.last_list_kind == "saved":
                            self.last_unified_results = [r for r in self.last_unified_results if self._record_key(r) != self._record_key(rec)]
                            total = len(self.last_unified_results)
                            if total <= 0:
                                self.pick_status_message = "Saved list is empty; returning home"
                                if self.pick_root_state is not None:
                                    self._restore_view(self.pick_root_state)
                                    self.pick_nav_stack = []
                            else:
                                selected = min(selected, total - 1)
                        continue
                    if self.last_list_kind == "references" and 0 <= selected < len(self.last_references):
                        preview = self._reference_preview_from_entry(self.last_references[selected])
                        if not preview:
                            self.pick_status_message = "Reference could not be resolved to a removable paper preview"
                        else:
                            self.pick_status_message = self._remove_saved_record(preview)
                        continue
                if key in (curses.KEY_UP, ord("k")) and selected > 0:
                    selected -= 1
                    self.pick_status_message = ""
                elif key in (curses.KEY_DOWN, ord("j")) and selected < total - 1:
                    selected += 1
                    self.pick_status_message = ""
                elif key == curses.KEY_LEFT:
                    action_idx = (action_idx - 1) % len(actions)
                    self.pick_status_message = ""
                elif key == curses.KEY_RIGHT:
                    action_idx = (action_idx + 1) % len(actions)
                    self.pick_status_message = ""
                elif key == curses.KEY_NPAGE:
                    selected = min(total - 1, selected + max(1, body_height // 2))
                    self.pick_status_message = ""
                elif key == curses.KEY_PPAGE:
                    selected = max(0, selected - max(1, body_height // 2))
                    self.pick_status_message = ""
                elif key == ord("g"):
                    selected = 0
                    self.pick_status_message = ""
                elif key == ord("G"):
                    selected = total - 1
                    self.pick_status_message = ""

        try:
            picked = curses.wrapper(run_picker)
            if picked is None:
                return None
            return picked[0], picked[1]
        except Exception as err:
            self.pick_status_message = f"Picker fallback due to terminal error: {err}"
            return fallback_prompt()

    @staticmethod
    def _status_attr(status: str, selected_row: bool) -> int:
        normalized = (status or "unknown").lower()
        base = curses.A_BOLD if selected_row else curses.A_NORMAL
        if normalized == "yes":
            return curses.color_pair(1) | base
        if normalized == "no":
            return curses.color_pair(4) | base
        if normalized == "n/a":
            return curses.A_DIM
        return curses.color_pair(3) | base

    def _interactive_pager(self, title: str, text: str, color: str = ANSI.BRIGHT_GREEN) -> None:
        if not text.strip():
            print(self._retro("No text available.", ANSI.YELLOW))
            return
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print(self._panel(title, [text[:12000]], color=color))
            return

        pager = shutil.which("less")
        if pager:
            header = color + title + ANSI.RESET + "\n" + color + ("=" * min(100, len(title))) + ANSI.RESET + "\n\n"
            content = header + text + "\n"
            try:
                proc = subprocess.Popen([pager, "-R"], stdin=subprocess.PIPE)
                if proc.stdin:
                    proc.stdin.write(content.encode("utf-8", errors="replace"))
                    proc.stdin.close()
                proc.wait()
                return
            except Exception:
                pass

        raw_lines = text.splitlines() or [text]
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        offset = 0

        def visual_lines(width: int) -> List[str]:
            out: List[str] = []
            for line in raw_lines:
                out.extend(textwrap.wrap(line, width=width) or [""])
            return out

        def render() -> int:
            size = shutil.get_terminal_size((100, 30))
            height = max(8, size.lines - 4)
            width = max(40, size.columns - 2)
            lines = visual_lines(width)
            sys.stdout.write("\033[2J\033[H")
            header = f"{title}  [{offset + 1}-{min(len(lines), offset + height)}/{len(lines)}]  q quit"
            sys.stdout.write(color + header[:width] + ANSI.RESET + "\n")
            sys.stdout.write(color + ("-" * min(width, len(header))) + ANSI.RESET + "\n")
            visible = lines[offset : offset + height]
            for line in visible:
                sys.stdout.write(line[:width] + "\n")
            sys.stdout.write(ANSI.DIM + ANSI.BRIGHT_BLACK + "Use Up/Down, PgUp/PgDn, j/k, g/G, q" + ANSI.RESET)
            sys.stdout.flush()
            return len(lines)

        try:
            tty.setraw(fd)
            while True:
                size = shutil.get_terminal_size((100, 30))
                page = max(1, size.lines - 6)
                total = render()
                ch = os.read(fd, 1)
                if not ch:
                    break
                c = ch.decode("utf-8", errors="ignore")
                if c in {"q", "Q", "\x03"}:
                    break
                if c in {"j", " "}:
                    offset = min(max(0, total - page), offset + page)
                    continue
                if c == "k":
                    offset = max(0, offset - page)
                    continue
                if c == "g":
                    offset = 0
                    continue
                if c == "G":
                    offset = max(0, total - page)
                    continue
                if c == "\x1b":
                    seq = os.read(fd, 2).decode("utf-8", errors="ignore")
                    if seq == "[A":
                        offset = max(0, offset - 1)
                    elif seq == "[B":
                        offset = min(max(0, total - page), offset + 1)
                    elif seq == "[5":
                        os.read(fd, 1)
                        offset = max(0, offset - page)
                    elif seq == "[6":
                        os.read(fd, 1)
                        offset = min(max(0, total - page), offset + page)
                    else:
                        break
                    continue
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()

    def _wait_for_keypress(self, message: str = "Press any key to return") -> None:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            sys.stdout.write("\n" + ANSI.DIM + ANSI.BRIGHT_BLACK + message + ANSI.RESET)
            sys.stdout.flush()
            tty.setraw(fd)
            os.read(fd, 1)
        except Exception:
            pass
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                pass
            sys.stdout.write("\r\033[2K")
            sys.stdout.flush()

    @contextlib.contextmanager
    def _progress(self, message: str):
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            yield
            return

        stop = threading.Event()
        frames = ["[=     ]", "[==    ]", "[===   ]", "[ ==== ]", "[  === ]", "[   == ]", "[    = ]"]

        def worker() -> None:
            idx = 0
            while not stop.is_set():
                frame = frames[idx % len(frames)]
                sys.stdout.write("\r\033[2K" + ANSI.DIM + ANSI.BRIGHT_BLACK + f"{message} {frame}" + ANSI.RESET)
                sys.stdout.flush()
                idx += 1
                time.sleep(0.12)
            sys.stdout.write("\r\033[2K")
            sys.stdout.flush()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=0.5)

    def _read_input_line(self) -> str:
        # Non-interactive fallback for piped/scripted usage.
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return input(self._retro("ScholarFetch> ", ANSI.BRIGHT_GREEN, bold=True))

        prompt = "ScholarFetch> "
        buffer: List[str] = []
        cursor = 0
        history_index = len(self.input_history)
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)

        def render() -> None:
            line = "".join(buffer)
            hint = self._command_hint(line) + self._selection_hint(line)
            width = shutil.get_terminal_size((100, 30)).columns
            available = max(0, width - len(prompt) - len(line) - 1)
            if available and len(hint) > available:
                hint = hint[:available]
            elif not available:
                hint = ""
            colored_hint = (ANSI.DIM + ANSI.BRIGHT_BLACK + hint + ANSI.RESET) if hint else ""
            sys.stdout.write("\r\033[2K" + prompt + line + colored_hint)
            sys.stdout.write(f"\033[{len(prompt) + cursor}C")
            sys.stdout.flush()

        try:
            tty.setraw(fd)
            while True:
                render()
                ch = os.read(fd, 1)
                if not ch:
                    raise EOFError
                c = ch.decode("utf-8", errors="ignore")

                if c in ("\r", "\n"):
                    sys.stdout.write("\r\033[2K" + prompt + "".join(buffer) + "\n")
                    sys.stdout.flush()
                    out = "".join(buffer)
                    if out.strip():
                        if not self.input_history or self.input_history[-1] != out:
                            self.input_history.append(out)
                    return out
                if c == "\x03":
                    raise KeyboardInterrupt
                if c == "\t" and not buffer:
                    self.auto_pick_after_list = not self.auto_pick_after_list
                    state = "enabled" if self.auto_pick_after_list else "disabled"
                    sys.stdout.write("\r\033[2K" + prompt + "\n")
                    sys.stdout.write(self._retro(f"Auto-picker {state}. Use /pick for manual browsing.\n", ANSI.BRIGHT_BLACK))
                    sys.stdout.flush()
                    continue
                if c == "\x04":
                    if not buffer:
                        raise EOFError
                    continue
                if c in ("\x7f", "\b"):  # backspace
                    if cursor > 0:
                        del buffer[cursor - 1]
                        cursor -= 1
                    continue
                if c == "\x1b":
                    seq = os.read(fd, 2).decode("utf-8", errors="ignore")
                    if seq == "[D" and cursor > 0:
                        cursor -= 1
                    elif seq == "[C" and cursor < len(buffer):
                        cursor += 1
                    elif seq == "[A" and self.input_history:
                        history_index = max(0, history_index - 1)
                        buffer = list(self.input_history[history_index])
                        cursor = len(buffer)
                    elif seq == "[B" and self.input_history:
                        history_index = min(len(self.input_history), history_index + 1)
                        if history_index >= len(self.input_history):
                            buffer = []
                        else:
                            buffer = list(self.input_history[history_index])
                        cursor = len(buffer)
                    continue
                if c.isprintable():
                    buffer.insert(cursor, c)
                    cursor += 1
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def print_help(self) -> None:
        print(
            self._panel(
                "COMMANDS",
                [
                    "/search <query>               Unified search across enabled engines.",
                    "/config ...                   Configure active engines (only/add/remove/reset/save).",
                    "/engines                      Show active engines used in parallel search.",
                    "/author <name>                Resolve author profiles (OpenAlex-ranked candidates).",
                    "/papers <author|index>        Fetch papers; supports has:abstract and has:fulltext filters.",
                    "/doi <doi>                    Quick metadata + abstract preview by DOI.",
                    "/abstract <doi|index>         Show abstract by DOI or from last result index.",
                    "/article <doi|index>          Retrieve full article text and open it in the pager.",
                    "/refs <doi|index>             Fetch references for a paper and store them for /ref.",
                    "/ref <index>                  Resolve one reference from the last /refs result.",
                    "/pick [mode]                  Browse the current author/paper/reference list. Use Left/Right to switch action.",
                    "Tab on empty prompt           Toggle auto-picker after list-producing commands.",
                    "Picker keys                   S save paper | X remove paper from saved list | Backspace back | Esc exit to prompt | q quit picker.",
                    "/saved                        Show the saved paper list as a browsable picker source.",
                    "/export [fmt style path ...]  Export saved papers as bib, citations, abstracts, or aggregated fulltext.",
                    "/import [path]                Import BibTeX into a browsable paper list.",
                    "/open <index>                 Open result #N from last /search and show details.",
                    "/clear                        Clear terminal screen.",
                    "/help                         Show this command list.",
                    "/quit                         Exit.",
                ],
                color=ANSI.BRIGHT_MAGENTA,
            )
        )

    @staticmethod
    def _is_filter_token(token: str) -> bool:
        t = token.lower()
        if re.fullmatch(r"year(=|>=|<=)\d{4}", t):
            return True
        if t.startswith("has:"):
            return True
        if t.startswith("venue:") or t.startswith("title:") or t.startswith("doi:"):
            return True
        return False

    @staticmethod
    def _split_selector_filters(arg: str) -> Tuple[str, List[str]]:
        tokens = [t for t in re.split(r"\s+", arg.strip()) if t]
        selector_tokens: List[str] = []
        filter_tokens: List[str] = []
        for tok in tokens:
            if RetroCLI._is_filter_token(tok):
                filter_tokens.append(tok)
            else:
                selector_tokens.append(tok)
        return " ".join(selector_tokens).strip(), filter_tokens

    @staticmethod
    def _record_year_int(rec: UnifiedRecord) -> int:
        try:
            return int(rec.year)
        except Exception:
            return 0

    def _fulltext_rank(self, rec: UnifiedRecord) -> int:
        status = self._record_fulltext_status(rec)
        if status == "yes":
            return 0
        if status == "unknown":
            return 1
        if status == "no":
            return 2
        return 3

    def _apply_paper_filters(self, records: List[UnifiedRecord], filters: List[str]) -> List[UnifiedRecord]:
        out = records[:]
        for f in filters:
            fl = f.lower()
            if re.fullmatch(r"year(=|>=|<=)\d{4}", fl):
                m = re.fullmatch(r"year(=|>=|<=)(\d{4})", fl)
                if not m:
                    continue
                op, year_s = m.group(1), m.group(2)
                y = int(year_s)
                if op == "=":
                    out = [r for r in out if self._record_year_int(r) == y]
                elif op == ">=":
                    out = [r for r in out if self._record_year_int(r) >= y]
                elif op == "<=":
                    out = [r for r in out if self._record_year_int(r) <= y]
            elif fl == "has:abstract":
                out = [r for r in out if bool((r.abstract or "").strip())]
            elif fl == "has:doi":
                out = [r for r in out if bool((r.doi or "").strip())]
            elif fl == "has:pdf":
                out = [r for r in out if bool((r.pdf_url or "").strip())]
            elif fl == "has:fulltext":
                out = [r for r in out if self._record_fulltext_status(r) == "yes"]
            elif fl.startswith("venue:"):
                needle = fl.split(":", 1)[1].strip()
                out = [r for r in out if needle in (r.venue or "").lower()]
            elif fl.startswith("title:"):
                needle = fl.split(":", 1)[1].strip()
                out = [r for r in out if needle in (r.title or "").lower()]
            elif fl.startswith("doi:"):
                needle = fl.split(":", 1)[1].strip()
                out = [r for r in out if needle in (r.doi or "").lower()]
        return out

    def _resolve_result_selector(self, arg: str) -> Tuple[str, str, Optional[UnifiedRecord]]:
        if arg.isdigit():
            idx = int(arg) - 1
            if self.last_list_kind == "references":
                if idx < 0 or idx >= len(self.last_references):
                    raise ValueError("Index out of range for last references.")
                ref = self.last_references[idx]
                rec = self._reference_preview_from_entry(ref)
                doi = (rec.doi if rec else ref.get("doi", "")).strip()
                label = (rec.title if rec else ref.get("preview_title") or ref.get("text") or f"reference #{idx + 1}")
                return doi, label, rec
            if idx < 0 or idx >= len(self.last_unified_results):
                raise ValueError("Index out of range for last results.")
            rec = self.last_unified_results[idx]
            return rec.doi or "", rec.title or f"result #{idx + 1}", rec
        return arg, arg, None

    def cmd_engines(self) -> None:
        lines = [
            "Parallel engines currently enabled:",
            ", ".join(self.enabled_engines),
            "Used by: /search and /doi (with unified, deduped results).",
            "Note: springer keyword search may be premium-restricted; DOI lookup works.",
        ]
        print(self._panel("ENGINES", lines, color=ANSI.BRIGHT_CYAN))

    def cmd_config(self, arg: str) -> None:
        tokens = [t for t in re.split(r"\s+", arg.strip()) if t]
        if not tokens:
            lines = [
                f"Enabled: {', '.join(self.enabled_engines)}",
                f"Available: {', '.join(self.available_engines)}",
                "Commands:",
                "/config only <eng1,eng2,...>",
                "/config add <eng1,eng2,...>",
                "/config remove <eng1,eng2,...>",
                "/config reset",
                "/config save",
            ]
            print(self._panel("CONFIG", lines, color=ANSI.BRIGHT_CYAN))
            return

        action = tokens[0].lower()
        raw = " ".join(tokens[1:]).replace(",", " ")
        engines = [e.strip().lower() for e in raw.split() if e.strip()]

        def valid_list(values: List[str]) -> List[str]:
            return [v for v in values if v in self.available_engines]

        if action == "only":
            picked = valid_list(engines)
            if not picked:
                print(self._retro("No valid engines specified.", ANSI.YELLOW))
                return
            self.enabled_engines = picked
            print(self._retro(f"Enabled engines set to: {', '.join(self.enabled_engines)}", ANSI.BRIGHT_CYAN))
        elif action == "add":
            picked = valid_list(engines)
            for e in picked:
                if e not in self.enabled_engines:
                    self.enabled_engines.append(e)
            print(self._retro(f"Enabled engines: {', '.join(self.enabled_engines)}", ANSI.BRIGHT_CYAN))
        elif action == "remove":
            picked = set(valid_list(engines))
            self.enabled_engines = [e for e in self.enabled_engines if e not in picked]
            if not self.enabled_engines:
                self.enabled_engines = self.default_engines[:]
                print(self._retro("Cannot leave engine set empty. Restored defaults.", ANSI.YELLOW))
            print(self._retro(f"Enabled engines: {', '.join(self.enabled_engines)}", ANSI.BRIGHT_CYAN))
        elif action == "reset":
            self.enabled_engines = self.default_engines[:]
            print(self._retro(f"Restored defaults: {', '.join(self.enabled_engines)}", ANSI.BRIGHT_CYAN))
        elif action == "save":
            self._save_engine_settings()
            print(self._retro(f"Saved engine settings to {self.config_path}", ANSI.BRIGHT_CYAN))
        else:
            print(self._retro("Unknown /config action. Use: only/add/remove/reset/save", ANSI.YELLOW))

    def cmd_search(self, arg: str) -> None:
        if not arg:
            print(self._retro("Usage: /search <keywords>", ANSI.YELLOW))
            return
        if self.enabled_engines == ["springer"] and not self._looks_like_doi(arg):
            print(
                self._retro(
                    "Springer-only mode: keyword/person search is usually premium-restricted. Use a DOI (/search <doi> or /doi <doi>).",
                    ANSI.YELLOW,
                )
            )
            return
        if self._looks_like_person_name(arg) and "openalex" in self.enabled_engines:
            self.cmd_papers(arg)
            return
        with self._progress("Searching scholarly engines..."):
            if self._looks_like_doi(arg):
                results = self._parallel_doi_lookup(arg)
            else:
                results = self._parallel_search(arg, limit_per_engine=4)
        with self._progress("Checking full-text availability..."):
            self._prefetch_fulltext_status(results, limit=min(12, len(results)))
        self.last_unified_results = results
        self.last_list_kind = "papers"
        self._set_picker_root("search", arg)
        if not results:
            print(self._retro("No results found across enabled engines.", ANSI.YELLOW))
            return

        lines = [f"Unified results from: {', '.join(self.enabled_engines)}"]
        for i, item in enumerate(results, start=1):
            full_status = self._record_fulltext_status(item)
            lines.append(f"[{i}] {item.title}")
            lines.append(
                f"    engine={item.engine} | doi={item.doi or '-'} | year={item.year or '-'} | authors={item.authors or '-'}"
            )
            lines.append(f"    venue={item.venue or '-'}")
            if full_status:
                lines.append(f"    elsevier_fulltext={full_status}")
            if item.abstract:
                lines.append(f"    abstract={'yes' if item.abstract else 'no'}")
        print(self._panel(f"SEARCH RESULTS for '{arg}'", lines, color=ANSI.BRIGHT_GREEN))

    @staticmethod
    def _abstract_quality_score(rec: UnifiedRecord) -> int:
        text = (rec.abstract or "").strip()
        if not text:
            return -10_000
        score = len(text)
        low = text.lower()
        if "https://api.elsevier.com/content/abstract/scopus_id/" in low:
            score -= 2000
        if "scopus_id:" in low and low.count("http") > 0:
            score -= 1200
        if text.count("  ") > 5:
            score -= 200
        if rec.engine in {"crossref", "openalex", "springer-oa", "semanticscholar"}:
            score += 250
        if rec.engine == "elsevier":
            score -= 100
        return score

    def cmd_open(self, arg: str) -> None:
        if not arg:
            print(self._retro("Usage: /open <index>", ANSI.YELLOW))
            return
        try:
            _, label, rec = self._resolve_result_selector(arg)
        except ValueError as err:
            print(self._retro(str(err), ANSI.YELLOW))
            return
        if not rec:
            print(self._retro("Selected item could not be resolved to a paper preview.", ANSI.YELLOW))
            return
        item = rec
        abstract_status = "yes" if item.abstract else "no"
        full_status = self._record_fulltext_status(item)
        lines = [
            f"Title: {item.title}",
            f"Engine: {item.engine}",
            f"DOI: {item.doi or '-'}",
            f"Year: {item.year or '-'}",
            f"Authors: {item.authors or '-'}",
            f"Venue: {item.venue or '-'}",
            "Access: "
            + self._availability_badge("ABSTRACT", abstract_status)
            + " | "
            + self._availability_badge("FULLTEXT", full_status),
            f"URL: {item.url or '-'}",
            f"PDF: {item.pdf_url or '-'}",
            f"Abstract: {(item.abstract[:1600] + '...') if item.abstract and len(item.abstract) > 1600 else (item.abstract or '(none)')}",
        ]
        print(self._panel(f"RESULT :: {label}", lines, color=ANSI.BRIGHT_CYAN))
        if self.pick_sticky:
            self._wait_for_keypress("Press any key to return to the picker")

    def cmd_pick(self, arg: str) -> None:
        mode = (arg or "").strip().lower()
        if mode == "article":
            mode = "text"
        if mode not in {"", "open", "abstract", "text", "refs", "papers", "ref", "author"}:
            print(self._retro("Usage: /pick [open|abstract|text|refs|papers|ref|author]", ANSI.YELLOW))
            return
        self.pick_sticky = True
        self.pick_nav_stack = []
        if not self.pick_path:
            self._set_picker_root(self.last_list_kind or "list")
        self.pick_root_state = self._snapshot_view(0, mode)
        self.pick_status_message = ""
        self.pick_selected_index = 0
        self.pick_current_action = mode
        self.pick_exit_reason = ""
        next_mode = mode
        while True:
            picked = self._pick_current_item(next_mode)
            if picked is None:
                self.pick_sticky = False
                self.pick_nav_stack = []
                self.pick_root_state = None
                self.pick_status_message = ""
                self.pick_selected_index = 0
                self.pick_current_action = ""
                exit_reason = self.pick_exit_reason
                self.pick_exit_reason = ""
                if exit_reason == "cancel":
                    print(self._retro("Picker cancelled.", ANSI.BRIGHT_BLACK))
                return
            mode, idx = picked
            self.pick_selected_index = idx
            self.pick_current_action = mode
            selected = str(idx + 1)
            current_kind = self.last_list_kind
            if current_kind == "references":
                if mode == "open":
                    self.cmd_open(selected)
                elif mode == "abstract":
                    self.cmd_abstract(selected)
                elif mode == "text":
                    self.cmd_article(selected)
                elif mode == "refs":
                    ok = self._navigate_to_reference_refs(idx, action=mode)
                    if not ok and not self.pick_status_message:
                        self.pick_status_message = "References not available for the selected reference"
                    if self.last_list_kind == "references" and self.pick_status_message:
                        continue
                elif mode == "author":
                    preview = self._reference_preview_from_entry(self.last_references[idx])
                    if not preview:
                        self.pick_status_message = "Reference could not be resolved to a paper preview"
                        continue
                    with self._progress("Fetching related author papers..."):
                        self._navigate_to_author_related_from_record(preview, idx, action=mode)
                    if self.pick_status_message.startswith("No ") or self.pick_status_message.endswith("author list"):
                        continue
                elif mode == "ref":
                    self.cmd_open(selected)
                next_actions = self._current_picker_actions()
                next_mode = mode if mode in next_actions else ""
                continue
            if mode == "open":
                self.cmd_open(selected)
            elif mode == "abstract":
                self.cmd_abstract(selected)
            elif mode == "text":
                self.cmd_article(selected)
            elif mode == "refs":
                ok = self._navigate_to_refs(idx, action=mode)
                if not ok and not self.pick_status_message:
                    self.pick_status_message = "References not available for the selected paper"
                if self.last_list_kind == "papers" and self.pick_status_message:
                    continue
            elif mode == "papers":
                with self._progress("Fetching author papers..."):
                    self._navigate_to_author_papers(idx, action=mode)
                if self.last_list_kind == "authors" and self.pick_status_message:
                    continue
            elif mode == "ref":
                with self._progress("Resolving selected reference..."):
                    self._navigate_to_ref(idx, action=mode)
                if self.last_list_kind == "references" and self.pick_status_message:
                    continue
            elif mode == "author":
                with self._progress("Fetching related author papers..."):
                    self._navigate_to_author_related_papers(idx, action=mode)
                if self.pick_status_message.startswith("No ") or self.pick_status_message.endswith("author list"):
                    continue
            if not self.pick_sticky:
                self.pick_nav_stack = []
                self.pick_root_state = None
                self.pick_status_message = ""
                self.pick_selected_index = 0
                self.pick_current_action = ""
                return
            next_actions = self._current_picker_actions()
            next_mode = mode if mode in next_actions else ""

    def cmd_author(self, arg: str) -> None:
        if not arg:
            print(self._retro("Usage: /author <name>", ANSI.YELLOW))
            return
        if "openalex" not in self.enabled_engines:
            print(self._retro("OpenAlex engine is disabled. Run /config add openalex or /config reset.", ANSI.YELLOW))
            return
        with self._progress("Resolving author profiles..."):
            candidates = self._openalex_author_candidates(arg, per_page=12)
        self.last_author_candidates = candidates
        self.last_list_kind = "authors"
        self._set_picker_root("author", arg)
        if not candidates:
            print(self._retro("No author records found.", ANSI.YELLOW))
            return

        lines = [f"Input: {arg}"]
        for i, cand in enumerate(candidates[:10], start=1):
            lines.append(f"[{i}] {cand['display_name']}")
            lines.append(
                f"    author_id={cand['author_id']} | works={cand['works_count']} | cites={cand['cited_by_count']} | orcid={cand['orcid'] or '-'}"
            )
            lines.append(f"    affiliation={cand['affiliation'] or '-'} | score={cand['score']}")
        lines.append("Tip: after /author <name>, run /papers 1 (or another index) for the exact profile.")
        print(self._panel("AUTHOR SEARCH RESULTS", lines, color=ANSI.BRIGHT_YELLOW))

    def cmd_papers(self, arg: str) -> None:
        if not arg:
            print(self._retro("Usage: /papers <author name|index>", ANSI.YELLOW))
            return
        if "openalex" not in self.enabled_engines:
            print(self._retro("OpenAlex engine is disabled. Run /config add openalex or /config reset.", ANSI.YELLOW))
            return
        selector, filters = self._split_selector_filters(arg)
        if not selector:
            print(
                self._retro(
                    "Usage: /papers <author name|index> [year>=YYYY] [has:abstract] [has:fulltext] [venue:word]",
                    ANSI.YELLOW,
                )
            )
            return
        best: Optional[Dict[str, Any]] = None
        if selector.isdigit() and self.last_author_candidates:
            idx = int(selector) - 1
            if idx < 0 or idx >= len(self.last_author_candidates):
                print(self._retro("Author index out of range. Run /author <name> first.", ANSI.YELLOW))
                return
            best = self.last_author_candidates[idx]
        else:
            with self._progress("Resolving author profile..."):
                candidates = self._openalex_author_candidates(selector, per_page=10)
            self.last_author_candidates = candidates
            if not candidates:
                print(self._retro("Could not resolve author profile.", ANSI.YELLOW))
                return
            best = candidates[0]

        with self._progress("Fetching papers..."):
            works = self._openalex_works_for_author(best["author_id"], max_results=120)
        works = self._dedupe_records(works)
        with self._progress("Checking Elsevier full-text availability..."):
            self._prefetch_fulltext_status(works, limit=min(25, len(works)))
        works.sort(
            key=lambda r: (
                self._fulltext_rank(r),
                0 if r.abstract else 1,
                -self._record_year_int(r),
                (r.title or "").lower(),
            )
        )
        if filters:
            works = self._apply_paper_filters(works, filters)
        self.last_unified_results = works
        self.last_list_kind = "papers"
        self._set_picker_root("papers", best["display_name"])
        if not works:
            if filters:
                print(self._retro("No papers match current filters.", ANSI.YELLOW))
            else:
                print(self._retro("No papers found for selected author.", ANSI.YELLOW))
            return
        lines = [
            f"Resolved author: {best['display_name']} ({best['author_id']})",
            f"Affiliation: {best['affiliation'] or '-'} | works_count(OpenAlex): {best['works_count']}",
            f"Deduplicated papers shown: {len(works)}",
        ]
        if filters:
            lines.append(f"Applied filters: {' '.join(filters)}")
        for i, item in enumerate(works[:30], start=1):
            full_status = self._record_fulltext_status(item)
            abstract_status = "yes" if item.abstract else "no"
            lines.append(f"[{i}] {item.title}")
            lines.append(f"    doi={item.doi or '-'} | year={item.year or '-'} | venue={item.venue or '-'}")
            lines.append(
                "    access: "
                + self._availability_badge("ABSTRACT", abstract_status)
                + " | "
                + self._availability_badge("FULLTEXT", full_status)
            )
        if len(works) > 30:
            lines.append(f"... {len(works) - 30} more papers available via /open <index>")
        print(self._panel(f"PAPERS RESULTS for '{arg}'", lines, color=ANSI.BRIGHT_GREEN))

    def cmd_doi(self, arg: str) -> None:
        if not arg:
            print(self._retro("Usage: /doi <doi>", ANSI.YELLOW))
            return
        try:
            with self._progress("Resolving DOI across engines..."):
                rows = self._parallel_doi_lookup(arg)
            entitlement = self._elsevier_article_entitlement(arg)
            self.last_unified_results = rows
            self.last_list_kind = "papers"
            self._set_picker_root("doi", arg)
            if not rows:
                print(self._retro("No DOI records found across engines.", ANSI.YELLOW))
                return
            lines = [f"DOI: {arg}", f"Engines hit: {', '.join(r.engine for r in rows)}"]
            lines.append("Access: " + self._availability_badge("FULLTEXT", entitlement.lower() if entitlement else "unknown"))
            for i, r in enumerate(rows, start=1):
                abstract_status = "yes" if r.abstract else "no"
                full_status = "n/a"
                if (r.doi or "").strip().lower() == arg.strip().lower():
                    full_status = self._record_fulltext_status(r)
                lines.append(f"[{i}] {r.engine} | {r.title}")
                lines.append(f"    year={r.year or '-'} | venue={r.venue or '-'} | doi={r.doi or '-'}")
                lines.append(
                    "    access: "
                    + self._availability_badge("ABSTRACT", abstract_status)
                    + " | "
                    + self._availability_badge("FULLTEXT", full_status)
                )
                lines.append(f"    url={r.url or '-'}")
            print(self._panel("DOI LOOKUP", lines, color=ANSI.BRIGHT_CYAN))
        except ElsevierAPIError as err:
            print(self._retro(f"Error retrieving DOI {arg}: {err}", ANSI.RED))

    def cmd_abstract(self, arg: str) -> bool:
        if not arg:
            print(self._retro("Usage: /abstract <doi|index>", ANSI.YELLOW))
            return False
        try:
            if arg.isdigit():
                idx = int(arg) - 1
                if idx < 0 or idx >= len(self.last_unified_results):
                    print(self._retro("Index out of range for last results.", ANSI.YELLOW))
                    self.pick_status_message = "Abstract not available: index out of range"
                    return False
                rec = self.last_unified_results[idx]
                candidates = [rec]
                if not rec.abstract and rec.doi:
                    with self._progress("Fetching abstract candidates..."):
                        candidates = self._parallel_doi_lookup(rec.doi)
                with_abs = [r for r in candidates if r.abstract]
                if not with_abs:
                    print(self._retro("No abstract text found for selected paper.", ANSI.YELLOW))
                    self.pick_status_message = "Abstract not available for selected paper"
                    return False
                best = max(with_abs, key=self._abstract_quality_score)
                title = rec.title
            else:
                with self._progress("Fetching abstract across engines..."):
                    rows = self._parallel_doi_lookup(arg)
                with_abs = [r for r in rows if r.abstract]
                if not with_abs:
                    print(self._retro("No abstract text found across engines for this DOI.", ANSI.YELLOW))
                    self.pick_status_message = "Abstract not available for this DOI"
                    return False
                best = max(with_abs, key=self._abstract_quality_score)
                title = arg
            self._interactive_pager(
                f"ABSTRACT VIEW :: {title} ({best.engine})",
                best.abstract[:20000],
                color=ANSI.BRIGHT_MAGENTA,
            )
            self.pick_status_message = ""
            return True
        except ValueError as err:
            print(self._retro(str(err), ANSI.YELLOW))
            self.pick_status_message = str(err)
            return False
        except ElsevierAPIError as err:
            print(self._retro(f"Error: {err}", ANSI.RED))
            self.pick_status_message = f"Abstract error: {err}"
            return False

    def cmd_article(self, arg: str) -> bool:
        if not arg:
            print(self._retro("Usage: /article <doi|index>", ANSI.YELLOW))
            return False
        try:
            doi, label, rec = self._resolve_result_selector(arg)
            if not doi and not (rec and rec.engine == "arxiv"):
                print(self._retro("Selected paper has no DOI. Full-text retrieval currently needs a DOI or arXiv source.", ANSI.YELLOW))
                self.pick_status_message = "Full text not available: missing DOI or arXiv source"
                return False
            with self._progress("Fetching article text and metadata..."):
                resolved = self._resolve_fulltext(doi, seed_record=rec)
            rows = resolved.get("results") or []
            if not rows and not resolved.get("found"):
                print(self._retro("No article metadata found for DOI.", ANSI.YELLOW))
                self.pick_status_message = "Full text not available: no article metadata found"
                return False
            lines = [f"DOI: {doi or '-'}"]
            if resolved.get("title"):
                lines.append(f"Title: {resolved['title']}")
            lines.append(f"Source: {resolved.get('source') or '-'}")
            lines.append(f"Elsevier full-text status: {resolved.get('elsevier_fulltext_status') or 'unknown'}")
            if resolved.get("found") and resolved.get("text"):
                self._interactive_pager(
                    f"ARTICLE VIEW :: {resolved.get('title') or label} ({resolved.get('engine') or 'text'})",
                    str(resolved["text"])[:120000],
                    color=ANSI.BRIGHT_GREEN,
                )
                self.pick_status_message = ""
                return True
            if rec and rec.title:
                lines.append(f"Selected result: {rec.title}")
            lines.append("No machine-readable full text was recovered from configured engines.")
            lines.append("Open/read links discovered:")
            for r in rows:
                if r.url or r.pdf_url:
                    lines.append(f"- {r.engine}: {r.url or '-'}")
                    if r.pdf_url:
                        lines.append(f"  pdf: {r.pdf_url}")
            if len(lines) <= 4:
                lines.append("No direct full-text link returned by current engines.")
            print(self._panel(f"ARTICLE VIEW :: {label}", lines, color=ANSI.BRIGHT_GREEN))
            self.pick_status_message = "Full text not available for selected paper"
            return False
        except ValueError as err:
            print(self._retro(str(err), ANSI.YELLOW))
            self.pick_status_message = str(err)
            return False
        except ElsevierAPIError as err:
            print(self._retro(f"Error: {err}", ANSI.RED))
            self.pick_status_message = f"Full text error: {err}"
            return False

    def cmd_refs(self, arg: str) -> None:
        if not arg:
            print(self._retro("Usage: /refs <doi|index>", ANSI.YELLOW))
            return
        try:
            doi, label, rec = self._resolve_result_selector(arg)
            if not doi:
                print(self._retro("Selected paper has no DOI. Reference retrieval currently requires a DOI.", ANSI.YELLOW))
                return
            if sys.stdin.isatty() and sys.stdout.isatty():
                print(self._retro("Fetching references metadata...", ANSI.BRIGHT_BLACK))
            resolved = self._resolve_references(doi, seed_record=rec)
            ref_rows = resolved.get("references") or []
            self.last_references, interrupted = self._enrich_references_for_picker(ref_rows)
            self.last_list_kind = "references"
            self._set_picker_root("refs", label)
            if not self.last_references:
                print(self._retro("No references found from current engines for this DOI.", ANSI.YELLOW))
                return
            lines = [
                f"DOI: {doi}",
                f"Reference source: {resolved.get('source') or '-'}",
                f"References found: {len(self.last_references)}",
            ]
            for ref in self.last_references[:30]:
                title = ref.get("preview_title") or ref.get("text") or "reference"
                doi_view = ref.get("preview_doi") or ref.get("doi") or "-"
                abstract_status = ref.get("abstract_status") or "unknown"
                full_status = ref.get("fulltext_status") or "unknown"
                lines.append(f"[{ref['index']}] {title}")
                lines.append(
                    "    access: "
                    + self._availability_badge("ABSTRACT", abstract_status)
                    + " | "
                    + self._availability_badge("FULLTEXT", full_status)
                )
                lines.append(
                    f"    year={ref.get('preview_year') or '-'} | engine={ref.get('preview_engine') or '-'} | doi={doi_view}"
                )
            if len(self.last_references) > 30:
                lines.append(f"... {len(self.last_references) - 30} more references available via /ref <index>")
            if interrupted:
                lines.append("Reference previews were interrupted; showing partial preview metadata.")
            lines.append("Tip: references now behave like papers. Use /open, /abstract, /article, or /refs on their index.")
            print(self._panel(f"REFERENCES :: {label}", lines, color=ANSI.BRIGHT_CYAN))
        except ValueError as err:
            print(self._retro(str(err), ANSI.YELLOW))

    def cmd_ref(self, arg: str) -> None:
        if not arg or not arg.isdigit():
            print(self._retro("Usage: /ref <index>", ANSI.YELLOW))
            return
        idx = int(arg) - 1
        if idx < 0 or idx >= len(self.last_references):
            print(self._retro("Reference index out of range. Run /refs first.", ANSI.YELLOW))
            return
        ref = self.last_references[idx]
        doi = (ref.get("doi") or "").strip()
        if doi:
            self.cmd_doi(doi)
            return
        text = (ref.get("text") or "").strip()
        if not text:
            print(self._retro("Selected reference is empty.", ANSI.YELLOW))
            return
        self.cmd_search(text[:220])

    @staticmethod
    def _split_authors(authors: str) -> List[str]:
        return [a.strip() for a in re.split(r",\s*", authors or "") if a.strip()]

    def _citation_text(self, rec: UnifiedRecord, style: str = "harvard") -> str:
        authors = self._split_authors(rec.authors)
        year = rec.year or "n.d."
        title = rec.title or "(untitled)"
        venue = rec.venue or ""
        doi = rec.doi or ""
        url = rec.url or ""
        if style == "ieee":
            author_text = ", ".join(authors) if authors else "Unknown author"
            tail = f" {venue}." if venue else ""
            if doi:
                tail += f" doi:{doi}."
            elif url:
                tail += f" {url}"
            return f"{author_text}, \"{title},\" {tail} {year}."
        if style == "apa":
            author_text = ", ".join(authors) if authors else "Unknown author"
            tail = f" {venue}." if venue else ""
            if doi:
                tail += f" https://doi.org/{doi}"
            elif url:
                tail += f" {url}"
            return f"{author_text} ({year}). {title}.{tail}"
        author_text = ", ".join(authors) if authors else "Unknown author"
        tail = f", {venue}" if venue else ""
        if doi:
            tail += f", doi:{doi}"
        elif url:
            tail += f", {url}"
        return f"{author_text} ({year}) {title}{tail}."

    def _bibtex_entry(self, rec: UnifiedRecord, idx: int) -> str:
        authors = " and ".join(self._split_authors(rec.authors)) or "Unknown"
        year = rec.year or "0000"
        base = re.sub(r"[^A-Za-z0-9]+", "", (self._split_authors(rec.authors)[0].split()[-1] if self._split_authors(rec.authors) else "item"))
        key = f"{base}{year}{idx}"
        fields = {
            "title": rec.title or "(untitled)",
            "author": authors,
            "year": year,
            "journal": rec.venue or "",
            "doi": rec.doi or "",
            "url": rec.url or "",
        }
        body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields.items() if v)
        return f"@article{{{key},\n{body}\n}}"

    def _prompt_export_choice(self) -> Tuple[str, str, str, bool]:
        fmt = input(self._retro("Export format [bib/citations/abstracts/fulltext] (default bib): ", ANSI.BRIGHT_CYAN)).strip().lower() or "bib"
        style = "harvard"
        if fmt == "text":
            fmt = "citations"
        if fmt == "citations":
            style = input(self._retro("Citation style [harvard/apa/ieee] (default harvard): ", ANSI.BRIGHT_CYAN)).strip().lower() or "harvard"
            if style not in {"harvard", "apa", "ieee"}:
                style = "harvard"
        include_refs = False
        if fmt == "fulltext":
            answer = input(self._retro("Include references in fulltext export? [y/N]: ", ANSI.BRIGHT_CYAN)).strip().lower()
            include_refs = answer in {"y", "yes", "1", "true"}
        path = input(self._retro("Output path: ", ANSI.BRIGHT_CYAN)).strip()
        return fmt, style, path, include_refs

    def cmd_export(self, arg: str) -> None:
        if not self.saved_records:
            print(self._retro("No saved papers. In picker, press S on papers you want to export.", ANSI.YELLOW))
            return
        parts = [p for p in re.split(r"\s+", arg.strip()) if p]
        include_refs = False
        if not parts:
            fmt, style, path, include_refs = self._prompt_export_choice()
        else:
            fmt = parts[0].lower()
            if fmt == "text":
                fmt = "citations"
            style = (parts[1].lower() if len(parts) > 1 else "harvard")
            path = parts[2] if len(parts) > 2 else ""
            if fmt == "fulltext":
                flag = (parts[3].lower() if len(parts) > 3 else "")
                include_refs = flag in {"refs", "withrefs", "yes", "y", "true", "1"}
            if not path:
                _, _, path, include_refs_prompt = self._prompt_export_choice()
                include_refs = include_refs or include_refs_prompt
        if not path:
            print(self._retro("Export cancelled: no output path provided.", ANSI.YELLOW))
            return
        if fmt == "bib":
            content = "\n\n".join(self._bibtex_entry(rec, i) for i, rec in enumerate(self.saved_records, start=1)) + "\n"
        elif fmt == "citations":
            if style not in {"harvard", "apa", "ieee"}:
                style = "harvard"
            content = "\n".join(self._citation_text(rec, style=style) for rec in self.saved_records) + "\n"
        elif fmt == "abstracts":
            blocks: List[str] = []
            for i, rec in enumerate(self.saved_records, start=1):
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
                    f"FullTextStatus: {self._record_fulltext_status(rec).upper()}",
                    "",
                    "ABSTRACT",
                    "-" * 80,
                    (rec.abstract or "(none)"),
                    "",
                ]
                blocks.append("\n".join(sections))
            content = "\n".join(blocks)
        elif fmt == "fulltext":
            blocks: List[str] = []
            for i, rec in enumerate(self.saved_records, start=1):
                resolved = self._resolve_fulltext(rec.doi, seed_record=rec)
                refs = self._resolve_references(rec.doi, seed_record=rec) if include_refs and rec.doi else {"references": []}
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
                    f"FullTextStatus: {self._record_fulltext_status(rec).upper()}",
                    "",
                    "ABSTRACT",
                    "-" * 80,
                    (rec.abstract or "(none)"),
                    "",
                ]
                if resolved.get("found") and resolved.get("text"):
                    sections.extend(
                        [
                            "FULL TEXT",
                            "-" * 80,
                            str(resolved.get("text") or ""),
                            "",
                        ]
                    )
                else:
                    sections.extend(
                        [
                            "FULL TEXT",
                            "-" * 80,
                            "(not available)",
                            "",
                        ]
                    )
                if include_refs:
                    ref_items = refs.get("references") or []
                    sections.extend(["REFERENCES", "-" * 80])
                    if ref_items:
                        for ref in ref_items:
                            line = ref.get("text") or ""
                            if ref.get("doi"):
                                line += f" | doi={ref['doi']}"
                            sections.append(line)
                    else:
                        sections.append("(none)")
                    sections.append("")
                blocks.append("\n".join(sections))
            content = "\n".join(blocks)
        else:
            print(self._retro("Unsupported export format. Use bib, citations, abstracts, or fulltext.", ANSI.YELLOW))
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        print(self._retro(f"Exported {len(self.saved_records)} papers to {path}", ANSI.BRIGHT_CYAN))

    def cmd_import(self, arg: str) -> None:
        path = arg.strip() or input(self._retro("BibTeX file path: ", ANSI.BRIGHT_CYAN)).strip()
        if not path:
            print(self._retro("Import cancelled: no file path provided.", ANSI.YELLOW))
            return
        try:
            text = open(path, "r", encoding="utf-8").read()
        except Exception as err:
            print(self._retro(f"Cannot read file: {err}", ANSI.RED))
            return
        entries = re.findall(r"@\w+\s*\{.*?\n\}", text, flags=re.DOTALL)
        imported: List[UnifiedRecord] = []
        for raw in entries:
            fields = {}
            for key, value in re.findall(r"(\w+)\s*=\s*\{(.*?)\}", raw, flags=re.DOTALL):
                fields[key.lower()] = re.sub(r"\s+", " ", value).strip()
            authors = (fields.get("author", "") or "").replace(" and ", ", ")
            imported.append(
                UnifiedRecord(
                    engine="imported",
                    title=fields.get("title", "(untitled)"),
                    doi=fields.get("doi", ""),
                    year=fields.get("year", ""),
                    authors=authors,
                    venue=fields.get("journal", "") or fields.get("booktitle", ""),
                    abstract="",
                    url=fields.get("url", ""),
                    pdf_url="",
                    raw_id=fields.get("doi", "") or fields.get("title", ""),
                )
            )
        imported = self._dedupe_records([r for r in imported if r.title])
        self.last_unified_results = imported
        self.last_list_kind = "papers"
        self._set_picker_root("import", os.path.basename(path))
        if not imported:
            print(self._retro("No importable BibTeX entries found.", ANSI.YELLOW))
            return
        print(self._retro(f"Imported {len(imported)} papers from {path}. Press Tab to browse them.", ANSI.BRIGHT_CYAN))

    def cmd_saved(self, arg: str) -> None:
        if not self.saved_records:
            print(self._retro("No saved papers yet. In picker, press S on papers you want to keep.", ANSI.YELLOW))
            return
        self.last_unified_results = list(self.saved_records)
        self.last_list_kind = "saved"
        self._set_picker_root("saved", f"{len(self.saved_records)} papers")
        lines = [f"Saved papers: {len(self.saved_records)}"]
        for i, item in enumerate(self.saved_records[:30], start=1):
            full_status = self._record_fulltext_status(item)
            abstract_status = "yes" if item.abstract else "no"
            lines.append(f"[{i}] {item.title}")
            lines.append(f"    doi={item.doi or '-'} | year={item.year or '-'} | venue={item.venue or '-'}")
            lines.append(
                "    access: "
                + self._availability_badge("ABSTRACT", abstract_status)
                + " | "
                + self._availability_badge("FULLTEXT", full_status)
            )
        if len(self.saved_records) > 30:
            lines.append(f"... {len(self.saved_records) - 30} more saved papers available in picker.")
        lines.append("Tip: press Tab to browse saved papers, read text, inspect refs, or export them.")
        print(self._panel("SAVED PAPERS", lines, color=ANSI.BRIGHT_MAGENTA))

    def run(self) -> None:
        self.print_welcome()
        while True:
            try:
                raw = self._read_input_line().strip()
            except (EOFError, KeyboardInterrupt):
                restore_terminal_state()
                print("\n" + self._retro("Session closed.", ANSI.BRIGHT_BLACK))
                return

            if not raw:
                continue
            if not raw.startswith("/"):
                print(self._retro("Commands must start with '/'. Try /help.", ANSI.YELLOW))
                continue

            cmd, _, arg = raw.partition(" ")
            arg = arg.strip()

            try:
                if cmd in {"/quit", "/exit"}:
                    print(self._retro("Bye.", ANSI.BRIGHT_BLACK))
                    return
                if cmd == "/help":
                    self.print_help()
                elif cmd == "/config":
                    self.cmd_config(arg)
                elif cmd == "/engines":
                    self.cmd_engines()
                elif cmd == "/search":
                    self.cmd_search(arg)
                elif cmd == "/author":
                    self.cmd_author(arg)
                elif cmd == "/papers":
                    self.cmd_papers(arg)
                elif cmd == "/doi":
                    self.cmd_doi(arg)
                elif cmd == "/abstract":
                    self.cmd_abstract(arg)
                elif cmd == "/article":
                    self.cmd_article(arg)
                elif cmd == "/refs":
                    self.cmd_refs(arg)
                elif cmd == "/ref":
                    self.cmd_ref(arg)
                elif cmd == "/export":
                    self.cmd_export(arg)
                elif cmd == "/import":
                    self.cmd_import(arg)
                elif cmd == "/saved":
                    self.cmd_saved(arg)
                elif cmd == "/pick":
                    self.cmd_pick(arg)
                elif cmd == "/open":
                    self.cmd_open(arg)
                elif cmd == "/clear":
                    # ANSI clear + home
                    print("\033[2J\033[H", end="")
                    self.print_welcome()
                else:
                    print(self._retro(f"Unknown command: {cmd}. Try /help.", ANSI.YELLOW))

                if (
                    self.auto_pick_after_list
                    and cmd in {"/search", "/author", "/papers", "/doi", "/refs", "/import", "/saved"}
                    and self._has_current_browsable_list()
                ):
                    self.cmd_pick("")
            except ElsevierAPIError as err:
                print(self._retro(f"API error: {err}", ANSI.RED))


def load_credentials() -> Tuple[str, str]:
    env_file = os.getenv("SCHOLARFETCH_ENV_FILE", ".scholarfetch.env")
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
                    if key and key not in os.environ:
                        os.environ[key] = value
        except Exception:
            pass

    api_key = os.getenv("ELSEVIER_API_KEY", "").strip()
    inst_token = os.getenv("ELSEVIER_INSTTOKEN", "").strip()

    # Optional local fallback file (never committed) for convenience.
    cfg_path = os.getenv("SCHOLARFETCH_CREDENTIALS_FILE", ".scholarfetch_credentials.json")
    if (not api_key or not inst_token) and os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
            api_key = api_key or cfg.get("api_key", "").strip()
            inst_token = inst_token or cfg.get("insttoken", "").strip()
        except Exception:
            pass

    if not api_key:
        if sys.stdin.isatty():
            api_key = getpass.getpass("Elsevier API Key (hidden): ").strip()
        else:
            api_key = input("Elsevier API Key: ").strip()
    if not api_key:
        raise SystemExit("Missing credentials. Provide ELSEVIER_API_KEY.")
    return api_key, inst_token


def main() -> None:
    try:
        api_key, inst_token = load_credentials()
        cli = RetroCLI(ElsevierClient(api_key, inst_token))
        cli.run()
    except KeyboardInterrupt:
        restore_terminal_state()
        print("\nInterrupted.")


if __name__ == "__main__":
    main()
