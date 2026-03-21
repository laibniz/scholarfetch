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

import getpass
import html
import json
import os
import re
import sys
import textwrap
import tty
import termios
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


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


RETRO_BANNER = r"""
   ______ _                _
  |  ____| |              (_)
  | |__  | |___  _____   ___  ___ _ __
  |  __| | / __|/ _ \ \ / / |/ _ \ '__|
  | |____| \__ \  __/\ V /| |  __/ |
  |______|_|___/\___| \_/ |_|\___|_|

  ██████╗ ██████╗ ██╗      ██████╗██╗
 ██╔════╝██╔═══██╗██║     ██╔════╝██║
 ██║     ██║   ██║██║     ██║     ██║
 ██║     ██║   ██║██║     ██║     ██║
 ╚██████╗╚██████╔╝███████╗╚██████╗███████╗
  ╚═════╝ ╚═════╝ ╚══════╝ ╚═════╝╚══════╝
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
        raw, _ = self._request(path, accept="text/plain")
        return raw.decode("utf-8", errors="replace")

    def article_xml_by_doi(self, doi: str) -> str:
        path = f"/content/article/doi/{urllib.parse.quote(doi, safe='')}"
        raw, _ = self._request(path, accept="text/xml")
        return raw.decode("utf-8", errors="replace")


class RetroCLI:
    def __init__(self, client: ElsevierClient):
        self.client = client
        self.last_results: List[ArticleEntry] = []
        self.last_unified_results: List[UnifiedRecord] = []
        self.last_author_candidates: List[Dict[str, Any]] = []
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
            "/open",
            "/clear",
            "/help",
            "/quit",
            "/exit",
        ]
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
        return all(re.fullmatch(r"[A-Za-zÀ-ÿ'`.-]+", p) is not None for p in parts)

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
        print(self._retro(RETRO_BANNER, ANSI.BRIGHT_CYAN, bold=True))
        print(
            self._panel(
                "SCHOLARFETCH TERMINAL :: READY",
                [
                    "Type slash commands to interact with the APIs.",
                    "Examples: /search graph neural networks | /author Albert Einstein | /doi 10.1016/S0014-5793(01)03313-0",
                    "Extra: /engines | /abstract <doi> | /article <doi> | /papers <author> | /open <n> | /help | /quit",
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
            return f"{ANSI.DIM}{ANSI.BRIGHT_BLACK}  no-match (/help){ANSI.RESET}"
        shown = " ".join(matches[:6])
        if len(matches) > 6:
            shown += " ..."
        return f"{ANSI.DIM}{ANSI.BRIGHT_BLACK}  {shown}{ANSI.RESET}"

    def _read_input_line(self) -> str:
        # Non-interactive fallback for piped/scripted usage.
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return input(self._retro("retro@scholarfetch> ", ANSI.BRIGHT_GREEN, bold=True))

        prompt = "retro@scholarfetch> "
        buffer: List[str] = []
        cursor = 0
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)

        def render() -> None:
            line = "".join(buffer)
            hint = self._command_hint(line)
            sys.stdout.write("\r\033[2K" + prompt + line + hint)
            # Place caret back at the current cursor position in the editable line.
            sys.stdout.write("\r")
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
                    return "".join(buffer)
                if c == "\x03":
                    raise KeyboardInterrupt
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
                    "/papers <author|index>        Fetch deduplicated papers for selected/best author.",
                    "/doi <doi>                    Quick metadata + abstract preview by DOI.",
                    "/abstract <doi|index>         Show abstract by DOI or from last result index.",
                    "/article <doi>                Retrieve full article plain text (entitlement-dependent).",
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
        if self._looks_like_doi(arg):
            results = self._parallel_doi_lookup(arg)
        elif self._looks_like_person_name(arg) and "openalex" in self.enabled_engines:
            self.cmd_papers(arg)
            return
        else:
            results = self._parallel_search(arg, limit_per_engine=4)
        self.last_unified_results = results
        if not results:
            print(self._retro("No results found across enabled engines.", ANSI.YELLOW))
            return

        lines = [f"Unified results from: {', '.join(self.enabled_engines)}"]
        for i, item in enumerate(results, start=1):
            lines.append(f"[{i}] {item.title}")
            lines.append(
                f"    engine={item.engine} | doi={item.doi or '-'} | year={item.year or '-'} | authors={item.authors or '-'}"
            )
            lines.append(f"    venue={item.venue or '-'}")
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
        if not arg.isdigit():
            print(self._retro("Usage: /open <index>", ANSI.YELLOW))
            return
        idx = int(arg) - 1
        if idx < 0 or idx >= len(self.last_unified_results):
            print(self._retro("Index out of range for last results.", ANSI.YELLOW))
            return

        item = self.last_unified_results[idx]
        lines = [
            f"Title: {item.title}",
            f"Engine: {item.engine}",
            f"DOI: {item.doi or '-'}",
            f"Year: {item.year or '-'}",
            f"Authors: {item.authors or '-'}",
            f"Venue: {item.venue or '-'}",
            f"URL: {item.url or '-'}",
            f"PDF: {item.pdf_url or '-'}",
            f"Abstract: {(item.abstract[:1600] + '...') if item.abstract and len(item.abstract) > 1600 else (item.abstract or '(none)')}",
        ]
        print(self._panel(f"RESULT #{idx + 1}", lines, color=ANSI.BRIGHT_CYAN))

    def cmd_author(self, arg: str) -> None:
        if not arg:
            print(self._retro("Usage: /author <name>", ANSI.YELLOW))
            return
        if "openalex" not in self.enabled_engines:
            print(self._retro("OpenAlex engine is disabled. Run /config add openalex or /config reset.", ANSI.YELLOW))
            return
        candidates = self._openalex_author_candidates(arg, per_page=12)
        self.last_author_candidates = candidates
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
            print(self._retro("Usage: /papers <author name|index> [year>=YYYY] [has:abstract] [venue:word]", ANSI.YELLOW))
            return
        best: Optional[Dict[str, Any]] = None
        if selector.isdigit() and self.last_author_candidates:
            idx = int(selector) - 1
            if idx < 0 or idx >= len(self.last_author_candidates):
                print(self._retro("Author index out of range. Run /author <name> first.", ANSI.YELLOW))
                return
            best = self.last_author_candidates[idx]
        else:
            candidates = self._openalex_author_candidates(selector, per_page=10)
            self.last_author_candidates = candidates
            if not candidates:
                print(self._retro("Could not resolve author profile.", ANSI.YELLOW))
                return
            best = candidates[0]

        works = self._openalex_works_for_author(best["author_id"], max_results=120)
        works = self._dedupe_records(works)
        # Prefer papers with abstracts while keeping recent papers near the top.
        works.sort(key=lambda r: (0 if r.abstract else 1, -self._record_year_int(r), (r.title or "").lower()))
        if filters:
            works = self._apply_paper_filters(works, filters)
        self.last_unified_results = works
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
            lines.append(f"[{i}] {item.title}")
            lines.append(
                f"    doi={item.doi or '-'} | year={item.year or '-'} | venue={item.venue or '-'}"
            )
            lines.append(f"    abstract={'yes' if item.abstract else 'no'}")
        if len(works) > 30:
            lines.append(f"... {len(works) - 30} more papers available via /open <index>")
        print(self._panel(f"PAPERS RESULTS for '{arg}'", lines, color=ANSI.BRIGHT_GREEN))

    def cmd_doi(self, arg: str) -> None:
        if not arg:
            print(self._retro("Usage: /doi <doi>", ANSI.YELLOW))
            return
        try:
            rows = self._parallel_doi_lookup(arg)
            self.last_unified_results = rows
            if not rows:
                print(self._retro("No DOI records found across engines.", ANSI.YELLOW))
                return
            lines = [f"DOI: {arg}", f"Engines hit: {', '.join(r.engine for r in rows)}"]
            for i, r in enumerate(rows, start=1):
                lines.append(f"[{i}] {r.engine} | {r.title}")
                lines.append(f"    year={r.year or '-'} | venue={r.venue or '-'} | doi={r.doi or '-'}")
                lines.append(f"    abstract={'yes' if r.abstract else 'no'} | url={r.url or '-'}")
            print(self._panel("DOI LOOKUP", lines, color=ANSI.BRIGHT_CYAN))
        except ElsevierAPIError as err:
            print(self._retro(f"Error retrieving DOI {arg}: {err}", ANSI.RED))

    def cmd_abstract(self, arg: str) -> None:
        if not arg:
            print(self._retro("Usage: /abstract <doi|index>", ANSI.YELLOW))
            return
        try:
            if arg.isdigit():
                idx = int(arg) - 1
                if idx < 0 or idx >= len(self.last_unified_results):
                    print(self._retro("Index out of range for last results.", ANSI.YELLOW))
                    return
                rec = self.last_unified_results[idx]
                candidates = [rec]
                if not rec.abstract and rec.doi:
                    candidates = self._parallel_doi_lookup(rec.doi)
                with_abs = [r for r in candidates if r.abstract]
                if not with_abs:
                    print(self._retro("No abstract text found for selected paper.", ANSI.YELLOW))
                    return
                best = max(with_abs, key=self._abstract_quality_score)
                title = rec.title
            else:
                rows = self._parallel_doi_lookup(arg)
                with_abs = [r for r in rows if r.abstract]
                if not with_abs:
                    print(self._retro("No abstract text found across engines for this DOI.", ANSI.YELLOW))
                    return
                best = max(with_abs, key=self._abstract_quality_score)
                title = arg
            print(
                self._panel(
                    f"ABSTRACT VIEW :: {title} ({best.engine})",
                    [best.abstract[:7000]],
                    color=ANSI.BRIGHT_MAGENTA,
                )
            )
        except ElsevierAPIError as err:
            print(self._retro(f"Error: {err}", ANSI.RED))

    def cmd_article(self, arg: str) -> None:
        if not arg:
            print(self._retro("Usage: /article <doi>", ANSI.YELLOW))
            return
        try:
            rows = self._parallel_doi_lookup(arg)
            if not rows:
                print(self._retro("No article metadata found for DOI.", ANSI.YELLOW))
                return
            lines = [f"DOI: {arg}", "Open/read links discovered:"]
            for r in rows:
                if r.url or r.pdf_url:
                    lines.append(f"- {r.engine}: {r.url or '-'}")
                    if r.pdf_url:
                        lines.append(f"  pdf: {r.pdf_url}")
            if len(lines) <= 2:
                lines.append("No direct full-text link returned by current engines.")
            print(self._panel(f"ARTICLE VIEW :: {arg}", lines, color=ANSI.BRIGHT_GREEN))
        except ElsevierAPIError as err:
            print(self._retro(f"Error: {err}", ANSI.RED))

    def run(self) -> None:
        self.print_welcome()
        while True:
            try:
                raw = self._read_input_line().strip()
            except (EOFError, KeyboardInterrupt):
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
                elif cmd == "/open":
                    self.cmd_open(arg)
                elif cmd == "/clear":
                    # ANSI clear + home
                    print("\033[2J\033[H", end="")
                    self.print_welcome()
                else:
                    print(self._retro(f"Unknown command: {cmd}. Try /help.", ANSI.YELLOW))
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
        print("\nInterrupted.")


if __name__ == "__main__":
    main()
