"""Check that every key number in a brief appears in the paper on the cited page.

python -m prep.verify briefs/test/brief.md papers/test.pdf
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass

NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass
class Row:
    number: str
    what: str
    location: str
    page: int | None
    found_on_page: bool = False
    found_anywhere: bool = False
    found_pages: tuple = ()


def parse_key_numbers(brief: str) -> list[Row]:
    """Parse the '## Key numbers' markdown table."""
    m = re.search(r"##\s*Key numbers\s*\n(.*?)(?:\n##\s|\Z)", brief, re.S | re.I)
    if not m:
        return []
    rows = []
    for line in m.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or set(cells[0]) <= set("-: ") or cells[0].lower() == "number":
            continue
        pm = re.search(r"\d+", cells[3])
        rows.append(Row(cells[0], cells[1], cells[2], int(pm.group()) if pm else None))
    return rows


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).replace("×", "x").replace(",", "")


def check_rows(rows: list[Row], pages: list[str]) -> list[Row]:
    normed = [_norm(p) for p in pages]
    for r in rows:
        nums = NUM_RE.findall(r.number.replace(",", ""))
        if not nums:
            continue
        hits = [i + 1 for i, p in enumerate(normed) if all(n in p for n in nums)]
        r.found_pages = tuple(hits)
        r.found_anywhere = bool(hits)
        r.found_on_page = r.page in hits
    return rows


def report(rows: list[Row]) -> tuple[str, bool]:
    lines, ok = [], True
    for r in rows:
        if r.found_on_page:
            status = "ok"
        elif r.found_anywhere:
            status = f"WRONG PAGE (cited p.{r.page}, found on p.{','.join(map(str, r.found_pages))})"
            ok = False
        else:
            status = "NOT FOUND in paper text"
            ok = False
        lines.append(f"{status:<40} {r.number:<14} {r.location:<14} {r.what[:60]}")
    if not rows:
        lines.append("no Key numbers table found")
        ok = False
    return "\n".join(lines), ok


def main(argv=None):
    from core.paper import extract_pages

    ap = argparse.ArgumentParser(prog="python -m prep.verify")
    ap.add_argument("brief")
    ap.add_argument("pdf")
    args = ap.parse_args(argv)
    rows = check_rows(parse_key_numbers(open(args.brief).read()), extract_pages(args.pdf))
    text, ok = report(rows)
    print(text)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
