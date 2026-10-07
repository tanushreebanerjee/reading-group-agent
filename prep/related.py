"""Fetch titles and abstracts of key references and citing papers.

Primary: Semantic Scholar Graph API (no key; rate limited, so we back off).
Fallback: parse the paper's bibliography, rank references by how often they
are cited in the text, and look each one up on arXiv by title.
"""
from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import requests

S2 = "https://api.semanticscholar.org/graph/v1"
ARXIV = "https://export.arxiv.org/api/query"
ATOM = {"a": "http://www.w3.org/2005/Atom"}
UA = {"User-Agent": "reading-group-assistant/0.1 (local research tool)"}


def _get(url: str, params: dict, tries: int = 5, wait: float = 3.0) -> requests.Response | None:
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30)
        except requests.RequestException:
            r = None
        if r is not None and r.status_code == 200:
            return r
        if r is not None and r.status_code not in (429, 500, 502, 503, 504):
            return None
        time.sleep(wait * (2 ** i))
    return None


def _trim(abstract: str | None, n: int = 600) -> str:
    a = re.sub(r"\s+", " ", abstract or "").strip()
    return a[:n] + ("..." if len(a) > n else "")


def s2_related(title: str, max_refs: int, max_citing: int) -> dict | None:
    r = _get(f"{S2}/paper/search/match", {"query": title, "fields": "paperId,title,year"})
    if not r or not r.json().get("data"):
        return None
    pid = r.json()["data"][0]["paperId"]
    fields = "title,abstract,year,citationCount,isInfluential"
    refs, cites = [], []
    time.sleep(1.1)
    r = _get(f"{S2}/paper/{pid}/references", {"fields": fields, "limit": 200})
    if r:
        for item in r.json().get("data", []):
            p = item.get("citedPaper") or {}
            if p.get("title"):
                refs.append({"title": p["title"], "year": p.get("year"), "abstract": _trim(p.get("abstract")),
                             "citations": p.get("citationCount") or 0,
                             "influential": bool(item.get("isInfluential"))})
    time.sleep(1.1)
    r = _get(f"{S2}/paper/{pid}/citations", {"fields": "title,abstract,year,citationCount", "limit": 200})
    if r:
        for item in r.json().get("data", []):
            p = item.get("citingPaper") or {}
            if p.get("title"):
                cites.append({"title": p["title"], "year": p.get("year"), "abstract": _trim(p.get("abstract")),
                              "citations": p.get("citationCount") or 0})
    refs.sort(key=lambda p: (p["influential"], p["citations"]), reverse=True)
    cites.sort(key=lambda p: p["citations"], reverse=True)
    return {"source": "semantic_scholar", "paper_id": pid,
            "references": refs[:max_refs], "citing": cites[:max_citing]}


# ---------- arXiv fallback ----------

def parse_bibliography(full_text: str) -> dict[int, str]:
    """Return {ref number: title} from a numbered bibliography ("12. Authors: Title. In: ...")."""
    idx = full_text.rfind("References")
    if idx < 0:
        return {}
    bib = full_text[idx:]
    entries = re.split(r"\n(?=\d{1,3}\.\s)", bib)
    out = {}
    for e in entries:
        m = re.match(r"(\d{1,3})\.\s+(.*)", e, re.S)
        if not m:
            continue
        num, body = int(m.group(1)), re.sub(r"-\n", "", m.group(2)).replace("\n", " ")
        # LNCS style: "Authors: Title. In: Venue" ; some entries have no authors
        parts = body.split(": ", 1)
        rest = parts[1] if len(parts) == 2 and len(parts[0]) < 400 else body
        title = re.split(r"\.\s+(?:In:|arXiv|Advances|Proceedings|\(|https?:)|\.\s*$", rest, 1)[0]
        title = title.strip(" .")
        if 10 < len(title) < 250:
            out[num] = title
    return out


def cited_counts(full_text: str) -> Counter:
    body = full_text[: full_text.rfind("References")] if "References" in full_text else full_text
    c = Counter()
    for grp in re.findall(r"\[(\d{1,3}(?:\s*[,–-]\s*\d{1,3})*)\]", body):
        for part in re.split(r"\s*,\s*", grp):
            if re.match(r"^\d+\s*[–-]\s*\d+$", part):
                a, b = map(int, re.split(r"\s*[–-]\s*", part))
                c.update(range(a, b + 1))
            elif part.strip().isdigit():
                c[int(part)] += 1
    return c


def arxiv_lookup(title: str) -> dict | None:
    q = re.sub(r"[^\w\s-]", " ", title)
    r = _get(ARXIV, {"search_query": f'ti:"{q}"', "max_results": 1}, tries=3)
    if not r:
        return None
    root = ET.fromstring(r.text)
    entry = root.find("a:entry", ATOM)
    if entry is None:
        return None
    return {"title": re.sub(r"\s+", " ", entry.findtext("a:title", "", ATOM)),
            "year": int(entry.findtext("a:published", "0000", ATOM)[:4]),
            "abstract": _trim(entry.findtext("a:summary", "", ATOM))}


def arxiv_related(full_text: str, max_refs: int) -> dict:
    titles = parse_bibliography(full_text)
    counts = cited_counts(full_text)
    ranked = [n for n, _ in counts.most_common() if n in titles][:max_refs]
    refs = []
    for n in ranked:
        hit = arxiv_lookup(titles[n])
        refs.append({"ref": n, "title": titles[n], "in_text_citations": counts[n],
                     "abstract": hit["abstract"] if hit else "", "year": hit["year"] if hit else None})
        time.sleep(3)  # arXiv asks for 1 request / 3 s
    return {"source": "arxiv_fallback", "references": refs, "citing": []}


def fetch_related(title: str, full_text: str, cache_path: Path, max_refs: int = 8, max_citing: int = 8) -> dict:
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    data = s2_related(title, max_refs, max_citing)
    if not data or not data.get("references"):
        data = arxiv_related(full_text, max_refs)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data, indent=1))
    return data


def format_related(data: dict) -> str:
    lines = [f"(source: {data.get('source')})", "## Key references"]
    for p in data.get("references", []):
        lines.append(f"- {p['title']} ({p.get('year') or 'n.d.'}): {p.get('abstract') or 'no abstract'}")
    lines.append("## Papers citing this one")
    if not data.get("citing"):
        lines.append("- none found")
    for p in data.get("citing", []):
        lines.append(f"- {p['title']} ({p.get('year') or 'n.d.'}): {p.get('abstract') or 'no abstract'}")
    return "\n".join(lines)
