"""WER of a transcript JSONL against the synthetic script (Phase 2 acceptance).

python tests/transcript_wer.py meetings/phase2_transcript.jsonl
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.transcript import read_jsonl  # noqa: E402
from tests.fixtures.make_synthetic import parse_script  # noqa: E402
from tests.stt_bench import wer, words  # noqa: E402


def main():
    meta, turns = parse_script(ROOT / "tests" / "fixtures" / "synthetic_script.md")
    ref = words(" ".join(t["text"] for t in turns))
    segs = read_jsonl(sys.argv[1])
    hyp_text = " ".join(s.text for s in segs)
    name = meta["assistant_name"].lower()
    n = hyp_text.lower().count(name)
    expected = sum(1 for e in meta["events"] if e["kind"] == "ask")
    w = wer(ref, words(hyp_text))
    print(f"segments={len(segs)}  WER={w:.1%}  '{name}' heard {n}/{expected}")
    ok = w < 0.15 and n >= expected
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
