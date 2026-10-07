"""Paper text extraction, section/caption detection, chunking, and a tiny BM25 retriever."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from pypdf import PdfReader

# "4 Method", "4.1 Overview", "5.2 Quantitative Results", "A.1 Training details"
HEADING_RE = re.compile(r"^((?:\d{1,2}|[A-E])(?:\.\d{1,2}){0,2})\s+([A-Z][A-Za-z][^\n]{1,80})$")
CAPTION_RE = re.compile(r"^(Table|Fig\.|Figure)\s*(\d+)\s*[:.]", re.I)
TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")


@dataclass
class Chunk:
    page: int          # 1-based
    section: str       # e.g. "4.3 Multi-view Diffusion and Determining the Boundary Layer"
    text: str

    @property
    def label(self) -> str:
        sec = f"§{self.section}" if self.section else "front matter"
        return f"[{sec}, p.{self.page}]"


@dataclass
class Paper:
    title: str
    pages: list[str]
    chunks: list[Chunk]

    @property
    def full_text(self) -> str:
        return "\n\n".join(f"=== Page {i + 1} ===\n{p}" for i, p in enumerate(self.pages))


def extract_pages(pdf_path: str | Path) -> list[str]:
    reader = PdfReader(str(pdf_path))
    return [(p.extract_text() or "") for p in reader.pages]


def guess_title(first_page: str) -> str:
    lines = [ln.strip() for ln in first_page.splitlines() if ln.strip()]
    # Title is usually the first 1-2 lines before the author list.
    title = []
    for ln in lines[:4]:
        if re.search(r"\d,|\d$|@|University|Institute|KAIST", ln) or len(title) >= 2:
            break
        title.append(ln)
    return " ".join(title) if title else (lines[0] if lines else "")


def _is_heading(line: str, current: str) -> bool:
    m = HEADING_RE.match(line.strip())
    if not m:
        return False
    num, rest = m.groups()
    # reject table rows / numbers followed by lots of digits
    if sum(c.isdigit() for c in rest) > 3 or len(rest.split()) > 12:
        return False
    if rest.rstrip().endswith((".", ",")):
        return False
    return True


def _is_label_soup(text: str) -> bool:
    """Figure panels extract as repeated short labels ("Source views GT Ours ..."): skip them."""
    toks = tokenize(text)
    return len(toks) >= 20 and len(set(toks)) / len(toks) < 0.35


def chunk_pages(pages: list[str], chunk_chars: int = 600, overlap: int = 200) -> list[Chunk]:
    chunks: list[Chunk] = []
    section = ""
    for pno, page in enumerate(pages, start=1):
        buf: list[str] = []
        buf_section = section
        tail = ""  # overlap so sentences cut at a chunk boundary stay retrievable

        def flush():
            nonlocal tail
            text = re.sub(r"\s+", " ", " ".join(buf)).strip()
            if len(text) > 40 and not _is_label_soup(text):
                chunks.append(Chunk(pno, buf_section, (tail + " " + text).strip()))
                tail = text[-overlap:] if overlap else ""
            buf.clear()

        for line in page.splitlines():
            if _is_heading(line, section):
                flush()
                section = re.split(r"\s+(?:Table|Fig\.|Figure)\s*\d", line.strip())[0]
                buf_section = section
                continue
            if CAPTION_RE.match(line.strip()):
                flush()  # captions start their own chunk so table/figure numbers are labelled
            buf.append(line.strip())
            if sum(len(b) for b in buf) >= chunk_chars:
                flush()
                buf_section = section
        flush()
    return chunks


def load_paper(pdf_path: str | Path, chunk_chars: int = 600, cache_dir: str | Path | None = None,
               overlap: int = 200) -> Paper:
    pdf_path = Path(pdf_path)
    cache = None
    if cache_dir:
        cache = Path(cache_dir) / f"{pdf_path.resolve().stem}.{pdf_path.stat().st_size}.pages.json"
        if cache.exists():
            pages = json.loads(cache.read_text())
        else:
            pages = extract_pages(pdf_path)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(pages))
    else:
        pages = extract_pages(pdf_path)
    return Paper(title=guess_title(pages[0] if pages else ""), pages=pages,
                 chunks=chunk_pages(pages, chunk_chars, overlap))


GLUE_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[0-9])(?=[A-Za-z])|(?<=[A-Za-z])(?=[0-9])")


def tokenize(text: str) -> list[str]:
    # pypdf often glues words ("useN= 2source"); split at case and digit/letter boundaries
    return TOKEN_RE.findall(GLUE_RE.sub(" ", text).lower())


STOP = set("the a an of to in and or is are was were be for on with by as at that this it its from "
           "what which how why does do did they their them we our you your about can could would "
           "should hey so ok okay".split())


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.docs = [[t for t in tokenize(d) if t not in STOP] for d in docs]
        self.k1, self.b = k1, b
        self.avgdl = sum(len(d) for d in self.docs) / max(1, len(self.docs))
        df = Counter()
        for d in self.docs:
            df.update(set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tfs = [Counter(d) for d in self.docs]

    def scores(self, query: str) -> list[float]:
        q = [t for t in tokenize(query) if t not in STOP]
        out = []
        for tf, d in zip(self.tfs, self.docs):
            s = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d) / self.avgdl))
            out.append(s)
        return out

    def top(self, query: str, k: int) -> list[int]:
        sc = self.scores(query)
        ranked = sorted(range(len(sc)), key=lambda i: sc[i], reverse=True)
        return [i for i in ranked[:k] if sc[i] > 0]


class Retriever:
    def __init__(self, paper: Paper):
        self.paper = paper
        self.bm25 = BM25([f"{c.section} {c.text}" for c in paper.chunks])

    def search(self, query: str, k: int = 4) -> list[Chunk]:
        return [self.paper.chunks[i] for i in self.bm25.top(query, k)]

    @staticmethod
    def format(chunks: list[Chunk]) -> str:
        return "\n\n".join(f"{c.label} {c.text}" for c in chunks)


TERM_RE = re.compile(r"\b(?:[A-Z][a-z]*[A-Z0-9][A-Za-z0-9]*|[A-Z]{2,}[a-z]?)(?:-[A-Za-z0-9]+)*\b")
TERM_SKIP = {"PDF", "ID", "OK", "II", "III", "IV", "URL", "GPU", "GPUs", "CVPR", "ICCV", "ECCV", "ICLR",
             "NeurIPS", "ICML", "arXiv", "In", "Fig", "Tab", "Sec", "Eq", "GT"}


def key_terms(paper: Paper, n: int = 25) -> list[str]:
    """Acronyms and mixed-case jargon (VAE, DL3DV, RealEstate10K), most frequent first.

    Used to prime speech recognition with the paper's vocabulary.
    """
    body = paper.full_text
    if "References" in body:
        body = body[: body.rfind("References")]
    counts = Counter(t for t in TERM_RE.findall(body) if t not in TERM_SKIP and len(t) <= 20)
    return [t for t, c in counts.most_common(n) if c >= 3]


def chunk_to_dict(c: Chunk) -> dict:
    return asdict(c)
