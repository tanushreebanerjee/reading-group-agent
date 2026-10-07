"""Pre-meeting brief.

python -m prep papers/x.pdf [-o briefs/x] [--fresh]
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from core.config import load_config
from core.llm import make_llm
from core.paper import load_paper
from prep.related import fetch_related, format_related
from prep.roles import run_roles
from prep.verify import check_rows, parse_key_numbers, report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m prep")
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out-dir", default=None, help="default: briefs/<pdf stem>/")
    ap.add_argument("--config", default=None)
    ap.add_argument("--fresh", action="store_true", help="ignore cached role outputs")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    pdf = Path(args.pdf)
    out_dir = Path(args.out_dir) if args.out_dir else Path(cfg["paths"]["briefs_dir"]) / pdf.stem
    if args.fresh and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    paper = load_paper(pdf)
    print(f"[prep] paper: {paper.title!r}, {len(paper.pages)} pages")

    rel_cfg = cfg.get("related", {})
    related = fetch_related(paper.title, paper.full_text, out_dir / "related.json",
                            rel_cfg.get("max_refs", 8), rel_cfg.get("max_citing", 8))
    print(f"[prep] related work via {related['source']}: "
          f"{len(related['references'])} refs, {len(related['citing'])} citing")

    llm = make_llm(cfg["llm"]["prep"])
    brief = run_roles(cfg, llm, paper, format_related(related), out_dir)

    rows = check_rows(parse_key_numbers(brief), paper.pages)
    text, ok = report(rows)
    print("[prep] number check:\n" + text)
    if not ok:
        brief += ("\n\n> **Auto-check warning:** some key numbers could not be matched to the cited page "
                  "in the PDF text. See `verify.txt`.\n")
    (out_dir / "verify.txt").write_text(text + "\n")
    (out_dir / "brief.md").write_text(brief.strip() + "\n")
    print(f"[prep] wrote {out_dir / 'brief.md'} ({len(brief.split())} words)")


if __name__ == "__main__":
    main()
